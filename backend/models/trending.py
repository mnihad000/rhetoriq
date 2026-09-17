from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from models.document import Document


TrendingFeedState = Literal["warming", "ready", "stale", "error"]


class DiscoveryQuery(BaseModel):
    query: str
    provider_role: Literal["discovery", "enrichment"]
    topic_seed: str


class DiscoveryRunStats(BaseModel):
    query_count: int = 0
    result_count: int = 0
    fetched_pages: int = 0
    accepted_documents: int = 0
    duplicate_documents: int = 0


class DiscoveryRunRecord(BaseModel):
    run_id: str
    started_at: datetime
    completed_at: datetime | None = None
    status: Literal["running", "completed", "failed"] = "running"
    is_reseed: bool = False
    stats: DiscoveryRunStats = Field(default_factory=DiscoveryRunStats)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    queries: list[DiscoveryQuery] = Field(default_factory=list)


class DiscoveryDocumentRecord(BaseModel):
    doc_id: str
    canonical_url: str
    domain: str
    document: Document
    providers: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    first_seen_at: datetime
    latest_seen_at: datetime
    first_run_id: str
    latest_run_id: str
    seen_run_ids: list[str] = Field(default_factory=list)


class TopicTimelinePoint(BaseModel):
    timestamp: datetime
    count: int


class TrendingTopic(BaseModel):
    id: str
    title: str
    canonical_phrase: str
    summary: str
    related_phrases: list[str] = Field(default_factory=list)
    status: str
    confidence_label: Literal["Low", "Medium", "High"]
    confidence_score: float = 0.0
    source_count: int = 0
    publisher_count: int = 0
    first_observed_at: datetime
    latest_observed_at: datetime
    source_diversity_snapshot: dict[str, int] = Field(default_factory=dict)
    timeline: list[TopicTimelinePoint] = Field(default_factory=list)
    velocity_score: float = 0.0
    persistence_runs: int = 0
    provider_mix: dict[str, int] = Field(default_factory=dict)
    supporting_document_ids: list[str] = Field(default_factory=list)
    # Stream signal fields are additive so legacy snapshots remain readable.
    signal_id: str | None = None
    signal_revision: int = 0
    pipeline_source: str = "legacy"
    emerging_observed_count: int | None = None
    emerging_baseline_count: float | None = None
    emerging_spike: float | None = None
    sustained_observed_count: int | None = None
    sustained_baseline_count: float | None = None
    sustained_spike: float | None = None
    event_time_quality: str = "unknown"
    coverage_limitations: list[str] = Field(default_factory=list)
    origin_disclaimer: str = "This signal identifies the first observation in the available dataset, not proven origin."


class PublishedTrendingSnapshot(BaseModel):
    snapshot_id: str
    state: TrendingFeedState = "warming"
    generated_at: datetime
    fresh_until: datetime
    last_completed_run_at: datetime | None = None
    last_reseed_at: datetime | None = None
    warning: str | None = None
    topics: list[TrendingTopic] = Field(default_factory=list)


class TrendingFeedResponse(BaseModel):
    state: TrendingFeedState
    generated_at: datetime | None = None
    fresh_until: datetime | None = None
    last_completed_run_at: datetime | None = None
    last_reseed_at: datetime | None = None
    warning: str | None = None
    topics: list[TrendingTopic] = Field(default_factory=list)
    source: str = "legacy"
    fallback_active: bool = False
    pipeline_evaluated_at: datetime | None = None


class TrendingStatusResponse(BaseModel):
    state: TrendingFeedState
    redis_available: bool
    refresh_lock_active: bool
    generated_at: datetime | None = None
    fresh_until: datetime | None = None
    last_completed_run_at: datetime | None = None
    last_reseed_at: datetime | None = None
    last_error: str | None = None
    latest_snapshot_id: str | None = None
    source: str = "legacy"
    fallback_active: bool = False
    pipeline_evaluated_at: datetime | None = None
    flink: dict[str, object] = Field(default_factory=dict)
    kafka: dict[str, object] = Field(default_factory=dict)
    enrichment: dict[str, object] = Field(default_factory=dict)
    signals: dict[str, object] = Field(default_factory=dict)


class TrendingInvestigationResponse(BaseModel):
    investigation_id: str
    reused_existing: bool = False
    topic_id: str
    canonical_phrase: str

