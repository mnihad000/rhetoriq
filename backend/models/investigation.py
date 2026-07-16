from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from models.document import (
    Document,
    SourceContentForm,
    SourceIdeology,
    SourceInstitutionKind,
)


PlannerIntent = Literal[
    "origin",
    "spread",
    "counter-narrative",
    "source-ecosystem",
    "general investigation",
]

RetrievalMode = Literal["broad", "narrow"]
InvestigationStatus = Literal[
    "planning_completed",
    "retrieval_completed",
    "source_diversity_completed",
    "timeline_completed",
    "counter_narrative_completed",
    "narrative_family_completed",
    "gap_analysis_completed",
    "provenance_completed",
    "analyst_completed",
    "skeptic_completed",
    "claim_counterpoint_completed",
    "receipts_completed",
    "agent_debate_completed",
    "report_completed",
    "research_loop_completed",
]
InvestigationStage = Literal[
    "planner",
    "retriever",
    "source_diversity",
    "timeline",
    "counter_narrative",
    "narrative_family",
    "gap_analysis",
    "provenance",
    "analyst",
    "skeptic",
    "claim_counterpoint",
    "receipts",
    "agent_debate",
    "report",
    "research_loop",
]
CoverageConfidence = Literal["low", "medium", "high"]
TimelineEventType = Literal[
    "first_observed",
    "early_amplification",
    "broader_pickup",
    "official_mention",
    "counter_narrative_entry",
    "resurfacing",
    "other",
]
NarrativeSide = Literal["main", "counter", "related", "unknown"]
TimelineConfidenceLabel = Literal["low", "medium", "high"]
CounterNarrativeRelationship = Literal["opposing", "reframing", "corrective"]
CounterNarrativeConfidenceLabel = Literal["low", "medium", "high"]
NarrativeGrowthStatus = Literal["emerging", "amplifying", "mainstreaming", "declining", "unknown"]
NarrativeFamilyConfidenceLabel = Literal["low", "medium", "high", "unknown"]
NarrativeFamilyBranchType = Literal["main", "counter", "related", "mutation"]
NarrativeFamilyGenerationMethod = Literal["deterministic", "hybrid_agent"]
ClaimType = Literal["observed_fact", "inference", "uncertainty", "limitation", "recommendation"]
AnalystConfidenceLabel = Literal["low", "medium", "high"]
ClaimCounterpointType = Literal["opposing", "corrective", "reframing"]
ClaimCounterpointConfidenceLabel = Literal["low", "medium", "high"]
ClaimSupportStatus = Literal[
    "supported",
    "partially_supported",
    "unsupported",
    "contradicted",
    "insufficient_evidence",
]
ReceiptVerificationStatus = Literal["verified", "unavailable", "metadata_mismatch", "pending"]
ClaimVerificationState = Literal[
    "verified",
    "mixed",
    "metadata_mismatch",
    "unavailable",
    "pending",
    "not_available",
]
ReceiptsConfidenceLabel = Literal["low", "medium", "high"]
AgentDebateConfidenceLabel = Literal["low", "medium", "high"]
ReportConfidenceLabel = Literal["low", "medium", "high"]
SourceDiversityConfidenceLabel = Literal["low", "medium", "high"]
RetrievalLane = Literal["discovery", "corroboration", "contradiction", "provenance", "official", "community"]
EvidenceQualityBand = Literal["tier_a", "tier_b", "tier_c", "tier_d"]
DateConfidenceLabel = Literal["low", "medium", "high"]
GapSeverity = Literal["low", "medium", "high", "critical"]
GapStatus = Literal["open", "resolved", "deferred"]
GapType = Literal[
    "chronology",
    "source_diversity",
    "contradiction",
    "primary_source",
    "claim_support",
    "duplication",
    "evergreen_contamination",
    "origin_confidence",
    "verification",
    "provenance",
]
SkepticDecision = Literal["pass", "pass_with_softening", "retry_required", "claim_rejected"]
ClaimLedgerState = Literal[
    "proposed",
    "supported",
    "partially_supported",
    "contradicted",
    "unresolved",
    "rejected",
    "softened",
]
ResearchLoopFinalDecision = Literal[
    "completed",
    "completed_with_softening",
    "insufficient_evidence",
    "configuration_missing",
]


