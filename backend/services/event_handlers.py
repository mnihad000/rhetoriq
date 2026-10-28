from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import logging
from typing import Callable
from urllib.parse import urlparse

from config import get_settings
from models.document import Document
from models.events import (
    DetectedSignalEvent,
    EventEnvelope,
    InvestigationRequestedEvent,
    PROCESSED_DOCUMENTS_TOPIC,
    ProcessedDocumentEvent,
    ProcessedDocumentPayload,
    ProcessingReceipt,
    RAW_DOCUMENTS_TOPIC,
    RawDocumentEvent,
    SIGNALS_DETECTED_TOPIC,
    INVESTIGATIONS_REQUESTED_TOPIC,
)
from services.document_store import live_store
from services.event_store import EventStore
from services.investigation_repository import InvestigationRepository
from services.research_repository import ResearchRepository


Handler = Callable[[EventEnvelope], None]
logger = logging.getLogger(__name__)
_SOURCE_TYPES = {
    "forum", "blog", "local_news", "national_news", "commentary",
    "speech_transcript", "government_record",
}


class EventHandlers:
    def __init__(self, target: str | None = None) -> None:
        settings = get_settings()
        self.target = target or settings.persistence_target
        self.events = EventStore(self.target)
        self.investigations = InvestigationRepository(self.target)
        self.research = ResearchRepository(self.target)

    def handler_for(self, topic: str) -> tuple[str, Handler]:
        handlers: dict[str, tuple[str, Handler]] = {
            RAW_DOCUMENTS_TOPIC: ("document-normalizer-v1", self.normalize_raw_document),
            PROCESSED_DOCUMENTS_TOPIC: ("document-persistence-v1", self.persist_processed_document),
            SIGNALS_DETECTED_TOPIC: ("signal-scheduler-v1", self.schedule_signal),
            INVESTIGATIONS_REQUESTED_TOPIC: ("investigation-worker-v1", self.execute_investigation),
        }
        return handlers.get(topic, (f"{topic}-projector-v1", self.project_audit_event))

    def normalize_raw_document(self, envelope: EventEnvelope) -> None:
        """Normalize and persist one raw document synchronously.

        The database worker still routes raw events through this handler, but
        there is no intermediate processed-document queue in the production
        path.  The processed envelope remains a stable, typed domain object so
        legacy replays and the outbox can continue to consume it when needed.
        """
        event = RawDocumentEvent.model_validate(envelope.model_dump(mode="json"))
        raw = event.payload
        document = raw.normalized_document or _document_from_raw(event)
        processed = ProcessedDocumentEvent.create(
            ProcessedDocumentPayload(
                document=document,
                processing=ProcessingReceipt(
                    normalizer_version=get_settings().DOCUMENT_PARSER_VERSION,
                    raw_event_id=event.event_id,
                    quality_flags=list(raw.retrieval.limitations),
                ),
                investigation_id=raw.investigation_id,
                run_id=raw.run_id,
                action_id=raw.action_id,
            ),
            producer="document-normalizer",
            correlation_id=event.correlation_id,
            causation_id=event.event_id,
            partition_key=document.id,
            occurred_at=event.occurred_at,
            event_id=f"evt_processed_{hashlib.sha256(event.event_id.encode()).hexdigest()[:24]}",
        )
        self.persist_processed_document(processed)

    def persist_processed_document(self, envelope: EventEnvelope) -> None:
        event = ProcessedDocumentEvent.model_validate(envelope.model_dump(mode="json"))
        payload = event.payload
        from services.b5_repository import ProjectionRepository
        from services.database import connect
        projections = ProjectionRepository(self.target)
        metadata = payload.document.metadata or {}
        source_kind = "discovery" if metadata.get("source_kind") == "discovery" else ("research" if payload.run_id else "ingestion")
        with connect(self.target) as conn:
            snapshot = projections.record_document(
                payload.document, source_event_id=event.event_id, enrichment=payload.enrichment,
                processing=payload.processing, source_kind=source_kind, connection=conn)
            if payload.enrichment is not None:
                from services.hosted_vectors import persist_hosted_vector
                persist_hosted_vector(self.target, payload.document.id, payload.enrichment, connection=conn)
            if payload.run_id:
                member = payload.document
                if snapshot["data"].get("withdrawn"):
                    member = member.model_copy(update={"metadata": {**(member.metadata or {}),
                        "acquisition_receipt_valid": False, "evidence_status": "withdrawn",
                        "retrieval_limitation": "Canonical document withdrawn by operator"}})
                self.research.save_document(payload.run_id, member, connection=conn)
        # Process-local compatibility state is updated only after durable commit.
        live_store.save(Document.model_validate(snapshot["data"]["document"]))

    def schedule_signal(self, envelope: EventEnvelope) -> None:
        # Stream-signal scheduling was retired with Flink.  Keep the handler
        # as a visible no-op so old envelopes can be acknowledged safely while
        # periodic database-backed discovery remains the source of trending.
        event = DetectedSignalEvent.model_validate(envelope.model_dump(mode="json"))
        logger.info("Ignoring retired signal event %s; automatic investigations are disabled", event.event_id)

    def execute_investigation(self, envelope: EventEnvelope) -> None:
        event = InvestigationRequestedEvent.model_validate(envelope.model_dump(mode="json"))
        from services.autonomous_research import get_research_manager
        get_research_manager().execute_queued(event.payload.run_id)

    def project_audit_event(self, envelope: EventEnvelope) -> None:
        # Stage and completion records are transactionally stored before their
        # outbox entry. This consumer ledger proves projection delivery without
        # duplicating the authoritative PostgreSQL state.  Flink evaluation and
        # late-document projections are retired; acknowledge them without
        # recreating a second signal store.
        if envelope.event_type in {"pipeline.evaluated", "document.late"}:
            logger.info("Ignoring retired pipeline audit event %s", envelope.event_id)
        return


