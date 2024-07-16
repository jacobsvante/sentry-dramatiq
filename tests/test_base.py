from uuid import UUID

import dramatiq
import sentry_sdk

from sentry_dramatiq import SentryMiddleware, DramatiqIntegration


def test_sentry_is_initialized_after_broker_initialization(initialized_broker, sentry_init):
    assert SentryMiddleware not in [m.__class__ for m in initialized_broker.middleware]
    sentry_init(integrations=[DramatiqIntegration()])
    assert SentryMiddleware in [m.__class__ for m in initialized_broker.middleware]


def test_that_a_single_error_is_captured(broker, worker, capture_events):
    events = capture_events()

    @dramatiq.actor(max_retries=0)
    def dummy_actor(x, y):
        return x / y

    dummy_actor.send(1, 2)
    dummy_actor.send(1, 0)
    broker.join(dummy_actor.queue_name)
    worker.join()

    (event,) = events
    exception = event["exception"]["values"][0]
    assert exception["type"] == "ZeroDivisionError"


def test_that_actor_name_is_set_as_transaction(broker, worker, capture_events):
    events = capture_events()

    @dramatiq.actor(max_retries=0)
    def dummy_actor(x, y):
        return x / y

    dummy_actor.send(1, 0)
    broker.join(dummy_actor.queue_name)
    worker.join()

    (event,) = events
    assert event["transaction"] == "dummy_actor"


def test_that_message_data_is_added_to_tags(broker, worker, capture_events):
    events = capture_events()

    @dramatiq.actor(max_retries=0)
    def dummy_actor(x, y):
        sentry_sdk.capture_message("hi")
        return x / y

    dummy_actor.send(1, 0)
    broker.join(dummy_actor.queue_name)
    worker.join()

    event1, event2 = events
    expected_tags = [
        "dramatiq.actor",
        "dramatiq.queue",
        "dramatiq.message_id",
    ]
    for tag in expected_tags:
        assert tag in event1["tags"]
        assert tag in event2["tags"]


def test_that_local_variables_are_captured(broker, worker, capture_events):
    events = capture_events()

    @dramatiq.actor(max_retries=0)
    def dummy_actor(x, y):
        foo = 42  # noqa
        return x / y

    dummy_actor.send(1, 2)
    dummy_actor.send(1, 0)
    broker.join(dummy_actor.queue_name)
    worker.join()

    (event,) = events
    exception = event["exception"]["values"][0]
    assert exception["stacktrace"]["frames"][-1]["vars"] == {
        "x": "1",
        "y": "0",
        "foo": "42",
    }


def test_that_messages_are_captured(broker, worker, capture_events):
    events = capture_events()

    @dramatiq.actor(max_retries=0)
    def dummy_actor():
        sentry_sdk.capture_message("hi")

    dummy_actor.send()
    broker.join(dummy_actor.queue_name)
    worker.join()

    (event,) = events
    assert event["message"] == "hi"
    assert event["level"] == "info"
    assert event["transaction"] == "dummy_actor"


def test_that_sub_actor_errors_are_captured(broker, worker, capture_events):
    events = capture_events()

    @dramatiq.actor(max_retries=0)
    def dummy_actor(x, y):
        sub_actor.send(x, y)

    @dramatiq.actor(max_retries=0)
    def sub_actor(x, y):
        return x / y

    dummy_actor.send(1, 2)
    dummy_actor.send(1, 0)
    broker.join(dummy_actor.queue_name)
    worker.join()

    (event,) = events
    assert event["transaction"] == "sub_actor"

    exception = event["exception"]["values"][0]
    assert exception["type"] == "ZeroDivisionError"


def test_that_multiple_errors_are_captured(broker, worker, capture_events):
    events = capture_events()

    @dramatiq.actor(max_retries=0)
    def dummy_actor(x, y):
        return x / y

    dummy_actor.send(1, 0)
    broker.join(dummy_actor.queue_name)
    worker.join()

    dummy_actor.send(1, None)
    broker.join(dummy_actor.queue_name)
    worker.join()

    event1, event2 = events

    assert event1["transaction"] == "dummy_actor"
    exception = event1["exception"]["values"][0]
    assert exception["type"] == "ZeroDivisionError"

    assert event2["transaction"] == "dummy_actor"
    exception = event2["exception"]["values"][0]
    assert exception["type"] == "TypeError"