class InvestigationPlanTimeWindow(BaseModel):
    start: str | None = None
    end: str | None = None
    label: Literal["today", "this_week", "this_month", "recent", "all_time", "custom", "unknown"] = "unknown"


class StopCondition(BaseModel):
    id: str
    description: str
    required: bool = True
    status: Literal["pending", "satisfied", "unsatisfied"] = "pending"


class RivalHypothesis(BaseModel):
    id: str
    hypothesis: str
    rationale: str | None = None


class InvestigationPlan(BaseModel):
    query_text: str
    topic: str
    primary_question: str | None = None
    canonical_phrase: str | None = None
    intent: PlannerIntent
    entities: list[str] = Field(default_factory=list)
    subquestions: list[str] = Field(default_factory=list)
    rival_hypotheses: list[RivalHypothesis] = Field(default_factory=list)
    disconfirming_evidence_criteria: list[str] = Field(default_factory=list)
    must_have_source_classes: list[str] = Field(default_factory=list)
    retrieval_lanes: list[RetrievalLane] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    semantic_queries: list[str] = Field(default_factory=list)
    target_source_types: list[str] = Field(default_factory=list)
    requested_outputs: list[str] = Field(default_factory=list)
    stop_conditions: list[StopCondition] = Field(default_factory=list)
    time_window: InvestigationPlanTimeWindow = Field(default_factory=InvestigationPlanTimeWindow)
    retrieval_mode: RetrievalMode = "broad"
    risk_notes: list[str] = Field(default_factory=list)
    uncertainty_requirements: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_derived_fields(self) -> "InvestigationPlan":
        if self.primary_question is None:
            self.primary_question = self.query_text
        if not self.retrieval_lanes:
            self.retrieval_lanes = _default_retrieval_lanes(self.intent)
        if not self.must_have_source_classes:
            self.must_have_source_classes = list(self.target_source_types)
        if not self.stop_conditions:
            self.stop_conditions = _default_stop_conditions(self.intent)
        if not self.subquestions:
            self.subquestions = _default_subquestions(self)
        return self


class PlannerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_text: str = Field(min_length=3)
    prior_context: dict | None = None


class PlannerResponse(BaseModel):
    investigation_id: str
    status: InvestigationStatus = "planning_completed"
    current_stage: InvestigationStage = "planner"
    query_text: str
    plan: InvestigationPlan
    warnings: list[str] = Field(default_factory=list)


class RecentInvestigationSummary(BaseModel):
    investigation_id: str
    query_text: str
    status: InvestigationStatus
    updated_at: datetime
    report_title: str
    report_summary: str | None = None
    receipt_count: int = 0
    source_count: int = 0


class SearchResult(BaseModel):
    query: str
    title: str
    url: str
    snippet: str | None = None
    rank: int
    provider: str
    provider_score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RawPage(BaseModel):
    url: str
    final_url: str
    status_code: int
    content_type: str | None = None
    html: str
    fetched_at: datetime


class FetchFailure(BaseModel):
    url: str
    error_type: str
    message: str
    status_code: int | None = None
    retryable: bool = False


class DuplicateCandidate(BaseModel):
    left_doc_id: str
    right_doc_id: str
    similarity_score: float
    reason: str
    cluster_id: str | None = None
    shared_origin_hint: str | None = None


class RetrievalDocumentAnnotation(BaseModel):
    document_id: str
    retrieval_lane: RetrievalLane
    retrieval_query: str
    pass_number: int = 1
    relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    contradiction_signal: float = Field(default=0.0, ge=0.0, le=1.0)
    source_uniqueness_score: float = Field(default=0.0, ge=0.0, le=1.0)
    primary_source_likelihood: float = Field(default=0.0, ge=0.0, le=1.0)
    date_confidence: DateConfidenceLabel = "low"
    quality_band: EvidenceQualityBand = "tier_d"
    duplicate_cluster_id: str | None = None
    upstream_origin_hint: str | None = None
    provenance_hint: str | None = None
    independence_penalty: float = Field(default=0.0, ge=0.0, le=1.0)


