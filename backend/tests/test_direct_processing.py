from datetime import datetime, timezone
import math

import pytest

from models.document import Document
from models.events import (
    CORPUS_PROJECTIONS_TOPIC,
    EnrichedDocumentEvent,
    EnrichedDocumentPayload,
    EventEnvelope,
    EnrichmentReceipt,
    INVESTIGATION_PROJECTIONS_TOPIC,
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
from services.document_normalization import normalize_raw_event
from services.enrichment import _hash, artifact_output_hash, bounded_embedding_input, EnrichmentService
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


@pytest.mark.parametrize("decision", ["block", "not_evaluated"])
def test_canonical_status_cannot_override_source_policy(tmp_path, decision):
    target = str(tmp_path / "policy.sqlite3")
    raw = raw_event_from_document(_document(), producer="fixture", correlation_id="policy")
    payload = raw.payload.model_copy(update={
        "retrieval": raw.payload.retrieval.model_copy(update={"policy_decision": decision}),
    })
    EventHandlers(target).normalize_raw_document(raw.model_copy(update={"payload": payload}))
    current = ProjectionRepository(target).current("document", "doc_direct_1")
    assert current["eligible"] is False
    assert current["data"]["document"]["metadata"]["acquisition_receipt_valid"] is False


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


def test_raw_receipt_cannot_be_upgraded_by_normalized_document(tmp_path):
    target = str(tmp_path / "receipt-authority.sqlite3")
    raw = raw_event_from_document(
        _document(citable=True), producer="fixture", correlation_id="receipt-authority"
    )
    raw = raw.model_copy(update={
        "payload": raw.payload.model_copy(update={
            "retrieval": RetrievalReceipt(
                status="provider_metadata_only", policy_decision="allow", limitations=["snippet_only"]
            )
        })
    })

    EventHandlers(target).normalize_raw_document(raw)

    current = ProjectionRepository(target).current("document", "doc_direct_1")
    assert current is not None
    assert current["eligible"] is False
    assert current["data"]["document"]["metadata"]["evidence_status"] == "provider_metadata_only"
    assert current["data"]["document"]["metadata"]["acquisition_receipt_valid"] is False


def test_withdrawn_document_is_not_restored_by_replayed_raw_event(tmp_path):
    target = str(tmp_path / "withdrawal.sqlite3")
    raw = raw_event_from_document(_document(), producer="fixture", correlation_id="withdrawal")
    handlers = EventHandlers(target)
    handlers.normalize_raw_document(raw)
    repository = ProjectionRepository(target)
    repository.withdraw("doc_direct_1", operation_id="operator-remove", reason="source withdrawn")

    handlers.normalize_raw_document(raw)

    current = repository.current("document", "doc_direct_1")
    assert current is not None
    assert current["revision"] == 2
    assert current["data"]["withdrawn"] is True
    assert current["eligible"] is False


def test_unsupported_raw_retrieval_status_fails_closed(tmp_path):
    target = str(tmp_path / "invalid-receipt.sqlite3")
    raw = raw_event_from_document(_document(), producer="fixture", correlation_id="invalid")
    payload = raw.model_dump(mode="json")
    payload["payload"]["retrieval"]["status"] = "unsupported_status"

    with pytest.raises(ValueError):
        EventHandlers(target).normalize_raw_document(EventEnvelope.model_validate(payload))

    assert ProjectionRepository(target).documents() == []


def test_optional_projection_routes_targets_and_reuses_handlers(tmp_path, monkeypatch):
    import sys
    import types

    instances = []

    class FakeProjectionHandlers:
        def __init__(self, *, target, settings):
            self.target = target
            self.events = []
            self.closed = False
            instances.append(self)

        def project_event(self, event):
            self.events.append(event.event_id)

        def close(self):
            self.closed = True

    fake_b5_worker = types.ModuleType("events.b5_worker")
    fake_b5_worker.ProjectionHandlers = FakeProjectionHandlers
    monkeypatch.setitem(sys.modules, "events.b5_worker", fake_b5_worker)
    handlers = EventHandlers(str(tmp_path / "projections.sqlite3"))
    monkeypatch.setattr(handlers.settings, "ENABLE_B5_RETRIEVAL", True)
    document_event = EventEnvelope.model_construct(
        event_id="corpus-1", event_type="corpus.projection.requested", payload={}
    )
    investigation_event = EventEnvelope.model_construct(
        event_id="investigation-1", event_type="investigation.projection.requested", payload={}
    )

    assert handlers.handler_for(CORPUS_PROJECTIONS_TOPIC)[1] == handlers.project_optional_views
    assert handlers.handler_for(INVESTIGATION_PROJECTIONS_TOPIC)[1] == handlers.project_optional_views
    handlers.project_optional_views(document_event)
    handlers.project_optional_views(document_event)
    handlers.project_optional_views(investigation_event)

    assert [item.target for item in instances] == ["elasticsearch", "neo4j", "minilm"]
    assert [item.events for item in instances] == [["corpus-1", "corpus-1"], ["corpus-1", "corpus-1", "investigation-1"], ["corpus-1", "corpus-1"]]
    handlers.close()
    assert all(item.closed for item in instances)


def _recorded_receipt(document: Document, *, input_hash: str = "input-1") -> EnrichmentReceipt:
    embedding_text, truncated = bounded_embedding_input(document.text)
    receipt = EnrichmentReceipt(
        artifact_id="artifact-direct-1",
        input_hash=input_hash,
        output_hash="pending",
        provider="recorded",
        model="recorded-model",
        prompt_version="prompt-v1",
        schema_version="schema-v1",
        embedding=[1.0 / math.sqrt(384)] * 384,
        embedding_input_hash=_hash(embedding_text),
        embedding_truncated=truncated,
    )
    return receipt.model_copy(update={"output_hash": artifact_output_hash(receipt)})


def test_enrichment_request_archives_document_without_provider_call(tmp_path, monkeypatch):
    target = str(tmp_path / "enrichment-request.sqlite3")
    raw = raw_event_from_document(_document(), producer="fixture", correlation_id="archive-request")
    request = normalize_raw_event(raw)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("archival enrichment path must not call a provider")

    monkeypatch.setattr(EnrichmentService, "enrich", fail_if_called)
    handlers = EventHandlers(target)
    handlers.normalize_raw_document(raw)
    handlers.archive_enrichment_request(request)
    handlers.archive_enrichment_request(request)

    current = ProjectionRepository(target).current("document", "doc_direct_1")
    assert current is not None
    assert current["eligible"] is True
    assert current["revision"] == 2
    assert "live_streaming_enrichment_retired_no_provider_call" in current["data"]["processing"]["quality_flags"]
    assert current["data"]["document"]["metadata"]["evidence_status"] == "canonical_evidence"


def test_enriched_document_archives_recorded_artifact_as_new_revision(tmp_path, monkeypatch):
    target = str(tmp_path / "enriched.sqlite3")
    document = _document()
    raw = raw_event_from_document(document, producer="fixture", correlation_id="archive-enriched")
    handlers = EventHandlers(target)
    handlers.normalize_raw_document(raw)
    receipt = _recorded_receipt(document)
    enriched = EnrichedDocumentEvent.create(
        EnrichedDocumentPayload(
            document=document,
            input_hash=receipt.input_hash,
            raw_event_id=raw.event_id,
            quality_flags=["recorded_artifact"],
            enrichment=receipt,
            semantic_output_hash=receipt.output_hash,
        ),
        producer="recorded-enrichment",
        correlation_id=raw.correlation_id,
        causation_id=raw.event_id,
        partition_key=document.id,
        event_id="enriched-direct-1",
    )

    provider_calls = []
    monkeypatch.setattr(EnrichmentService, "enrich", lambda *args, **kwargs: provider_calls.append(args))
    handlers.archive_enriched_document(enriched)

    current = ProjectionRepository(target).current("document", document.id)
    assert current is not None
    assert current["revision"] == 2
    assert current["data"]["enrichment"]["artifact_id"] == receipt.artifact_id
    assert current["data"]["processing"]["raw_event_id"] == raw.event_id
    assert current["data"]["document"]["metadata"]["acquisition_receipt_valid"] is True
    assert provider_calls == []


def test_invalid_recorded_enrichment_fails_closed_before_persistence(tmp_path):
    target = str(tmp_path / "invalid-enriched.sqlite3")
    document = _document()
    receipt = _recorded_receipt(document).model_copy(update={"output_hash": "tampered"})
    enriched = EnrichedDocumentEvent.create(
        EnrichedDocumentPayload(
            document=document,
            input_hash=receipt.input_hash,
            raw_event_id="raw-invalid-enriched",
            enrichment=receipt,
            semantic_output_hash=receipt.output_hash,
        ),
        producer="recorded-enrichment",
        correlation_id="invalid-enriched",
        partition_key=document.id,
        event_id="enriched-invalid-1",
    )

    with pytest.raises(ValueError):
        EventHandlers(target).archive_enriched_document(enriched)

    assert ProjectionRepository(target).documents() == []


def test_unknown_database_topic_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="Unsupported event topic"):
        EventHandlers(str(tmp_path / "unknown-topic.sqlite3")).handler_for("documents.unsupported.v1")
