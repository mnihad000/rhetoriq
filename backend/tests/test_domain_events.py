from __future__ import annotations
from datetime import datetime, timezone
import json
import pytest
from models.document import Document
from models.events import validate_event
from models.events import (PRIMARY_TOPICS, event_schema, INVESTIGATIONS_COMPLETED_TOPIC, INVESTIGATIONS_STAGE_EVENTS_TOPIC, INVESTIGATIONS_REQUESTED_TOPIC, PROCESSED_DOCUMENTS_TOPIC, RAW_DOCUMENTS_TOPIC)
from models.research import ResearchBudgetLimits
from models.investigation import InvestigationPlanTimeWindow
from services.event_factory import raw_event_from_document
from services.event_handlers import EventHandlers
from services.event_store import EventStore
from services.research_repository import ResearchRepository
from events.worker import EventProcessor


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
