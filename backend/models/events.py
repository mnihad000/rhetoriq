from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, ClassVar, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from models.document import Document
from models.investigation import InvestigationPlanTimeWindow


RAW_DOCUMENTS_TOPIC = "raw.documents.v1"
ENRICHMENT_REQUESTED_TOPIC = "documents.enrichment-requested.v1"
ENRICHED_DOCUMENTS_TOPIC = "documents.enriched.v1"
LATE_DOCUMENTS_TOPIC = "documents.late.v1"
PIPELINE_EVALUATED_TOPIC = "pipeline.evaluated.v1"
PROCESSED_DOCUMENTS_TOPIC = "documents.processed.v1"
SIGNALS_DETECTED_TOPIC = "signals.detected.v1"
INVESTIGATIONS_REQUESTED_TOPIC = "investigations.requested.v1"
INVESTIGATIONS_STAGE_EVENTS_TOPIC = "investigations.stage-events.v1"
INVESTIGATIONS_COMPLETED_TOPIC = "investigations.completed.v1"

PRIMARY_TOPICS = (
    RAW_DOCUMENTS_TOPIC,
    ENRICHMENT_REQUESTED_TOPIC,
    ENRICHED_DOCUMENTS_TOPIC,
    LATE_DOCUMENTS_TOPIC,
    PIPELINE_EVALUATED_TOPIC,
    PROCESSED_DOCUMENTS_TOPIC,
    SIGNALS_DETECTED_TOPIC,
    INVESTIGATIONS_REQUESTED_TOPIC,
    INVESTIGATIONS_STAGE_EVENTS_TOPIC,
    INVESTIGATIONS_COMPLETED_TOPIC,
)


def dlq_topic(topic: str) -> str:
    if not topic.endswith(".v1"):
        raise ValueError(f"Unsupported topic version: {topic}")
    return f"{topic[:-3]}.dlq.v1"


DLQ_TOPICS = tuple(dlq_topic(topic) for topic in PRIMARY_TOPICS)


class EventModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RetrievalReceipt(EventModel):
    status: Literal[
        "provider_metadata_only",
        "canonical_evidence",
        "primary_source_record",
        "failed",
    ]
    http_status: int | None = None
    parser_version: str | None = None
    content_hash: str | None = None
    policy_decision: Literal["allow", "block", "not_evaluated"] = "not_evaluated"
    limitations: list[str] = Field(default_factory=list)


class RawDocumentPayload(EventModel):
    provider: str
    transport: str
    provider_record_id: str
    provider_query: str | None = None
    provider_cursor: str | None = None
    source_url: str
    canonical_url: str
    title: str
    body: str | None = None
    snippet: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    collected_at: datetime
    language: str | None = None
    content_type: str
    source_type: str | None = None
    retrieval: RetrievalReceipt
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    investigation_id: str | None = None
    run_id: str | None = None
    action_id: str | None = None
    normalized_document: Document | None = None


class ProcessingReceipt(EventModel):
    normalizer_version: str
    duplicate_of_document_id: str | None = None
    quality_flags: list[str] = Field(default_factory=list)
    raw_event_id: str


class SignalScore(EventModel):
    spike: float
    confidence: float
    observed_count: int = Field(ge=0)
    baseline_count: int = Field(ge=0)


class DetectedSignalPayload(EventModel):
    signal_id: str
    canonical_phrase_id: str
    related_phrase_ids: list[str] = Field(default_factory=list)
    observed_window: InvestigationPlanTimeWindow
    baseline_window: InvestigationPlanTimeWindow
    score: SignalScore
    supporting_document_ids: list[str] = Field(default_factory=list)
    source_diversity: int = Field(default=0, ge=0)
    publisher_diversity: int = Field(default=0, ge=0)
    coverage_limitations: list[str] = Field(default_factory=list)
    origin_disclaimer: str = "This signal identifies the first observation in the available dataset, not proven origin."
    auto_investigate: bool = False
    query_text: str | None = None
    horizon: Literal["emerging", "sustained"] | None = None
    revision: int = Field(default=1, ge=1)
    lifecycle_status: Literal["new", "active", "revised", "resolved"] | None = None
    scoring_version: str | None = None
    baseline_statistics: dict[str, Any] = Field(default_factory=dict)
    event_time_quality: str | None = None
    artifact_lineage: list[str] = Field(default_factory=list)
    horizon_metrics: dict[str, Any] = Field(default_factory=dict)
    canonical_phrase: str | None = None
    semantic_output_hash: str | None = None
    first_observed_at: datetime | None = None
    latest_observed_at: datetime | None = None


class EvidenceSpan(EventModel):
    surface_form: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode="after")
    def valid_range(self) -> "EvidenceSpan":
        if self.end < self.start:
            raise ValueError("Evidence span end must be >= start")
        return self


