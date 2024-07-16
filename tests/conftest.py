import json
import os
import subprocess

import dramatiq
import pytest
import sentry_sdk
from dramatiq.brokers.stub import StubBroker
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport
from sentry_sdk.utils import reraise

from sentry_dramatiq import DramatiqIntegration

SEMAPHORE = "./semaphore"

if not os.path.isfile(SEMAPHORE):
    SEMAPHORE = None


def _get_event(envelope: Envelope):
    return envelope.items[0].payload.json


@pytest.fixture(autouse=True)
def reraise_internal_exceptions(request, monkeypatch):
    errors = []
    if "tests_internal_exceptions" in request.keywords:
        return

    def _capture_internal_exception(self, exc_info):
        errors.append(exc_info)

    @request.addfinalizer
    def _():
        for e in errors:
            reraise(*e)

    monkeypatch.setattr(
        sentry_sdk.Scope, "_capture_internal_exception", _capture_internal_exception
    )


@pytest.fixture
def monkeypatch_test_transport(monkeypatch, assert_semaphore_acceptance):
    def check_event(envelope: Envelope):
        def check_string_keys(map):
            for key, value in map.items():
                assert isinstance(key, (str,))
                if isinstance(value, dict):
                    check_string_keys(value)

        check_string_keys(_get_event(envelope))
        assert_semaphore_acceptance(_get_event(envelope))

    def inner(client):
        monkeypatch.setattr(client, "transport", TestTransport(check_event))

    return inner


def _no_errors_in_semaphore_response(obj):
    """Assert that semaphore didn't throw any errors when processing the
    event."""

    def inner(obj):
        if not isinstance(obj, dict):
            return

        assert "err" not in obj

        for value in obj.values():
            inner(value)

    try:
        inner(obj.get("_meta"))
        inner(obj.get(""))
    except AssertionError:
        raise AssertionError(obj)


@pytest.fixture
def assert_semaphore_acceptance(tmpdir):
    def inner(event):
        if not SEMAPHORE:
            return
        # not dealing with the subprocess API right now
        file = tmpdir.join("event")
        file.write(json.dumps(dict(event)))
        output = json.loads(
            subprocess.check_output(
                [SEMAPHORE, "process-event"], stdin=file.open()
            ).decode("utf-8")
        )
        _no_errors_in_semaphore_response(output)
        output.pop("_meta", None)

    return inner


@pytest.fixture
def sentry_init(monkeypatch_test_transport):
    def inner(*a, **kw):
        client = sentry_sdk.Client(*a, **kw)
        sentry_sdk.Scope.get_global_scope().set_client(client)
        monkeypatch_test_transport(client)

    return inner


class TestTransport(Transport):
    def __init__(self, capture_event_callback):
        Transport.__init__(self)
        self.capture_envelope = capture_event_callback
        self._queue = None

    def capture_envelope(self, envelope: Envelope) -> None:
        pass


@pytest.fixture
def capture_events(monkeypatch):
    def inner():
        events = []
        test_client = sentry_sdk.get_client()
        old_capture_envelope = test_client.transport.capture_envelope

        def append_event(envelope):
            for item in envelope:
                if item.headers.get("type") in ("event", "transaction"):
                    events.append(item.payload.json)
            return old_capture_envelope(envelope)

        monkeypatch.setattr(test_client.transport, "capture_envelope", append_event)

        return events

    return inner


@pytest.fixture
def capture_events_forksafe(monkeypatch):
    def inner():
        events_r, events_w = os.pipe()
        events_r = os.fdopen(events_r, "rb", 0)
        events_w = os.fdopen(events_w, "wb", 0)

        test_client = sentry_sdk.get_client()

        old_capture_envelope = test_client.transport.capture_event

        def append(event):
            events_w.write(json.dumps(event).encode("utf-8"))
            events_w.write(b"\n")
            return old_capture_envelope(event)

        def flush(timeout=None, callback=None):
            events_w.write(b"flush\n")

        monkeypatch.setattr(test_client.transport, "capture_event", append)
        monkeypatch.setattr(test_client, "flush", flush)

        return EventStreamReader(events_r)

    return inner


class EventStreamReader(object):
    def __init__(self, file):
        self.file = file

    def read_event(self):
        return json.loads(self.file.readline().decode("utf-8"))

    def read_flush(self):
        assert self.file.readline() == b"flush\n"


@pytest.fixture
def capture_exceptions(monkeypatch):
    def inner():
        errors = set()
        old_capture_event = sentry_sdk.capture_event

        def capture_event(self, event, hint=None):
            if hint:
                if "exc_info" in hint:
                    error = hint["exc_info"][1]
                    errors.add(error)
            return old_capture_event(self, event, hint=hint)

        monkeypatch.setattr(sentry_sdk, "capture_event", capture_event)
        return errors

    return inner


@pytest.fixture
def broker(sentry_init):
    sentry_init(integrations=[DramatiqIntegration()])
    broker = StubBroker()
    broker.emit_after("process_boot")
    dramatiq.set_broker(broker)
    yield broker
    broker.flush_all()
    broker.close()


@pytest.fixture
def initialized_broker(sentry_init):
    broker = StubBroker()
    broker.emit_after("process_boot")
    dramatiq.set_broker(broker)
    yield broker
    broker.flush_all()
    broker.close()


@pytest.fixture
def worker(broker):
    worker = dramatiq.Worker(broker, worker_timeout=100, worker_threads=1)
    worker.start()
    yield worker
    worker.stop()