class RetrievalRound(BaseModel):
    round_number: int
    queries: list[str] = Field(default_factory=list)
    provider: str
    lane: RetrievalLane | None = None
    pass_number: int = 1
    discovered_results: int = 0
    fetched_pages: int = 0
    accepted_documents: int = 0
    new_documents: int = 0
    warnings: list[str] = Field(default_factory=list)


class CoverageSummary(BaseModel):
    total_documents: int = 0
    unique_sources: int = 0
    source_type_distribution: dict[str, int] = Field(default_factory=dict)
    lane_distribution: dict[str, int] = Field(default_factory=dict)
    has_counter_narrative_candidates: bool = False
    has_timeline_coverage: bool = False
    has_official_source: bool = False
    exact_phrase_hits: int = 0
    search_rounds_completed: int = 0


class RetrievalResult(BaseModel):
    investigation_id: str
    plan_snapshot: InvestigationPlan
    retrieved_document_ids: list[str] = Field(default_factory=list)
    high_relevance_document_ids: list[str] = Field(default_factory=list)
    main_narrative_document_ids: list[str] = Field(default_factory=list)
    counter_narrative_candidate_ids: list[str] = Field(default_factory=list)
    context_document_ids: list[str] = Field(default_factory=list)
    document_annotations: list[RetrievalDocumentAnnotation] = Field(default_factory=list)
    possible_duplicate_pairs: list[DuplicateCandidate] = Field(default_factory=list)
    search_rounds: list[RetrievalRound] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    coverage_summary: CoverageSummary = Field(default_factory=CoverageSummary)
    evidence_coverage_confidence: CoverageConfidence = "low"
    cached: bool = False


class RetrieveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force_refresh: bool = False
    max_rounds: int | None = Field(default=None, ge=1, le=6)


class SourceDiversityFinding(BaseModel):
    id: str
    label: str
    detail: str


class SourceDiversityResult(BaseModel):
    investigation_id: str
    plan_snapshot: InvestigationPlan
    total_documents: int = 0
    classified_documents: int = 0
    source_type_distribution: dict[str, int] = Field(default_factory=dict)
    geographic_distribution: dict[str, int] = Field(default_factory=dict)
    institution_distribution: dict[SourceInstitutionKind | str, int] = Field(default_factory=dict)
    content_form_distribution: dict[SourceContentForm | str, int] = Field(default_factory=dict)
    ideology_distribution: dict[SourceIdeology | str, int] = Field(default_factory=dict)
    findings: list[SourceDiversityFinding] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_label: SourceDiversityConfidenceLabel = "low"
    cached: bool = False


class SourceDiversityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force_refresh: bool = False


class TimelineEvent(BaseModel):
    id: str
    document_id: str
    timestamp: datetime
    source_name: str
    source_type: str
    title: str
    url: str
    snippet: str | None = None
    event_type: TimelineEventType
    narrative_side: NarrativeSide
    importance_score: float = Field(ge=0.0, le=1.0)
    explanation: str


class TimelineResult(BaseModel):
    investigation_id: str
    plan_snapshot: InvestigationPlan
    timeline_events: list[TimelineEvent] = Field(default_factory=list)
    first_observed_doc_id: str | None = None
    timeline_summary: str
    limitations: list[str] = Field(default_factory=list)
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_label: TimelineConfidenceLabel = "low"
    cached: bool = False


class TimelineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force_refresh: bool = False


class CounterNarrative(BaseModel):
    id: str
    title: str
    summary: str
    canonical_phrase: str | None = None
    related_phrases: list[str] = Field(default_factory=list)
    supporting_document_ids: list[str] = Field(default_factory=list)
    first_observed_doc_id: str | None = None
    relationship_to_main_narrative: CounterNarrativeRelationship
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)


class CounterNarrativeResult(BaseModel):
    investigation_id: str
    plan_snapshot: InvestigationPlan
    counter_narratives: list[CounterNarrative] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_label: CounterNarrativeConfidenceLabel = "low"
    cached: bool = False


class CounterNarrativeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force_refresh: bool = False


