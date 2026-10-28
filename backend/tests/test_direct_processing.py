from datetime import datetime, timezone

from models.document import Document
from models.events import (
    PROCESSED_DOCUMENTS_TOPIC,
    RawDocumentEvent,
    RawDocumentPayload,
    RetrievalReceipt,
)
from models.research import ResearchBudgetLimits
from services.b5_repository import ProjectionRepository
from services.event_factory import raw_event_from_document
from services.event_handlers import EventHandlers
from services.event_store import EventStore
from services.research_repository import ResearchRepository


NOW = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)


def _document(*, source_kind: str = "ingestion", citable: bool = True) -> Document:
    status = "canonical_evidence" if citable else "provider_metadata_only"
    return Document(
        id="doc_direct_1",
        source_id="provider:direct-1",
        source_name="example.gov",
        source_type="government_record",
        url="https://example.gov/records/direct-1",
        title="Direct processing record",
        published_at=NOW,
        collected_at=NOW,
        text="A directly processed public record.",
        snippet="A directly processed public record.",
        language="en",
        content_type="article",
        entities=["Example Agency"],
        phrases=["public record"],
        metadata={
            "provider": "fixture",
            "source_kind": source_kind,
            "evidence_status": status,
            "acquisition_receipt_valid": citable,
            "source_policy": {"decision": "allow", "citable": citable},
        },
    )


def test_raw_document_is_persisted_and_attached_to_research_run(tmp_path):
    target = str(tmp_path / "direct.sqlite3")
    research = ResearchRepository(target)
    run = research.create_run("inv_direct", ResearchBudgetLimits(), run_id="run_direct")
    raw = raw_event_from_document(
        _document(), producer="fixture", correlation_id="direct",
        run_id=run.run_id, investigation_id=run.investigation_id,
    )

    EventHandlers(target).normalize_raw_document(raw)

    current = ProjectionRepository(target).current("document", "doc_direct_1")
    assert current is not None
    assert current["eligible"] is True
    assert current["data"]["document"]["metadata"]["evidence_status"] == "canonical_evidence"
    assert [item.id for item in research.get_documents(run.run_id)] == ["doc_direct_1"]
    assert EventStore(target).pending()  # canonical projection remains durable work


def test_discovery_receipt_remains_ineligible(tmp_path):
    target = str(tmp_path / "discovery.sqlite3")
    raw = raw_event_from_document(
        _document(source_kind="discovery", citable=False),
        producer="fixture", correlation_id="direct",
    )

    EventHandlers(target).normalize_raw_document(raw)

    current = ProjectionRepository(target).current("document", "doc_direct_1")
    assert current is not None
    assert current["eligible"] is False
    assert current["data"]["source_kind"] == "discovery"
    assert current["data"]["document"]["metadata"]["evidence_status"] == "provider_metadata_only"


def test_duplicate_raw_event_keeps_processed_identity_and_revision(tmp_path):
    target = str(tmp_path / "duplicate.sqlite3")
    raw = raw_event_from_document(_document(), producer="fixture", correlation_id="direct")
    handlers = EventHandlers(target)

    handlers.normalize_raw_document(raw)
    first = ProjectionRepository(target).current("document", "doc_direct_1")
    handlers.normalize_raw_document(raw)
    second = ProjectionRepository(target).current("document", "doc_direct_1")

    assert first is not None and second is not None
    assert second["snapshot_id"] == first["snapshot_id"]
    assert second["revision"] == first["revision"] == 1
    processed = [item for item in EventStore(target).pending() if item.topic == PROCESSED_DOCUMENTS_TOPIC]
    assert processed == []


def test_raw_without_normalized_document_preserves_receipt_status(tmp_path):
    target = str(tmp_path / "receipt.sqlite3")
    raw = RawDocumentEvent.create(
        RawDocumentPayload(
            provider="fixture",
            transport="search",
            provider_record_id="lead-1",
            source_url="https://example.com/lead-1",
            canonical_url="https://example.com/lead-1",
            title="A search snippet",
            body=None,
            snippet="Only a discovery snippet.",
            collected_at=NOW,
            content_type="snippet",
            source_type="blog",
            retrieval=RetrievalReceipt(
                status="provider_metadata_only",
                policy_decision="allow",
                limitations=["snippet_only"],
            ),
        ),
        producer="fixture", correlation_id="receipt", partition_key="lead-1",
        event_id="raw_receipt_1",
    )

    EventHandlers(target).normalize_raw_document(raw)
    records = ProjectionRepository(target).documents()
    assert len(records) == 1
    metadata = records[0]["data"]["document"]["metadata"]
    assert metadata["evidence_status"] == "provider_metadata_only"
    assert metadata["acquisition_receipt_valid"] is False
