from __future__ import annotations

import httpx
import pytest

from events.enrichment_worker import EnrichmentWorker
from models.events import EnrichmentRequestedEvent
from services.enrichment_artifacts import BudgetExceeded
from tests.test_b4_enrichment import _request


class Message:
    def __init__(self, value=b"framed", partition=2, offset=7):
        self._value, self._partition, self._offset = value, partition, offset

    def value(self): return self._value
    def key(self): return b"doc-1"
    def partition(self): return self._partition
    def offset(self): return self._offset
    def topic(self): return "documents.enrichment-requested.v1"
    def error(self): return None


class Consumer:
    def __init__(self, message):
        self.message = message
        self.subscriptions, self.commits, self.pauses, self.resumes = [], [], [], []
        self.poll_count = 0
        self.closed = False
        self.partitions = ["p0", "p1"]

    def subscribe(self, topics): self.subscriptions.append(topics)
    def assignment(self): return self.partitions
    def pause(self, partitions): self.pauses.append(list(partitions))
    def resume(self, partitions): self.resumes.append(list(partitions))
    def poll(self, _timeout):
        self.poll_count += 1
        if self.poll_count == 1: return self.message
        return None
    def commit(self, **kwargs): self.commits.append(kwargs)
    def close(self): self.closed = True


class Publisher:
    def __init__(self, failures=0): self.failures, self.messages = failures, []
    def publish(self, topic, event):
        self.messages.append((topic, event))
        if self.failures:
            self.failures -= 1
            raise ConnectionError("broker unavailable")


class Registry:
    def __init__(self, request): self.request, self.calls = request, 0
    def decode(self, _topic, _value): self.calls += 1; return self.request.model_dump(mode="json")


def test_publish_failure_keeps_exact_output_pending_and_pauses_all_partitions():
    request = _request()
    consumer, publisher, registry = Consumer(Message()), Publisher(failures=1), Registry(request)
    service = type("Service", (), {"enrich": lambda self, _request: "stable-output"})()
    worker = EnrichmentWorker(service, sleep=lambda _seconds: None)

    worker.run_forever(consumer, publisher, registry, max_messages=1)

    assert consumer.commits and len(consumer.commits) == 1
    assert [event for _topic, event in publisher.messages] == ["stable-output", "stable-output"]
    assert consumer.pauses and consumer.pauses[0] == ["p0", "p1"]
    assert consumer.resumes == [["p0", "p1"]]
    assert consumer.closed is True


def test_registry_outage_retries_and_does_not_dlq_valid_input():
    request = _request()
    consumer = Consumer(Message())

    class FlakyRegistry(Registry):
        def decode(self, topic, value):
            self.calls += 1
            if self.calls == 1: raise httpx.ConnectError("registry unavailable")
            return self.request.model_dump(mode="json")

    registry = FlakyRegistry(request)
    output = Publisher()
    service = type("Service", (), {"enrich": lambda self, _request: "output"})()
    worker = EnrichmentWorker(service, sleep=lambda _seconds: None)
    worker.run_forever(consumer, output, registry, max_messages=1)

    assert registry.calls == 2
    assert [topic for topic, _event in output.messages] == ["documents.enriched.v1"]
    assert consumer.commits


def test_budget_pause_retries_same_request_and_resets_health():
    request = _request()
    consumer, output, registry = Consumer(Message()), Publisher(), Registry(request)

    class Service:
        def __init__(self): self.calls = 0
        def enrich(self, _request):
            self.calls += 1
            if self.calls == 1: raise BudgetExceeded("budget exhausted")
            return "output"

    service = Service()
    worker = EnrichmentWorker(service, sleep=lambda _seconds: None)
    worker.run_forever(consumer, output, registry, max_messages=1)

    assert service.calls == 2
    assert worker.health_state == "healthy"
    assert worker.budget_paused is False
    assert consumer.commits


def test_poll_stopiteration_is_not_swallowed_and_consumer_closes():
    class StoppingConsumer(Consumer):
        def poll(self, _timeout): raise StopIteration

    consumer = StoppingConsumer(Message())
    worker = EnrichmentWorker(type("Service", (), {"enrich": lambda self, request: request})())
    with pytest.raises(StopIteration):
        worker.run_forever(consumer, Publisher(), Registry(_request()))
    assert consumer.closed is True