class NarrativeFamilyChild(BaseModel):
    id: str
    title: str
    canonical_phrase: str
    branch_type: NarrativeFamilyBranchType = "related"
    related_phrases: list[str] = Field(default_factory=list)
    first_observed_doc_id: str | None = None
    relationship_to_parent: str
    growth_status: NarrativeGrowthStatus = "unknown"
    branch_summary: str
    supporting_document_ids: list[str] = Field(default_factory=list)
    source_count: int = 0
    source_type_count: int = 0
    source_diversity_score: float = Field(default=0.0, ge=0.0, le=1.0)
    growth_score: float = Field(default=0.0, ge=0.0, le=1.0)


class NarrativeMutationStep(BaseModel):
    from_phrase: str
    to_phrase: str
    from_doc_id: str
    to_doc_id: str
    mutation_type: Literal["mutation", "phrase_reuse"]
    similarity_score: float = Field(ge=0.0, le=1.0)
    time_delta_hours: float = Field(ge=0.0)
    source_shift: bool = False
    explanation: str


class NarrativeFamilyResult(BaseModel):
    investigation_id: str
    plan_snapshot: InvestigationPlan
    family_title: str
    parent_frame: str
    summary: str
    child_narratives: list[NarrativeFamilyChild] = Field(default_factory=list)
    active_branch_id: str | None = None
    fastest_growing_child: str | None = None
    broadest_source_diversity_child: str | None = None
    mutation_summary: str = ""
    mutation_trail: list[NarrativeMutationStep] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_label: NarrativeFamilyConfidenceLabel = "unknown"
    generation_method: NarrativeFamilyGenerationMethod = "deterministic"
    cached: bool = False


class NarrativeFamilyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force_refresh: bool = False


class DraftReportSections(BaseModel):
    executive_summary: str
    observed_facts: str
    reasonable_inferences: str
    timeline_summary: str
    counter_narrative_summary: str
    uncertainties: str


class CandidateClaim(BaseModel):
    id: str
    claim_text: str
    claim_type: ClaimType
    supporting_document_ids: list[str] = Field(default_factory=list)
    supporting_evidence_span_ids: list[str] = Field(default_factory=list)
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    caveats: list[str] = Field(default_factory=list)


class AnalystResult(BaseModel):
    investigation_id: str
    plan_snapshot: InvestigationPlan
    draft_report_sections: DraftReportSections
    candidate_claims: list[CandidateClaim] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    recommended_human_checks: list[str] = Field(default_factory=list)
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_label: AnalystConfidenceLabel = "low"
    cached: bool = False


class AnalystRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force_refresh: bool = False


class ReportCitation(BaseModel):
    document_id: str
    source_name: str
    source_type: str
    title: str
    url: str
    published_at: datetime | None = None
    snippet: str | None = None
    relevance_note: str


class ClaimCounterpointPair(BaseModel):
    claim_id: str
    main_claim_text: str
    counter_claim_text: str
    counter_type: ClaimCounterpointType
    relationship_summary: str
    supporting_document_ids: list[str] = Field(default_factory=list)
    counter_document_ids: list[str] = Field(default_factory=list)
    main_receipts: list["ReportCitation"] = Field(default_factory=list)
    counter_receipts: list["ReportCitation"] = Field(default_factory=list)
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    caveats: list[str] = Field(default_factory=list)


class ClaimCounterpointResult(BaseModel):
    investigation_id: str
    plan_snapshot: InvestigationPlan
    pairs: list[ClaimCounterpointPair] = Field(default_factory=list)
    unmatched_claim_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_label: ClaimCounterpointConfidenceLabel = "low"
    cached: bool = False


class ClaimCounterpointRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force_refresh: bool = False


class ReceiptEvidence(BaseModel):
    document_id: str
    source_name: str
    source_type: str
    title: str
    url: str
    published_at: datetime | None = None
    snippet: str | None = None
    evidence_span: str
    support_reason: str
    matched_terms: list[str] = Field(default_factory=list)
    verification_status: ReceiptVerificationStatus = "pending"