def test_that_message_data_is_added_as_request(broker, worker, capture_events):
    events = capture_events()

    @dramatiq.actor(max_retries=0)
    def dummy_actor(x, y):
        return x / y

    dummy_actor.send_with_options(
        args=(
            1,
            0,
        ),
        max_retries=0,
    )
    broker.join(dummy_actor.queue_name)
    worker.join()

    (event,) = events

    assert event["transaction"] == "dummy_actor"
    request_data = event["contexts"]["dramatiq"]["data"]
    assert request_data["queue_name"] == "default"
    assert request_data["actor_name"] == "dummy_actor"
    assert request_data["args"] == [1, 0]
    assert request_data["kwargs"] == {}
    assert request_data["options"]["max_retries"] == 0
    assert UUID(request_data["message_id"])
    assert isinstance(request_data["message_timestamp"], int)


def test_that_expected_exceptions_are_not_captured(broker, worker, capture_events):
    events = capture_events()

    class ExpectedException(Exception):
        pass

    @dramatiq.actor(max_retries=0, throws=ExpectedException)
    def dummy_actor():
        raise ExpectedException

    dummy_actor.send()
    broker.join(dummy_actor.queue_name)
    worker.join()

    assert events == []


def test_that_retry_exceptions_are_not_captured(broker, worker, capture_events):
    events = capture_events()

    @dramatiq.actor(max_retries=2)
    def dummy_actor():
        raise dramatiq.errors.Retry("Retrying", delay=100)

    dummy_actor.send()
    broker.join(dummy_actor.queue_name)
    worker.join()

    assert events == []


def test_that_unhandled_exceptions_are_captured_per_retry(broker, worker, capture_events):
    events = capture_events()

    @dramatiq.actor(max_retries=1, min_backoff=100, max_backoff=100)
    def dummy_actor():
        raise RuntimeError("unhandled")

    dummy_actor.send()
    broker.join(dummy_actor.queue_name)
    worker.join()

    assert len(events) == 2

    event1, event2 = events
    assert event1["transaction"] == "dummy_actor"
    exception = event1["exception"]["values"][0]
    assert exception["type"] == "RuntimeError"

    assert event2["transaction"] == "dummy_actor"
    exception = event2["exception"]["values"][0]
    assert exception["type"] == "RuntimeError"


def test_that_transaction_is_captured_when_tracing_is_enabled(broker, worker, capture_events):
    sentry_sdk.get_client().options["traces_sample_rate"] = 1
    events = capture_events()

    @dramatiq.actor(queue_name="queue")
    def dummy_actor(a, b):
        pass

    message = dummy_actor.send("a", b=1)
    broker.join(dummy_actor.queue_name)
    worker.join()

    (event,) = events
    assert event["type"] == "transaction"
    request_data = event["contexts"]["dramatiq"]["data"]
    assert request_data["queue_name"] == "queue"
    assert request_data["actor_name"] == "dummy_actor"
    assert request_data["args"] == ["a"]
    assert request_data["kwargs"] == {"b": 1}
    assert request_data["message_id"] == message.message_id

    assert event["transaction"] == "dummy_actor"
    tags = event["tags"]

    assert tags["dramatiq.message_id"] == message.message_id
    assert tags["dramatiq.actor"] == "dummy_actor"
    assert tags["dramatiq.queue"] == "queue"


def test_all_actor_call_are_captured_when_job_is_retried(broker, worker, capture_events):
    sentry_sdk.get_client().options["traces_sample_rate"] = 1
    events = capture_events()

    @dramatiq.actor(max_retries=1, min_backoff=100, max_backoff=100)
    def dummy_actor():
        raise Exception("failed")

    dummy_actor.send()

    broker.join(dummy_actor.queue_name)
    worker.join()

    event1, event2, event3, event4 = events
    for event in events:
        assert event["transaction"] == "dummy_actor"

    assert event1["exception"]["values"][0]["type"] == "Exception"
    assert event3["exception"]["values"][0]["type"] == "Exception"
    assert event2["type"] == "transaction"
    assert event4["type"] == "transaction"


def test_that_transaction_continues_trace(broker, worker, capture_events):
    sentry_sdk.get_client().options["traces_sample_rate"] = 1
    events = capture_events()

    @dramatiq.actor()
    def dummy_actor():
        pass

    with sentry_sdk.start_transaction(name="parent_transaction", sampled=True):
        dummy_actor.send()

    broker.join(dummy_actor.queue_name)
    worker.join()

    event1, event2 = events
    assert event1["type"] == "transaction"
    assert event1["transaction"] == "parent_transaction"

    assert event2["type"] == "transaction"
    assert event2["transaction"] == "dummy_actor"

    assert event1["contexts"]["trace"]["trace_id"] == event2["contexts"]["trace"]["trace_id"]
