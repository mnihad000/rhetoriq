from __future__ import annotations

from datetime import datetime, timezone
import json

import httpx
import pytest

from models.document import Document
from models.events import (
    DetectedSignalEvent,
    DetectedSignalPayload,
    INVESTIGATIONS_COMPLETED_TOPIC,
    INVESTIGATIONS_REQUESTED_TOPIC,
    INVESTIGATIONS_STAGE_EVENTS_TOPIC,
    PROCESSED_DOCUMENTS_TOPIC,
    RAW_DOCUMENTS_TOPIC,
    PRIMARY_TOPICS,
    RawDocumentEvent,
    SignalScore,
    event_schema,
    validate_event,
)
from models.investigation import InvestigationPlanTimeWindow
from models.research import ResearchBudgetLimits
from services.event_factory import raw_event_from_document
from services.event_handlers import EventHandlers
from services.event_store import EventStore
from services.kafka_runtime import InMemoryEventPublisher, OutboxPublisher, SchemaRegistry
from services.research_repository import ResearchRepository
from events.worker import EventProcessor, _is_permanent, _redact
from services.kafka_runtime import SchemaRegistryError


def _document() -> Document:
    return Document(
        id="doc_b3_1",
        source_id="provider:1",
        source_name="example.gov",
        source_type="government_record",
        url="https://example.gov/rule/1",
        title="Example rule",
        published_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        collected_at=datetime(2026, 9, 16, tzinfo=timezone.utc),
        text="Official example rule text.",
        snippet="Official example rule text.",
        language="en",
        content_type="rule",
        entities=[],
        phrases=[],
        metadata={
            "provider": "federal_register",
            "transport": "first_party_public_api",
            "source_native_id": "2026-1",
            "evidence_status": "primary_source_record",
            "source_policy": {"decision": "allow", "citable": True},
            "acquisition_receipt_valid": True,
        },
    )


def test_all_primary_topics_have_typed_json_schemas() -> None:
    for topic in PRIMARY_TOPICS:
        schema = event_schema(topic)
        assert schema["properties"]["schema_version"]["const"] == 1
        assert "$ref" in schema["properties"]["payload"]


def test_outbox_publish_is_durable_and_idempotent(tmp_path) -> None:
    store = EventStore(str(tmp_path / "events.sqlite3"))
    event = raw_event_from_document(
        _document(), producer="test", correlation_id="collection_1"
    )
    assert store.enqueue(RAW_DOCUMENTS_TOPIC, event) is True
    assert store.enqueue(RAW_DOCUMENTS_TOPIC, event) is False

    publisher = InMemoryEventPublisher()
    assert OutboxPublisher(store, publisher).publish_batch() == 1
    assert publisher.messages[0][0] == RAW_DOCUMENTS_TOPIC
    assert store.pending() == []


def test_raw_to_processed_pipeline_is_idempotent(tmp_path, monkeypatch) -> None:
    target = str(tmp_path / "events.sqlite3")
    monkeypatch.setattr("services.postgres_corpus.sync_document", lambda *_args, **_kwargs: None)
    store = EventStore(target)
    handlers = EventHandlers(target)
    processor = EventProcessor(store, handlers)
    raw = raw_event_from_document(
        _document(), producer="test", correlation_id="collection_1"
    )

    assert processor.process(RAW_DOCUMENTS_TOPIC, raw.model_dump(mode="json")) is True
    assert processor.process(RAW_DOCUMENTS_TOPIC, raw.model_dump(mode="json")) is False
    pending = store.pending()
    processed_record = next(item for item in pending if item.topic == PROCESSED_DOCUMENTS_TOPIC)
    processed = validate_event(PROCESSED_DOCUMENTS_TOPIC, processed_record.event_json)
    assert processed.payload.document.id == "doc_b3_1"

    assert processor.process(PROCESSED_DOCUMENTS_TOPIC, processed.model_dump(mode="json")) is True
    assert processor.process(PROCESSED_DOCUMENTS_TOPIC, processed.model_dump(mode="json")) is False


