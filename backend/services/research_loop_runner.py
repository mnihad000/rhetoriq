from __future__ import annotations

from collections import defaultdict

from agents.claim_counterpoint_agent import build_claim_counterpoints
from agents.receipts_agent import build_receipts as build_receipts_agent
from agents.retriever_agent import RetrieverAgent
from config import get_settings
from models.document import Document
from models.investigation import (
    AgentDebateResult,
    ClaimLedgerResult,
    FinalReportResult,
    GapAnalysisResult,
    GapLedgerResult,
    InvestigationPlan,
    InvestigationWorkspace,
    ProvenanceTraceResult,
    ResearchLoopRunResult,
    ResearchPassSummary,
    RetryHistoryEntry,
    RetrievalLane,
    RetrievalResult,
    SkepticReviewResult,
    ConfidenceDimension,
    ConfidenceDimensions,
    EvidenceBudget,
)
from services.agent_debate_builder import build_agent_debate
from services.analyst_builder import build_analyst_result
from services.claim_ledger_builder import build_claim_ledger
from services.counter_narrative_builder import build_counter_narratives
from services.final_report_builder import apply_receipts_annotations, build_final_report
from services.gap_analysis_builder import build_gap_analysis
from services.investigation_repository import InvestigationRepository
from services.narrative_family_builder import build_narrative_family
from services.provenance_trace_builder import build_provenance_trace
from services.skeptic_builder import build_skeptic_review
from services.source_diversity_builder import build_source_diversity
from services.timeline_builder import build_timeline