class ClaimReceiptReview(BaseModel):
    claim_id: str
    claim_text: str
    claim_side: Literal["main", "counter"]
    support_status: ClaimSupportStatus
    support_summary: str
    supporting_receipts: list[ReceiptEvidence] = Field(default_factory=list)
    contradicting_receipts: list[ReceiptEvidence] = Field(default_factory=list)
    missing_evidence_notes: list[str] = Field(default_factory=list)
    verification_state: ClaimVerificationState
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    caveats: list[str] = Field(default_factory=list)


class ReceiptsResult(BaseModel):
    investigation_id: str
    plan_snapshot: InvestigationPlan
    claim_receipts: list[ClaimReceiptReview] = Field(default_factory=list)
    counter_claim_receipts: list[ClaimReceiptReview] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_label: ReceiptsConfidenceLabel = "low"
    cached: bool = False


class ReceiptsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force_refresh: bool = False


class SoftenedClaim(BaseModel):
    claim_id: str
    original: str
    softened: str
    reason: str


class AgentDebateResult(BaseModel):
    investigation_id: str
    plan_snapshot: InvestigationPlan
    analyst_position: str
    skeptic_response: str
    receipts_check: str
    counter_narrative_note: str
    safety_grounding_decision: str
    final_language_decision: str
    rejected_claims: list[str] = Field(default_factory=list)
    softened_claims: list[SoftenedClaim] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_label: AgentDebateConfidenceLabel = "low"
    cached: bool = False


class AgentDebateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force_refresh: bool = False


class FinalReportClaim(BaseModel):
    claim_id: str
    claim_text: str
    claim_type: ClaimType
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    caveats: list[str] = Field(default_factory=list)
    citations: list[ReportCitation] = Field(default_factory=list)
    support_status: ClaimSupportStatus | None = None
    support_summary: str | None = None
    supporting_receipts: list[ReceiptEvidence] = Field(default_factory=list)
    contradicting_receipts: list[ReceiptEvidence] = Field(default_factory=list)
    missing_evidence_notes: list[str] = Field(default_factory=list)
    verification_state: ClaimVerificationState | None = None
    counterpoint_summary: str | None = None
    counterpoint_type: ClaimCounterpointType | None = None
    counter_citations: list[ReportCitation] = Field(default_factory=list)


class FinalReportSections(BaseModel):
    headline: str
    executive_summary: str
    observed_facts: str
    reasonable_inferences: str
    timeline_summary: str
    counter_narrative_summary: str
    limitations: str
    recommended_human_checks: str


class ConfidenceDimension(BaseModel):
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str


class ConfidenceDimensions(BaseModel):
    coverage_confidence: ConfidenceDimension = Field(default_factory=lambda: ConfidenceDimension(score=0.0, reason="Not yet assessed."))
    chronology_confidence: ConfidenceDimension = Field(default_factory=lambda: ConfidenceDimension(score=0.0, reason="Not yet assessed."))
    contradiction_confidence: ConfidenceDimension = Field(default_factory=lambda: ConfidenceDimension(score=0.0, reason="Not yet assessed."))
    provenance_confidence: ConfidenceDimension = Field(default_factory=lambda: ConfidenceDimension(score=0.0, reason="Not yet assessed."))
    verification_confidence: ConfidenceDimension = Field(default_factory=lambda: ConfidenceDimension(score=0.0, reason="Not yet assessed."))
    synthesis_confidence: ConfidenceDimension = Field(default_factory=lambda: ConfidenceDimension(score=0.0, reason="Not yet assessed."))


class FinalReportResult(BaseModel):
    investigation_id: str
    plan_snapshot: InvestigationPlan
    report_title: str
    report_summary: str
    sections: FinalReportSections
    key_claims: list[FinalReportClaim] = Field(default_factory=list)
    evidence_packet: list[ReportCitation] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    recommended_human_checks: list[str] = Field(default_factory=list)
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_label: ReportConfidenceLabel = "low"
    confidence_dimensions: ConfidenceDimensions | None = None
    cached: bool = False


class FinalReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force_refresh: bool = False


class GapItem(BaseModel):
    gap_id: str
    gap_type: GapType
    severity: GapSeverity
    summary: str
    related_claim_ids: list[str] = Field(default_factory=list)
    recommended_retrieval_lane: RetrievalLane | None = None
    recommended_source_classes: list[str] = Field(default_factory=list)
    follow_up_queries: list[str] = Field(default_factory=list)
    resolved_in_pass: int | None = None
    status: GapStatus = "open"