def test_event_id_payload_mismatch_is_rejected(tmp_path) -> None:
    store = EventStore(str(tmp_path / "events.sqlite3"))
    handlers = EventHandlers(str(tmp_path / "events.sqlite3"))
    processor = EventProcessor(store, handlers)
    raw = raw_event_from_document(_document(), producer="test", correlation_id="collection_1")
    processor.process(RAW_DOCUMENTS_TOPIC, raw.model_dump(mode="json"))
    changed = raw.model_copy(deep=True)
    changed.payload.title = "Changed under the same event id"
    with pytest.raises(ValueError, match="different payload"):
        processor.process(RAW_DOCUMENTS_TOPIC, changed.model_dump(mode="json"))


def test_research_events_are_transactionally_staged(tmp_path) -> None:
    repository = ResearchRepository(str(tmp_path / "events.sqlite3"))
    run = repository.create_run("inv_b3", ResearchBudgetLimits())
    event = repository.append_event(run.run_id, "node.started", {"node": "research"})
    records = repository.events.pending(100)
    stage = [item for item in records if item.topic == INVESTIGATIONS_STAGE_EVENTS_TOPIC]
    assert stage
    decoded = validate_event(INVESTIGATIONS_STAGE_EVENTS_TOPIC, stage[-1].event_json)
    assert decoded.payload.sequence == event.sequence
    assert decoded.payload.investigation_id == "inv_b3"


def test_schema_registry_uses_backward_transitive_json_schema() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "PUT":
            return httpx.Response(200, json={"compatibility": "BACKWARD_TRANSITIVE"})
        return httpx.Response(200, json={"id": 41})

    registry = SchemaRegistry(
        "http://registry.test/apis/ccompat/v7",
        transport=httpx.MockTransport(respond),
    )
    assert registry.ensure_schema(RAW_DOCUMENTS_TOPIC) == 41
    assert json.loads(requests[0].content)["compatibility"] == "BACKWARD_TRANSITIVE"
    body = json.loads(requests[1].content)
    assert body["schemaType"] == "JSON"
    assert "RawDocumentPayload" in body["schema"]


def test_dead_letter_errors_are_redacted() -> None:
    value = _redact("authorization=Bearer-abc password=hunter2 ordinary context")
    assert "Bearer-abc" not in value
    assert "hunter2" not in value
    assert "ordinary context" in value


def test_permanent_contract_failures_skip_transient_retry_classification() -> None:
    assert _is_permanent(SchemaRegistryError("bad schema")) is True
    assert _is_permanent(ValueError("policy rejected")) is True
    assert _is_permanent(TimeoutError("broker timeout")) is False


def test_completion_outbox_event_is_exactly_once(tmp_path) -> None:
    repository = ResearchRepository(str(tmp_path / "events.sqlite3"))
    run = repository.create_run("inv_complete", ResearchBudgetLimits())

    repository.append_event(
        run.run_id,
        "run.completed",
        {"status": "completed", "decision": "published", "content_hash": "abc123"},
    )
    repository.append_event(
        run.run_id,
        "run.completed",
        {"status": "completed", "decision": "published", "content_hash": "abc123"},
    )

    completions = [
        item for item in repository.events.pending(100)
        if item.topic == INVESTIGATIONS_COMPLETED_TOPIC
    ]
    assert len(completions) == 1


def test_signal_replay_does_not_duplicate_investigation_request(tmp_path, monkeypatch) -> None:
    from config import get_settings
    monkeypatch.setattr(get_settings(), "FLINK_AUTO_INVESTIGATE", True)
    target = str(tmp_path / "events.sqlite3")
    store = EventStore(target)
    processor = EventProcessor(store, EventHandlers(target))
    signal = DetectedSignalEvent.create(
        DetectedSignalPayload(
            signal_id="signal-1",
            canonical_phrase_id="phrase-1",
            query_text="public records policy",
            observed_window=InvestigationPlanTimeWindow(label="recent"),
            baseline_window=InvestigationPlanTimeWindow(label="all_time"),
            score=SignalScore(spike=2.0, confidence=0.9, observed_count=10, baseline_count=2),
            auto_investigate=True,
            publisher_diversity=3,
            source_diversity=2,
        ),
        producer="test",
        correlation_id="signal-1",
        partition_key="signal-1",
    )

    assert processor.process("signals.detected.v1", signal.model_dump(mode="json")) is True
    assert processor.process(
        "signals.detected.v1",
        signal.model_dump(mode="json"),
        replay_namespace="replay-1",
    ) is True
    requests = [item for item in store.pending(100) if item.topic == INVESTIGATIONS_REQUESTED_TOPIC]
    assert len(requests) == 1