class EntityMention(EventModel):
    value: str
    surface_form: str
    evidence: EvidenceSpan


class CanonicalPhrase(EventModel):
    phrase: str
    surface_form: str
    evidence: EvidenceSpan


class EnrichmentReceipt(EventModel):
    artifact_id: str
    input_hash: str
    output_hash: str
    provider: str
    model: str
    prompt_version: str
    schema_version: str
    embedding_model: str = "gemini-embedding-001"
    embedding: list[float] = Field(min_length=384, max_length=384)
    embedding_input_hash: str | None = None
    embedding_truncated: bool = False
    entities: list[EntityMention] = Field(default_factory=list, max_length=20)
    canonical_phrases: list[CanonicalPhrase] = Field(default_factory=list, max_length=20)
    usage: dict[str, Any] = Field(default_factory=dict)
    failure_state: str | None = None


class EnrichmentRequestedPayload(EventModel):
    document: Document
    input_hash: str
    pipeline_version: str = "b4-v1"
    raw_event_id: str
    quality_flags: list[str] = Field(default_factory=list)
    investigation_id: str | None = None
    run_id: str | None = None
    action_id: str | None = None


class EnrichedDocumentPayload(EventModel):
    document: Document
    input_hash: str
    pipeline_version: str = "b4-v1"
    raw_event_id: str
    quality_flags: list[str] = Field(default_factory=list)
    enrichment: EnrichmentReceipt
    semantic_output_hash: str
    investigation_id: str | None = None
    run_id: str | None = None
    action_id: str | None = None


class LateDocumentPayload(EventModel):
    document: Document
    input_hash: str
    raw_event_id: str
    event_time_quality: str
    lateness_seconds: float = Field(ge=0)
    quality_flags: list[str] = Field(default_factory=list)
    investigation_id: str | None = None
    run_id: str | None = None
    action_id: str | None = None


class PipelineEvaluationPayload(EventModel):
    evaluated_at: datetime
    status: Literal["healthy", "degraded"]
    watermark: datetime | None = None
    signal_count: int = Field(default=0, ge=0)
    late_event_count: int = Field(default=0, ge=0)
    documents_in_state: int = Field(default=0, ge=0)
    details: dict[str, Any] = Field(default_factory=dict)


class ProcessedDocumentPayload(EventModel):
    document: Document
    processing: ProcessingReceipt
    semantic_output_hash: str | None = None
    enrichment: EnrichmentReceipt | None = None
    investigation_id: str | None = None
    run_id: str | None = None
    action_id: str | None = None


class InvestigationRequestedPayload(EventModel):
    investigation_id: str
    run_id: str
    query_text: str | None = None
    signal_id: str | None = None
    requested_outputs: list[str] = Field(default_factory=list)
    time_window: InvestigationPlanTimeWindow = Field(default_factory=InvestigationPlanTimeWindow)
    source_classes: list[str] = Field(default_factory=list)
    authorization_context: dict[str, str] = Field(default_factory=dict)
    force_refresh: bool = False

    @model_validator(mode="after")
    def require_query_or_signal(self) -> "InvestigationRequestedPayload":
        if not self.query_text and not self.signal_id:
            raise ValueError("An investigation request requires query_text or signal_id")
        return self


class InvestigationStagePayload(EventModel):
    investigation_id: str
    run_id: str
    sequence: int = Field(ge=1)
    stage: str
    status: str
    artifact_refs: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    summary: str | None = None


class InvestigationCompletedPayload(EventModel):
    investigation_id: str
    run_id: str
    status: str
    terminal_decision: str
    report_id: str | None = None
    artifact_refs: list[str] = Field(default_factory=list)
    content_hash: str | None = None
    limitations: list[str] = Field(default_factory=list)


class DeadLetterPayload(EventModel):
    original_topic: str
    original_partition: int | None = None
    original_offset: int | None = None
    original_key: str | None = None
    original_event_id: str | None = None
    payload_sha256: str
    safe_payload: dict[str, Any] | None = None
    consumer: str
    failure_class: str
    failure_code: str
    error_message: str
    attempt_count: int = Field(ge=1)
    failed_at: datetime