class InvestigationRunner:
    def __init__(
        self,
        repository: InvestigationRepository,
        retriever: RetrieverAgent,
    ) -> None:
        self._repository = repository
        self._retriever = retriever
        self._settings = get_settings()

    def run(self, investigation_id: str, plan: InvestigationPlan, *, force_refresh: bool = False) -> InvestigationWorkspace:
        if not self._live_models_available():
            run_result = ResearchLoopRunResult(
                investigation_id=investigation_id,
                plan_snapshot=plan,
                final_decision="configuration_missing",
                warnings=["Live Gemini or Groq configuration is required for the supervised research loop."],
                confidence_dimensions=self._empty_confidence_dimensions("Live model configuration missing."),
            )
            self._repository.save_research_loop_run_result(run_result)
            return self._repository.get_investigation_workspace(investigation_id)

        prior_result: RetrievalResult | None = None
        prior_documents: list[Document] | None = None
        pass_history: list[ResearchPassSummary] = []
        retry_history: list[RetryHistoryEntry] = []
        last_gap_analysis: GapAnalysisResult | None = None
        last_skeptic_review: SkepticReviewResult | None = None
        last_provenance: ProvenanceTraceResult | None = None
        last_report: FinalReportResult | None = None
        last_claim_ledger: ClaimLedgerResult | None = None
        max_passes = max(1, self._settings.RESEARCH_LOOP_MAX_PASSES)

        for pass_number in range(1, max_passes + 1):
            lanes, follow_up_queries, requested_source_classes = self._pass_instructions(
                plan,
                last_gap_analysis,
                last_skeptic_review,
            )
            run_plan = plan.model_copy(
                update={
                    "target_source_types": _dedupe([*plan.target_source_types, *requested_source_classes]),
                }
            )
            retrieval = self._retriever.retrieve(
                investigation_id=investigation_id,
                plan=run_plan,
                force_refresh=force_refresh or pass_number > 1,
                lanes=lanes,
                pass_number=pass_number,
                follow_up_queries=follow_up_queries,
                prior_result=prior_result,
                prior_documents=prior_documents,
            )
            documents = self._repository.get_retrieved_documents(investigation_id)
            source_diversity = build_source_diversity(investigation_id, run_plan, retrieval, documents)
            self._repository.save_source_diversity_result(source_diversity)
            timeline = build_timeline(investigation_id, run_plan, retrieval, documents)
            self._repository.save_timeline_result(timeline)
            counter_narratives = build_counter_narratives(investigation_id, run_plan, retrieval, documents)
            self._repository.save_counter_narrative_result(counter_narratives)
            family = build_narrative_family(investigation_id, run_plan, retrieval, documents, timeline, counter_narratives)
            self._repository.save_narrative_family_result(family)
            analyst = build_analyst_result(investigation_id, run_plan, retrieval, documents, timeline, counter_narratives)
            self._repository.save_analyst_result(analyst)
            provenance = build_provenance_trace(investigation_id, run_plan, retrieval, documents)
            self._repository.save_provenance_trace_result(provenance)
            gap_analysis = build_gap_analysis(
                investigation_id,
                run_plan,
                retrieval,
                documents,
                source_diversity,
                timeline,
                counter_narratives,
                analyst,
                provenance,
                pass_number=pass_number,
            )
            self._repository.save_gap_analysis_result(gap_analysis)
            skeptic_review = build_skeptic_review(
                investigation_id,
                run_plan,
                analyst,
                gap_analysis,
                provenance,
                pass_number=pass_number,
                max_passes=max_passes,
            )
            self._repository.save_skeptic_review_result(skeptic_review)

            pass_history.append(
                ResearchPassSummary(
                    pass_number=pass_number,
                    lanes_run=lanes,
                    gaps_opened=[gap.gap_id for gap in gap_analysis.missing_evidence if gap.status == "open"],
                    gaps_resolved=[gap.gap_id for gap in gap_analysis.missing_evidence if gap.status == "resolved"],
                    skeptic_decision=skeptic_review.overall_decision,
                    notes=[skeptic_review.reason],
                )
            )
            for lane, queries in follow_up_queries.items():
                retry_history.append(
                    RetryHistoryEntry(
                        pass_number=pass_number,
                        lane=lane,
                        reason=skeptic_review.reason,
                        source_classes=requested_source_classes,
                        queries=queries,
                    )
                )

            prior_result = retrieval
            prior_documents = documents
            last_gap_analysis = gap_analysis
            last_skeptic_review = skeptic_review
            last_provenance = provenance

            if skeptic_review.overall_decision != "retry_required":
                break

        assert prior_result is not None
        assert prior_documents is not None
        assert last_gap_analysis is not None
        assert last_skeptic_review is not None
        assert last_provenance is not None

        claim_counterpoints = build_claim_counterpoints(
            investigation_id,
            run_plan,
            prior_result,
            prior_documents,
            counter_narratives,
            analyst,
        )
        self._repository.save_claim_counterpoint_result(claim_counterpoints)
        report = build_final_report(
            investigation_id,
            run_plan,
            prior_result,
            prior_documents,
            timeline,
            counter_narratives,
            family,
            analyst,
            claim_counterpoints,
        )
        receipts = build_receipts_agent(
            investigation_id,
            run_plan,
            prior_documents,
            report,
            claim_counterpoints,
            self._verification_map(report, claim_counterpoints),
        )
        self._repository.save_receipts_result(receipts)
        claim_ledger = build_claim_ledger(investigation_id, analyst, receipts, last_skeptic_review)
        self._repository.save_claim_ledger_result(claim_ledger)
        gap_ledger = GapLedgerResult(investigation_id=investigation_id, entries=last_gap_analysis.missing_evidence)
        self._repository.save_gap_ledger_result(gap_ledger)

        final_decision = (
            "completed_with_softening"
            if last_skeptic_review.overall_decision == "pass_with_softening"
            else "completed"
        )
        if last_skeptic_review.overall_decision == "retry_required":
            final_decision = "insufficient_evidence"

        report = apply_receipts_annotations(report, receipts)
        report = report.model_copy(
            update={
                "confidence_dimensions": self._confidence_dimensions(
                    prior_result,
                    timeline,
                    last_gap_analysis,
                    last_provenance,
                    receipts,
                    claim_ledger,
                ),
            }
        )
        report = self._filter_report_claims(report, claim_ledger)
        self._repository.save_final_report_result(report)
        debate = build_agent_debate(
            investigation_id,
            run_plan,
            analyst,
            counter_narratives,
            family,
            claim_counterpoints,
            receipts,
            report,
        )
        self._repository.save_agent_debate_result(debate)

        research_loop = ResearchLoopRunResult(
            investigation_id=investigation_id,
            plan_snapshot=run_plan,
            pass_history=pass_history,
            retry_history=retry_history,
            active_pass=pass_history[-1].pass_number,
            final_decision=final_decision,
            evidence_budget=EvidenceBudget(
                documents_fetched=len(prior_documents),
                source_classes_covered=len(prior_result.coverage_summary.source_type_distribution),
                retries_used=max(0, len(pass_history) - 1),
                unresolved_gaps_remaining=sum(1 for gap in last_gap_analysis.missing_evidence if gap.status == "open"),
            ),
            confidence_dimensions=report.confidence_dimensions or self._empty_confidence_dimensions("No final confidence dimensions computed."),
            warnings=report.limitations,
        )
        self._repository.save_research_loop_run_result(research_loop)
        return self._repository.get_investigation_workspace(investigation_id)

    def _pass_instructions(
        self,
        plan: InvestigationPlan,
        gap_analysis: GapAnalysisResult | None,
        skeptic_review: SkepticReviewResult | None,
    ) -> tuple[list[RetrievalLane], dict[RetrievalLane, list[str]], list[str]]:
        if gap_analysis is None or skeptic_review is None or skeptic_review.overall_decision != "retry_required":
            return list(plan.retrieval_lanes), {}, []
        follow_up_queries: dict[RetrievalLane, list[str]] = defaultdict(list)
        source_classes: list[str] = []
        lanes: list[RetrievalLane] = []
        for gap in gap_analysis.missing_evidence:
            if gap.status != "open" or gap.recommended_retrieval_lane is None:
                continue
            lanes.append(gap.recommended_retrieval_lane)
            follow_up_queries[gap.recommended_retrieval_lane].extend(gap.follow_up_queries)
            source_classes.extend(gap.recommended_source_classes)
        return _dedupe_lanes(lanes) or list(plan.retrieval_lanes), {lane: _dedupe(queries) for lane, queries in follow_up_queries.items()}, _dedupe(source_classes)

    def _verification_map(
        self,
        report: FinalReportResult,
        claim_counterpoints,
    ) -> dict[str, str]:
        doc_ids = self._cited_document_ids(report, claim_counterpoints)
        return {doc_id: "pending" for doc_id in doc_ids}

    def _cited_document_ids(self, report: FinalReportResult, claim_counterpoints) -> list[str]:
        doc_ids: list[str] = []
        for claim in report.key_claims:
            for citation in [*claim.citations, *claim.counter_citations]:
                doc_ids.append(citation.document_id)
        if claim_counterpoints is not None:
            for pair in claim_counterpoints.pairs:
                for citation in [*pair.main_receipts, *pair.counter_receipts]:
                    doc_ids.append(citation.document_id)
        return list(dict.fromkeys(doc_ids))

    def _confidence_dimensions(
        self,
        retrieval: RetrievalResult,
        timeline,
        gap_analysis: GapAnalysisResult,
        provenance: ProvenanceTraceResult,
        receipts,
        claim_ledger: ClaimLedgerResult,
    ) -> ConfidenceDimensions:
        verification_score = max(0.0, receipts.confidence_score - 0.12 * sum(
            1 for review in receipts.claim_receipts if review.verification_state in {"pending", "metadata_mismatch", "unavailable"}
        ))
        synthesis_score = max(
            0.0,
            min(
                retrieval.coverage_summary.total_documents / 10,
                sum(1 for entry in claim_ledger.entries if entry.survived_to_report) / max(1, len(claim_ledger.entries)),
            ) - (0.08 * sum(1 for gap in gap_analysis.missing_evidence if gap.status == "open")),
        )
        return ConfidenceDimensions(
            coverage_confidence=ConfidenceDimension(
                score=_label_score(retrieval.evidence_coverage_confidence),
                reason=f"{retrieval.coverage_summary.total_documents} retrieved documents across {retrieval.coverage_summary.unique_sources} sources.",
            ),
            chronology_confidence=ConfidenceDimension(
                score=timeline.confidence_score,
                reason=timeline.timeline_summary,
            ),
            contradiction_confidence=ConfidenceDimension(
                score=gap_analysis.scores.contradiction_coverage,
                reason=f"{len(gap_analysis.missing_evidence)} open gap(s) remain after contradiction review.",
            ),
            provenance_confidence=ConfidenceDimension(
                score=provenance.confidence_score,
                reason=provenance.earliest_anchor_summary,
            ),
            verification_confidence=ConfidenceDimension(
                score=round(min(max(verification_score, 0.0), 0.95), 3),
                reason="Verification confidence is penalized when supporting receipts remain pending or mismatched.",
            ),
            synthesis_confidence=ConfidenceDimension(
                score=round(min(max(synthesis_score, 0.0), 0.95), 3),
                reason="Synthesis confidence is capped by unresolved gaps and claim survival.",
            ),
        )

    def _filter_report_claims(self, report: FinalReportResult, claim_ledger: ClaimLedgerResult) -> FinalReportResult:
        ledger_by_claim_id = {entry.claim_id: entry for entry in claim_ledger.entries}
        filtered_claims = [
            claim
            for claim in report.key_claims
            if ledger_by_claim_id.get(claim.claim_id) is None
            or ledger_by_claim_id[claim.claim_id].state not in {"contradicted", "rejected", "unresolved"}
        ]
        return report.model_copy(update={"key_claims": filtered_claims})

    def _live_models_available(self) -> bool:
        return (not self._settings.DEMO_MODE) and bool(
            self._settings.GEMINI_API_KEY or self._settings.GROQ_API_KEY
        )

    def _empty_confidence_dimensions(self, reason: str) -> ConfidenceDimensions:
        return ConfidenceDimensions(
            coverage_confidence=ConfidenceDimension(score=0.0, reason=reason),
            chronology_confidence=ConfidenceDimension(score=0.0, reason=reason),
            contradiction_confidence=ConfidenceDimension(score=0.0, reason=reason),
            provenance_confidence=ConfidenceDimension(score=0.0, reason=reason),
            verification_confidence=ConfidenceDimension(score=0.0, reason=reason),
            synthesis_confidence=ConfidenceDimension(score=0.0, reason=reason),
        )


def _dedupe(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(normalized)
    return output


def _dedupe_lanes(values: list[RetrievalLane]) -> list[RetrievalLane]:
    output: list[RetrievalLane] = []
    seen: set[RetrievalLane] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def _label_score(label: str) -> float:
    if label == "high":
        return 0.82
    if label == "medium":
        return 0.58
    return 0.32