def _document_from_raw(event: RawDocumentEvent) -> Document:
    raw = event.payload
    source_type = raw.source_type if raw.source_type in _SOURCE_TYPES else "commentary"
    hostname = (urlparse(raw.canonical_url).hostname or raw.provider).lower()
    stable_id = "doc_" + hashlib.sha256(
        f"{raw.provider}:{raw.provider_record_id}:{raw.canonical_url}".encode("utf-8")
    ).hexdigest()[:20]
    text = (raw.body or raw.snippet or raw.title).strip()
    evidence_status = raw.retrieval.status
    receipt_valid = evidence_status in {"canonical_evidence", "primary_source_record"}
    return Document(
        id=stable_id,
        source_id=f"{raw.provider}:{raw.provider_record_id}",
        source_name=hostname,
        source_type=source_type,
        url=raw.canonical_url,
        title=raw.title,
        published_at=raw.published_at,
        collected_at=raw.collected_at,
        text=text,
        snippet=(raw.snippet or text[:320]),
        language=raw.language or "unknown",
        content_type=raw.content_type,
        entities=[],
        phrases=[],
        metadata={
            "provider": raw.provider,
            "transport": raw.transport,
            "source_native_id": raw.provider_record_id,
            "provider_query": raw.provider_query,
            "provider_cursor": raw.provider_cursor,
            "canonical_url": raw.canonical_url,
            "publication_timestamp": raw.published_at.isoformat() if raw.published_at else None,
            "collection_timestamp": raw.collected_at.isoformat(),
            "source_policy": {
                "decision": raw.retrieval.policy_decision,
                "citable": receipt_valid,
            },
            "fetch_outcome": {
                "status": raw.retrieval.status,
                "http_status": raw.retrieval.http_status,
                "parser_version": raw.retrieval.parser_version,
                "content_hash": raw.retrieval.content_hash,
            },
            "limitations": raw.retrieval.limitations,
            "provider_metadata": raw.provider_metadata,
            "raw_event_id": event.event_id,
            # These are deliberately derived from the acquisition receipt and
            # never promoted from a discovery/snippet payload.
            "evidence_status": evidence_status,
            "acquisition_receipt_valid": receipt_valid,
            "source_kind": str(raw.provider_metadata.get("source_kind") or "ingestion"),
        },
    )
