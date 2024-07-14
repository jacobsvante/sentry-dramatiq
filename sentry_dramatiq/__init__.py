import json
from typing import Any, Dict

import sentry_sdk
from dramatiq.broker import Broker
from dramatiq.errors import Retry
from dramatiq.message import Message
from dramatiq.middleware import Middleware, default_middleware
from sentry_sdk import Scope, continue_trace
from sentry_sdk.integrations import Integration
from sentry_sdk.integrations.logging import ignore_logger
from sentry_sdk.tracing import TRANSACTION_SOURCE_TASK
from sentry_sdk.utils import (
    AnnotatedValue,
    capture_internal_exceptions,
    event_from_exception,
)


# TODO:
#  - add results storage
#  - add more readable info about retries
class DramatiqIntegration(Integration):
    """Dramatiq integration for Sentry"""

    identifier = "dramatiq"

    @staticmethod
    def setup_once() -> None:
        _patch_dramatiq_broker()


def _insert_sentry_middleware(middleware: list):
    assert SentryMiddleware not in (
        m.__class__ for m in middleware
    ), "Sentry middleware must not be passed in manually to broker"
    middleware.insert(0, SentryMiddleware())


def _patch_dramatiq_broker():
    from dramatiq.broker import global_broker
    if global_broker is not None:
        _insert_sentry_middleware(global_broker.middleware)

    original_broker__init__ = Broker.__init__

    def sentry_patched_broker__init__(self, *args, **kw):
        integration = sentry_sdk.get_client().get_integration(DramatiqIntegration)

        try:
            middleware = kw.pop("middleware")
        except KeyError:
            # Unfortunately Broker and StubBroker allows middleware to be
            # passed in as positional arguments, whilst RabbitmqBroker and
            # RedisBroker does not.
            if len(args) > 0:
                assert len(args) < 2
                middleware = None if args[0] is None else args[0]
                args = []
            else:
                middleware = None

        if middleware is None:
            middleware = list(m() for m in default_middleware)
        else:
            middleware = list(middleware)

        if integration is not None:
            _insert_sentry_middleware(middleware)

        kw["middleware"] = middleware
        # raise Exception([args, kw])
        original_broker__init__(self, *args, **kw)

    Broker.__init__ = sentry_patched_broker__init__
    # this logger logs unhandled exceptions, preventing our middleware from capturing it
    ignore_logger("dramatiq.worker.WorkerThread")


class SentryMiddleware(Middleware):
    """A Dramatiq middleware that automatically captures and sends
    exceptions to Sentry.

    This is automatically added to every instantiated broker via the
    DramatiqIntegration.
    """

    def before_enqueue(self, broker: Broker, message: Message, delay):
        if sentry_sdk.get_client().get_integration(DramatiqIntegration) is None:
            return

        if "_sentry_trace_headers" in message.options:
            # this means that message got queued due to retry or delay, so we don't want to propagate empty headers
            return

        scope = Scope.get_current_scope()
        if scope.span is not None:
            message.options["_sentry_trace_headers"] = dict(
                scope.iter_trace_propagation_headers()
            )

    def before_process_message(self, broker: Broker, message: Message):
        integration = sentry_sdk.get_client().get_integration(DramatiqIntegration)
        if integration is None:
            return

        scope_manager = sentry_sdk.new_scope()
        scope = scope_manager.__enter__()
        scope.clear_breadcrumbs()
        scope.add_event_processor(_make_message_event_processor(message, broker))

        transaction = continue_trace(
            message.options.get("_sentry_trace_headers", {}),
            op="queue.tasks.dramatiq",
            name=message.actor_name,
            source=TRANSACTION_SOURCE_TASK,
        )

        started_transaction = sentry_sdk.start_transaction(
            transaction,
            custom_sampling_context={"dramatiq_message": message},
        )
        started_transaction.__enter__()
        message._scope_manager = scope_manager
        message._started_transaction = started_transaction

    def after_process_message(self, broker: Broker, message: Message, *, result=None, exception=None):
        client = sentry_sdk.get_client()
        integration = client.get_integration(DramatiqIntegration)

        if integration is None:
            return

        actor = broker.get_actor(message.actor_name)
        throws = message.options.get("throws") or actor.options.get("throws")

        try:
            if (
                exception is not None
                and not (throws and isinstance(exception, throws))
                and not isinstance(exception, Retry)
            ):
                event, hint = event_from_exception(
                    exception,
                    client_options=client.options,
                    mechanism={"type": "dramatiq", "handled": False},
                )
                sentry_sdk.capture_event(event, hint=hint)
        finally:
            message._started_transaction.__exit__(None, None, None)
            message._scope_manager.__exit__(None, None, None)


def _make_message_event_processor(message: Message, broker: Broker):
    def inner(event: Dict[str, Any], hint: Dict[str, Any]) -> Dict[str, Any]:
        with capture_internal_exceptions():
            DramatiqMessageExtractor(message, broker).extract_into_event(event)

        return event

    return inner


class DramatiqMessageExtractor(object):
    def __init__(self, message: Message, broker: Broker):
        self.message = message
        self.broker = broker
        self.message_data = dict(message.asdict())

    def content_length(self) -> int:
        return len(json.dumps(self.message_data))

    def extract_into_event(self, event: Dict[str, Any]):
        client = sentry_sdk.get_client()

        content_length = self.content_length()
        contexts = event.setdefault("contexts", {})
        request_info = contexts.setdefault("dramatiq", {})
        request_info["type"] = "dramatiq"

        bodies = client.options["max_request_body_size"]
        if (
            bodies == "never"
            or (bodies == "small" and content_length > 10 ** 3)
            or (bodies == "medium" and content_length > 10 ** 4)
        ):
            data = AnnotatedValue(
                "",
                {"rem": [["!config", "x", 0, content_length]], "len": content_length},
            )
        else:
            data = self.message_data

        request_info["data"] = data

        actor = self.broker.get_actor(self.message.actor_name)
        request_info["actor"] = {
            "options": actor.options,
        }

        tags = event.setdefault("tags", {})
        tags.update({
            "dramatiq.actor": self.message.actor_name,
            "dramatiq.queue": self.message.queue_name,
            "dramatiq.message_id": self.message.message_id,
        })

        if self.message.options.get("redis_message_id"):
            tags["dramatiq.redis_message_id"] = self.message.options["redis_message_id"]