class GapAnalysisScores(BaseModel):
    chronology_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    source_diversity_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    contradiction_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    primary_source_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    claim_support_density: float = Field(default=0.0, ge=0.0, le=1.0)
    duplication_contamination: float = Field(default=0.0, ge=0.0, le=1.0)
    evergreen_contamination: float = Field(default=0.0, ge=0.0, le=1.0)
    origin_confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class GapAnalysisResult(BaseModel):
    investigation_id: str
    plan_snapshot: InvestigationPlan
    pass_number: int = 1
    scores: GapAnalysisScores = Field(default_factory=GapAnalysisScores)
    missing_evidence: list[GapItem] = Field(default_factory=list)
    weak_claim_ids: list[str] = Field(default_factory=list)
    missing_source_classes: list[str] = Field(default_factory=list)
    retry_priority: list[str] = Field(default_factory=list)
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    cached: bool = False


class SkepticClaimReview(BaseModel):
    claim_id: str
    claim_text: str
    decision: SkepticDecision
    reason: str
    softened_text: str | None = None
    related_gap_ids: list[str] = Field(default_factory=list)


class SkepticReviewResult(BaseModel):
    investigation_id: str
    plan_snapshot: InvestigationPlan
    pass_number: int = 1
    overall_decision: SkepticDecision
    reason: str
    claim_reviews: list[SkepticClaimReview] = Field(default_factory=list)
    retry_instructions: list[str] = Field(default_factory=list)
    stop_condition_status: list[StopCondition] = Field(default_factory=list)
    cached: bool = False


class ClaimLedgerEntry(BaseModel):
    claim_id: str
    claim_text: str
    claim_type: ClaimType
    state: ClaimLedgerState
    supporting_document_ids: list[str] = Field(default_factory=list)
    counter_document_ids: list[str] = Field(default_factory=list)
    verification_state: ClaimVerificationState = "not_available"
    survived_to_report: bool = False
    pass_number: int = 1
    notes: list[str] = Field(default_factory=list)


class ClaimLedgerResult(BaseModel):
    investigation_id: str
    entries: list[ClaimLedgerEntry] = Field(default_factory=list)
    cached: bool = False


class GapLedgerResult(BaseModel):
    investigation_id: str
    entries: list[GapItem] = Field(default_factory=list)
    cached: bool = False


class ProvenanceTraceNode(BaseModel):
    document_id: str
    source_name: str
    published_at: datetime | None = None
    role: Literal["earliest_anchor", "upstream_reference", "official_anchor", "syndicated_copy", "context"] = "context"
    citation_hint: str | None = None


class ProvenanceTraceResult(BaseModel):
    investigation_id: str
    plan_snapshot: InvestigationPlan
    earliest_anchor_document_id: str | None = None
    earliest_anchor_summary: str = ""
    trace_nodes: list[ProvenanceTraceNode] = Field(default_factory=list)
    duplicate_clusters: dict[str, list[str]] = Field(default_factory=dict)
    likely_upstream_source: str | None = None
    official_anchor_document_id: str | None = None
    provenance_limitations: list[str] = Field(default_factory=list)
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    cached: bool = False


class ResearchPassSummary(BaseModel):
    pass_number: int
    lanes_run: list[RetrievalLane] = Field(default_factory=list)
    gaps_opened: list[str] = Field(default_factory=list)
    gaps_resolved: list[str] = Field(default_factory=list)
    skeptic_decision: SkepticDecision | None = None
    notes: list[str] = Field(default_factory=list)


class RetryHistoryEntry(BaseModel):
    pass_number: int
    lane: RetrievalLane
    reason: str
    source_classes: list[str] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)


class EvidenceBudget(BaseModel):
    documents_fetched: int = 0
    source_classes_covered: int = 0
    retries_used: int = 0
    unresolved_gaps_remaining: int = 0


