from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import logging
from typing import Callable
from urllib.parse import urlparse

from config import get_settings
from models.document import Document
from models.events import (
    CORPUS_PROJECTIONS_TOPIC,
    DetectedSignalEvent,
    ENRICHED_DOCUMENTS_TOPIC,
    ENRICHMENT_REQUESTED_TOPIC,
    EnrichedDocumentEvent,
    EnrichmentRequestedEvent,
    EventEnvelope,
    INVESTIGATION_PROJECTIONS_TOPIC,
    INVESTIGATIONS_COMPLETED_TOPIC,
    INVESTIGATIONS_STAGE_EVENTS_TOPIC,
    InvestigationRequestedEvent,
    LATE_DOCUMENTS_TOPIC,
    PIPELINE_EVALUATED_TOPIC,
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
        self.settings = get_settings()
        self.target = target or self.settings.persistence_target
        self.events = EventStore(self.target)
        self.investigations = InvestigationRepository(self.target)
        self.research = ResearchRepository(self.target)
        self._projection_handlers: dict[str, object] = {}

    def handler_for(self, topic: str) -> tuple[str, Handler]:
        handlers: dict[str, tuple[str, Handler]] = {
            RAW_DOCUMENTS_TOPIC: ("document-normalizer-v1", self.normalize_raw_document),
            ENRICHMENT_REQUESTED_TOPIC: ("document-archival-normalizer-v1", self.archive_enrichment_request),
            ENRICHED_DOCUMENTS_TOPIC: ("document-archival-enrichment-v1", self.archive_enriched_document),
            PROCESSED_DOCUMENTS_TOPIC: ("document-persistence-v1", self.persist_processed_document),
            SIGNALS_DETECTED_TOPIC: ("signal-scheduler-v1", self.schedule_signal),
            INVESTIGATIONS_REQUESTED_TOPIC: ("investigation-worker-v1", self.execute_investigation),
            LATE_DOCUMENTS_TOPIC: ("retired-late-document-v1", self.project_audit_event),
            PIPELINE_EVALUATED_TOPIC: ("retired-pipeline-evaluation-v1", self.project_audit_event),
            INVESTIGATIONS_STAGE_EVENTS_TOPIC: ("investigation-stage-audit-v1", self.project_audit_event),
            INVESTIGATIONS_COMPLETED_TOPIC: ("investigation-completion-audit-v1", self.project_audit_event),
            CORPUS_PROJECTIONS_TOPIC: ("b5-projections-v1", self.project_optional_views),
            INVESTIGATION_PROJECTIONS_TOPIC: ("b5-investigation-projections-v1", self.project_optional_views),
        }
        if topic not in handlers:
            raise ValueError(f"Unsupported event topic for database processing: {topic}")
        return handlers[topic]

    def project_optional_views(self, envelope: EventEnvelope) -> None:
        """Apply optional B5 views when explicitly enabled.

        The canonical projection is already durable in ``b5_repository``.  These
        target adapters are deliberately loaded only for an enabled deployment,
        so the normal Vercel/Railway path has no Elasticsearch, Neo4j, or model
        startup cost.  Each target keeps its own revision ledger in
        ``ProjectionRepository``; replaying one event is therefore safe even if
        a later target fails and the database job is retried.
        """
        if not self.settings.ENABLE_B5_RETRIEVAL:
            logger.info(
                "Skipping optional projection %s because ENABLE_B5_RETRIEVAL is disabled",
                envelope.event_id,
            )
            return

        # Avoid importing target clients (and their optional dependencies) in
        # the default deployment.  Failures from an enabled target intentionally
        # propagate so the durable job remains retryable.
        from events.b5_worker import ProjectionHandlers

        targets = (
            ("neo4j",)
            if envelope.event_type == "investigation.projection.requested"
            else ("elasticsearch", "neo4j", "minilm")
        )
        for target in targets:
            handler = self._projection_handlers.get(target)
            if handler is None:
                handler = ProjectionHandlers(target=target, settings=self.settings)
                self._projection_handlers[target] = handler
            handler.project_event(envelope)

    def close(self) -> None:
        """Release any optional projection adapters created by this handler."""
        for handler in self._projection_handlers.values():
            close = getattr(handler, "close", None)
            if close is not None:
                close()
        self._projection_handlers.clear()

    def normalize_raw_document(self, envelope: EventEnvelope) -> None:
        """Normalize and persist one raw document synchronously.

        The database worker still routes raw events through this handler, but
        there is no intermediate processed-document queue in the production
        path.  The processed envelope remains a stable, typed domain object so
        legacy replays and the outbox can continue to consume it when needed.
        """
        event = RawDocumentEvent.model_validate(envelope.model_dump(mode="json"))
        raw = event.payload
        document = _document_from_raw(event)
        if raw.normalized_document is not None:
            document = _apply_receipt_authority(event, raw.normalized_document)
        self._persist_archived_document(
            document=document,
            raw_event_id=event.event_id,
            quality_flags=list(raw.retrieval.limitations),
            investigation_id=raw.investigation_id,
            run_id=raw.run_id,
            action_id=raw.action_id,
            producer="document-normalizer",
            correlation_id=event.correlation_id,
            causation_id=event.event_id,
            occurred_at=event.occurred_at,
            event_identity=event.event_id,
        )

    def archive_enrichment_request(self, envelope: EventEnvelope) -> None:
        """Archive the requested document without invoking live enrichment.

        Streaming enrichment was retired for the deployment branch.  The
        requested document is still persisted so historical outbox replay does
        not silently discard its receipt, policy, or research lineage.
        """
        event = EnrichmentRequestedEvent.model_validate(envelope.model_dump(mode="json"))
        payload = event.payload
        self._persist_archived_document(
            document=_apply_recorded_receipt_authority(payload.document),
            raw_event_id=payload.raw_event_id,
            quality_flags=[
                *payload.quality_flags,
                "live_streaming_enrichment_retired_no_provider_call",
            ],
            investigation_id=payload.investigation_id,
            run_id=payload.run_id,
            action_id=payload.action_id,
            producer="document-archival-normalizer",
            correlation_id=event.correlation_id,
            causation_id=event.event_id,
            occurred_at=event.occurred_at,
            event_identity=event.event_id,
        )

    def archive_enriched_document(self, envelope: EventEnvelope) -> None:
        """Validate and persist a recorded enrichment artifact only."""
        event = EnrichedDocumentEvent.model_validate(envelope.model_dump(mode="json"))
        payload = event.payload
        from services.enrichment import validate_artifact

        if payload.enrichment.input_hash != payload.input_hash:
            raise ValueError("Recorded enrichment input hash does not match the event")
        if payload.semantic_output_hash != payload.enrichment.output_hash:
            raise ValueError("Recorded enrichment output hash does not match the event")
        validate_artifact(payload.enrichment, payload.document.text)
        self._persist_archived_document(
            document=_apply_recorded_receipt_authority(payload.document),
            raw_event_id=payload.raw_event_id,
            quality_flags=list(payload.quality_flags),
            investigation_id=payload.investigation_id,
            run_id=payload.run_id,
            action_id=payload.action_id,
            enrichment=payload.enrichment,
            semantic_output_hash=payload.semantic_output_hash,
            producer="document-archival-enrichment",
            correlation_id=event.correlation_id,
            causation_id=event.event_id,
            occurred_at=event.occurred_at,
            # An enriched event supersedes its raw snapshot.  Its own identity
            # prevents b5_source_operations from treating new semantics as a
            # conflicting replay of the original raw event.
            event_identity=event.event_id,
        )

    def _persist_archived_document(
        self,
        *,
        document: Document,
        raw_event_id: str,
        quality_flags: list[str],
        investigation_id: str | None,
        run_id: str | None,
        action_id: str | None,
        producer: str,
        correlation_id: str,
        causation_id: str,
        occurred_at: datetime,
        event_identity: str,
        enrichment=None,
        semantic_output_hash: str | None = None,
    ) -> None:
        processed = ProcessedDocumentEvent.create(
            ProcessedDocumentPayload(
                document=document,
                processing=ProcessingReceipt(
                    normalizer_version=self.settings.DOCUMENT_PARSER_VERSION,
                    raw_event_id=raw_event_id,
                    quality_flags=list(dict.fromkeys(quality_flags)),
                ),
                semantic_output_hash=semantic_output_hash,
                enrichment=enrichment,
                investigation_id=investigation_id,
                run_id=run_id,
                action_id=action_id,
            ),
            producer=producer,
            correlation_id=correlation_id,
            causation_id=causation_id,
            partition_key=document.id,
            occurred_at=occurred_at,
            event_id=f"evt_processed_{hashlib.sha256(event_identity.encode()).hexdigest()[:24]}",
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
    receipt_valid = (evidence_status in {"canonical_evidence", "primary_source_record"}
                     and raw.retrieval.policy_decision == "allow")
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


def _apply_receipt_authority(event: RawDocumentEvent, document: Document) -> Document:
    """Reconcile a provider receipt onto a connector-supplied document.

    A normalized document is an optimization supplied by an upstream connector;
    it cannot upgrade a raw acquisition that only contains provider metadata or
    a failed fetch into citable evidence.  The raw event's receipt is therefore
    the authority for evidence status and eligibility on every replay.
    """
    raw = event.payload
    metadata = dict(document.metadata or {})
    evidence_status = raw.retrieval.status
    receipt_valid = (evidence_status in {"canonical_evidence", "primary_source_record"}
                     and raw.retrieval.policy_decision == "allow")
    raw_source_kind = str(raw.provider_metadata.get("source_kind") or "").strip().lower()
    existing_source_kind = str(metadata.get("source_kind") or "ingestion")
    source_kind = "discovery" if raw_source_kind == "discovery" or existing_source_kind == "discovery" else existing_source_kind
    policy = metadata.get("source_policy") if isinstance(metadata.get("source_policy"), dict) else {}
    metadata.update({
        "evidence_status": evidence_status,
        "acquisition_receipt_valid": receipt_valid,
        "source_kind": source_kind,
        "source_policy": {**policy, "decision": raw.retrieval.policy_decision, "citable": receipt_valid},
        "raw_event_id": event.event_id,
    })
    return document.model_copy(update={"metadata": metadata})


def _apply_recorded_receipt_authority(document: Document) -> Document:
    """Preserve receipt metadata carried by archived normalization events.

    Historical enrichment events do not carry the original raw payload, so the
    normalized document's recorded retrieval receipt is the only available
    authority.  Unknown or provider-metadata-only statuses remain ineligible.
    """
    metadata = dict(document.metadata or {})
    retrieval = metadata.get("retrieval") if isinstance(metadata.get("retrieval"), dict) else {}
    evidence_status = retrieval.get("status") or metadata.get("evidence_status")
    policy = metadata.get("source_policy") if isinstance(metadata.get("source_policy"), dict) else {}
    decision = retrieval.get("policy_decision", policy.get("decision", "not_evaluated"))
    receipt_valid = (evidence_status in {"canonical_evidence", "primary_source_record"}
                     and decision == "allow")
    metadata.update({
        "evidence_status": evidence_status or "provider_metadata_only",
        "acquisition_receipt_valid": receipt_valid,
        "source_policy": {
            **policy,
            "decision": decision,
            "citable": receipt_valid,
        },
    })
    return document.model_copy(update={"metadata": metadata})
