from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ResearchActionType = Literal[
    "web_search",
    "gdelt_search",
    "hacker_news_search",
    "canonical_fetch",
    "browser_fetch",
    "internal_search",
    "assess_evidence",
]
ResearchRetrievalLane = Literal[
    "discovery", "corroboration", "contradiction", "provenance", "official", "community"
]
ResearchRunStatus = Literal[
    "queued",
    "running",
    "completed",
    "insufficient_evidence",
    "configuration_missing",
    "failed",
]
ResearchActionStatus = Literal["queued", "running", "completed", "partial", "failed", "blocked"]


class ResearchBudgetLimits(BaseModel):
    wall_seconds: int = 300
    tool_calls: int = 24
    model_calls: int = 12
    model_tokens: int = 60_000
    spend_usd: float = 0.50
    search_results: int = 60
    canonical_fetches: int = 20
    browser_renders: int = 3
    internal_searches: int = 4
    domain_requests: int = 4
    retries: int = 2


class ResearchBudgetUsage(BaseModel):
    active_seconds: float = 0.0
    tool_calls: int = 0
    model_calls: int = 0
    model_tokens: int = 0
    spend_usd: float = 0.0
    search_results: int = 0
    canonical_fetches: int = 0
    browser_renders: int = 0
    internal_searches: int = 0
    domain_requests: dict[str, int] = Field(default_factory=dict)
    retries: int = 0


class ResearchActionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: ResearchActionType
    retrieval_lane: ResearchRetrievalLane = "discovery"
    gap_ids: list[str] = Field(default_factory=list)
    query: str | None = None
    candidate_id: str | None = None
    requested_source_classes: list[str] = Field(default_factory=list)
    action_summary: str = Field(min_length=8, max_length=280)
    expected_evidence: str = Field(min_length=3, max_length=280)

    @model_validator(mode="after")
    def validate_target(self) -> "ResearchActionDecision":
        if self.action_type in {"web_search", "gdelt_search", "hacker_news_search", "internal_search"} and not self.query:
            raise ValueError(f"{self.action_type} requires query")
        if self.action_type in {"canonical_fetch", "browser_fetch"} and not self.candidate_id:
            raise ValueError(f"{self.action_type} requires candidate_id")
        return self


class ResearchRunSummary(BaseModel):
    run_id: str
    investigation_id: str
    parent_run_id: str | None = None
    mode: Literal["live", "recorded"] = "live"
    plan_version: str = "a2-v1"
    status: ResearchRunStatus
    active_node: str | None = None
    active_action: str | None = None
    last_event_sequence: int = 0
    limits: ResearchBudgetLimits
    usage: ResearchBudgetUsage
    action_count: int = 0
    document_count: int = 0
    source_count: int = 0
    terminal_decision: str | None = None
    warnings: list[str] = Field(default_factory=list)
    started_at: datetime | None = None
    updated_at: datetime
    completed_at: datetime | None = None


class ResearchEvent(BaseModel):
    run_id: str
    sequence: int
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ResearchActionRecord(BaseModel):
    action_id: str
    run_id: str
    sequence: int
    idempotency_key: str | None = None
    decision: ResearchActionDecision
    status: ResearchActionStatus
    provider: str | None = None
    result_count: int = 0
    document_ids: list[str] = Field(default_factory=list)
    receipt_ids: list[str] = Field(default_factory=list)
    duration_ms: int | None = None
    failure_category: str | None = None
    warning: str | None = None
    created_at: datetime
    updated_at: datetime


class PublicationCheck(BaseModel):
    key: str
    label: str
    passed: bool
    measured: float | int | str | bool | None = None
    threshold: float | int | str | bool | None = None
    detail: str


class ResearchEvaluation(BaseModel):
    run_id: str
    investigation_id: str
    passed: bool
    final_decision: Literal["published", "insufficient_evidence"]
    checks: list[PublicationCheck] = Field(default_factory=list)
    failed_reasons: list[str] = Field(default_factory=list)
    metrics: dict[str, float | int | bool] = Field(default_factory=dict)
    created_at: datetime


class ResearchTrailResponse(BaseModel):
    run: ResearchRunSummary | None = None
    events: list[ResearchEvent] = Field(default_factory=list)
    actions: list[ResearchActionRecord] = Field(default_factory=list)
    evaluation: ResearchEvaluation | None = None
    replay_comparison: dict[str, Any] | None = None
    next_sequence: int = 0


class ReplayResponse(BaseModel):
    run: ResearchRunSummary
    source_run_id: str