class EventEnvelope(EventModel):
    event_id: str
    event_type: str
    schema_version: Literal[1] = 1
    occurred_at: datetime
    produced_at: datetime
    producer: str
    correlation_id: str
    causation_id: str | None = None
    partition_key: str
    payload: Any

    payload_model: ClassVar[type[EventModel] | None] = None
    expected_event_type: ClassVar[str | None] = None

    @model_validator(mode="after")
    def validate_contract(self) -> "EventEnvelope":
        if self.expected_event_type and self.event_type != self.expected_event_type:
            raise ValueError(f"Expected event_type {self.expected_event_type}")
        if self.payload_model is not None:
            self.payload_model.model_validate(self.payload)
        return self

    @classmethod
    def create(
        cls,
        payload: EventModel,
        *,
        producer: str,
        correlation_id: str,
        partition_key: str,
        occurred_at: datetime | None = None,
        causation_id: str | None = None,
        event_id: str | None = None,
    ) -> "EventEnvelope":
        now = datetime.now(timezone.utc)
        return cls(
            event_id=event_id or f"evt_{uuid4().hex}",
            event_type=cls.expected_event_type or "event.unknown",
            occurred_at=occurred_at or now,
            produced_at=now,
            producer=producer,
            correlation_id=correlation_id,
            causation_id=causation_id,
            partition_key=partition_key,
            payload=payload.model_dump(mode="json"),
        )


class RawDocumentEvent(EventEnvelope):
    payload: RawDocumentPayload
    payload_model = RawDocumentPayload
    expected_event_type = "raw.document.collected"


class ProcessedDocumentEvent(EventEnvelope):
    payload: ProcessedDocumentPayload
    payload_model = ProcessedDocumentPayload
    expected_event_type = "document.processed"


class DetectedSignalEvent(EventEnvelope):
    payload: DetectedSignalPayload
    payload_model = DetectedSignalPayload
    expected_event_type = "signal.detected"


class InvestigationRequestedEvent(EventEnvelope):
    payload: InvestigationRequestedPayload
    payload_model = InvestigationRequestedPayload
    expected_event_type = "investigation.requested"


class InvestigationStageEvent(EventEnvelope):
    payload: InvestigationStagePayload
    payload_model = InvestigationStagePayload
    expected_event_type = "investigation.stage"


class InvestigationCompletedEvent(EventEnvelope):
    payload: InvestigationCompletedPayload
    payload_model = InvestigationCompletedPayload
    expected_event_type = "investigation.completed"


class DeadLetterEvent(EventEnvelope):
    payload: DeadLetterPayload
    payload_model = DeadLetterPayload
    expected_event_type = "event.dead_lettered"


class EnrichmentRequestedEvent(EventEnvelope):
    payload: EnrichmentRequestedPayload
    payload_model = EnrichmentRequestedPayload
    expected_event_type = "document.enrichment_requested"


class EnrichedDocumentEvent(EventEnvelope):
    payload: EnrichedDocumentPayload
    payload_model = EnrichedDocumentPayload
    expected_event_type = "document.enriched"


class LateDocumentEvent(EventEnvelope):
    payload: LateDocumentPayload
    payload_model = LateDocumentPayload
    expected_event_type = "document.late"


class PipelineEvaluationEvent(EventEnvelope):
    payload: PipelineEvaluationPayload
    payload_model = PipelineEvaluationPayload
    expected_event_type = "pipeline.evaluated"


TOPIC_EVENT_MODELS: dict[str, type[EventEnvelope]] = {
    RAW_DOCUMENTS_TOPIC: RawDocumentEvent,
    ENRICHMENT_REQUESTED_TOPIC: EnrichmentRequestedEvent,
    ENRICHED_DOCUMENTS_TOPIC: EnrichedDocumentEvent,
    LATE_DOCUMENTS_TOPIC: LateDocumentEvent,
    PIPELINE_EVALUATED_TOPIC: PipelineEvaluationEvent,
    PROCESSED_DOCUMENTS_TOPIC: ProcessedDocumentEvent,
    SIGNALS_DETECTED_TOPIC: DetectedSignalEvent,
    INVESTIGATIONS_REQUESTED_TOPIC: InvestigationRequestedEvent,
    INVESTIGATIONS_STAGE_EVENTS_TOPIC: InvestigationStageEvent,
    INVESTIGATIONS_COMPLETED_TOPIC: InvestigationCompletedEvent,
}


def validate_event(topic: str, value: bytes | str | dict[str, Any]) -> EventEnvelope:
    model = TOPIC_EVENT_MODELS.get(topic)
    if model is None:
        if topic in DLQ_TOPICS:
            model = DeadLetterEvent
        else:
            raise ValueError(f"Unknown event topic: {topic}")
    if isinstance(value, bytes):
        return model.model_validate_json(value)
    if isinstance(value, str):
        return model.model_validate_json(value)
    return model.model_validate(value)


def event_schema(topic: str) -> dict[str, Any]:
    model = TOPIC_EVENT_MODELS.get(topic)
    if model is None:
        if topic in DLQ_TOPICS:
            model = DeadLetterEvent
        else:
            raise ValueError(f"Unknown event topic: {topic}")
    return model.model_json_schema()