class ResearchLoopRunResult(BaseModel):
    investigation_id: str
    plan_snapshot: InvestigationPlan
    pass_history: list[ResearchPassSummary] = Field(default_factory=list)
    retry_history: list[RetryHistoryEntry] = Field(default_factory=list)
    active_pass: int = 1
    final_decision: ResearchLoopFinalDecision = "insufficient_evidence"
    evidence_budget: EvidenceBudget = Field(default_factory=EvidenceBudget)
    confidence_dimensions: ConfidenceDimensions = Field(default_factory=ConfidenceDimensions)
    warnings: list[str] = Field(default_factory=list)
    cached: bool = False


class RunInvestigationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force_refresh: bool = False


class InvestigationWorkspace(BaseModel):
    investigation_id: str
    query_text: str
    status: InvestigationStatus
    current_stage: InvestigationStage
    created_at: datetime
    updated_at: datetime
    plan: InvestigationPlan | None = None
    retrieval: RetrievalResult | None = None
    retrieved_documents: list[Document] = Field(default_factory=list)
    source_diversity: SourceDiversityResult | None = None
    timeline: TimelineResult | None = None
    counter_narratives: CounterNarrativeResult | None = None
    narrative_family: NarrativeFamilyResult | None = None
    gap_analysis: GapAnalysisResult | None = None
    skeptic_review: SkepticReviewResult | None = None
    claim_ledger: ClaimLedgerResult | None = None
    gap_ledger: GapLedgerResult | None = None
    provenance_trace: ProvenanceTraceResult | None = None
    research_loop: ResearchLoopRunResult | None = None
    analyst: AnalystResult | None = None
    claim_counterpoints: ClaimCounterpointResult | None = None
    receipts: ReceiptsResult | None = None
    agent_debate: AgentDebateResult | None = None
    report: FinalReportResult | None = None


def _default_retrieval_lanes(intent: PlannerIntent) -> list[RetrievalLane]:
    base: list[RetrievalLane] = ["discovery", "corroboration", "contradiction"]
    if intent in {"origin", "spread"}:
        base.append("provenance")
    if intent in {"origin", "spread", "source-ecosystem"}:
        base.append("official")
    if intent in {"spread", "source-ecosystem", "general investigation"}:
        base.append("community")
    return list(dict.fromkeys(base))


def _default_stop_conditions(intent: PlannerIntent) -> list[StopCondition]:
    if intent == "origin":
        return [
            StopCondition(id="origin_anchor", description="Earliest dated anchor in corpus identified."),
            StopCondition(id="origin_retry", description="Attempted earlier-variant or provenance retrieval."),
            StopCondition(id="origin_path", description="Provenance path found or explicitly marked unclear."),
        ]
    if intent == "spread":
        return [
            StopCondition(id="spread_timeline", description="Timeline spans at least two source classes."),
            StopCondition(id="spread_diffusion", description="At least one broader pickup or diffusion indicator found."),
            StopCondition(id="spread_contradiction", description="Contradiction search attempted."),
        ]
    if intent == "counter-narrative":
        return [
            StopCondition(id="counter_cluster", description="At least one direct opposing or corrective cluster identified."),
            StopCondition(id="counter_same_claim", description="Counter-frame addresses the same claim rather than adjacent context."),
        ]
    if intent == "source-ecosystem":
        return [
            StopCondition(id="ecosystem_diversity", description="Source diversity spans multiple publisher types."),
            StopCondition(id="ecosystem_duplication", description="Duplicate or syndication effects have been assessed."),
            StopCondition(id="ecosystem_independence", description="At least one independent non-amplifying source exists."),
        ]
    return [
        StopCondition(id="general_coverage", description="Coverage is sufficient for the main question."),
        StopCondition(id="general_contradiction", description="Competing or contradictory evidence has been searched."),
    ]


def _default_subquestions(plan: InvestigationPlan) -> list[str]:
    phrase = plan.canonical_phrase or plan.topic
    questions = [
        f"What is the strongest evidence directly about {phrase}?",
        f"What competing or contradictory framing exists around {phrase}?",
    ]
    if plan.intent in {"origin", "spread"}:
        questions.append(f"What is the earliest anchored appearance of {phrase} in the retrieved corpus?")
    if plan.intent == "source-ecosystem":
        questions.append(f"What kinds of sources are amplifying or independently covering {phrase}?")
    return questions[:5]
