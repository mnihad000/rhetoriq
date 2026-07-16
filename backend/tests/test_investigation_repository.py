import sqlite3
from datetime import datetime, timezone

from models.document import Document
from models.investigation import (
    AnalystResult,
    DraftReportSections,
    FinalReportResult,
    FinalReportSections,
    InvestigationPlan,
    InvestigationPlanTimeWindow,
    ReceiptEvidence,
    ReceiptsResult,
    RetrievalResult,
    SourceDiversityResult,
    TimelineResult,
)
from services.investigation_repository import InvestigationRepository


def _plan(topic: str = "hidden energy tax") -> InvestigationPlan:
    return InvestigationPlan(
        query_text=f"Trace {topic}",
        topic=topic,
        canonical_phrase=topic,
        intent="origin",
        entities=topic.split(),
        search_queries=[topic],
        semantic_queries=[f"Investigate {topic}"],
        target_source_types=["local_news", "national_news"],
        requested_outputs=["timeline", "source_diversity", "receipts"],
        time_window=InvestigationPlanTimeWindow(label="all_time"),
        retrieval_mode="broad",
        risk_notes=[],
        uncertainty_requirements=[],
    )


def _documents() -> list[Document]:
    return [
        Document(
            id="doc_1",
            source_id="domain:springfieldgazette.com",
            source_name="springfieldgazette.com",
            source_type="local_news",
            url="https://springfieldgazette.com/doc_1",
            title="Hidden energy tax appears locally",
            published_at=datetime(2026, 6, 1, 8, 0, tzinfo=timezone.utc),
            collected_at=datetime(2026, 6, 19, tzinfo=timezone.utc),
            text="Hidden energy tax appears locally",
            snippet="Hidden energy tax appears locally",
            language="en",
            content_type="article",
            geographic_scope="local",
            entities=[],
            phrases=["hidden energy tax"],
            metadata={"retrieval_score": 5.0},
        ),
        Document(
            id="doc_2",
            source_id="domain:reuters.com",
            source_name="reuters.com",
            source_type="national_news",
            url="https://reuters.com/doc_2",
            title="National coverage follows",
            published_at=datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc),
            collected_at=datetime(2026, 6, 19, tzinfo=timezone.utc),
            text="National coverage follows the hidden energy tax claim",
            snippet="National coverage follows the hidden energy tax claim",
            language="en",
            content_type="article",
            geographic_scope="national",
            entities=[],
            phrases=["hidden energy tax"],
            metadata={"retrieval_score": 4.5},
        ),
    ]


def _retrieval(investigation_id: str, plan: InvestigationPlan) -> RetrievalResult:
    return RetrievalResult(
        investigation_id=investigation_id,
        plan_snapshot=plan,
        retrieved_document_ids=["doc_1", "doc_2"],
        high_relevance_document_ids=["doc_1", "doc_2"],
        main_narrative_document_ids=["doc_1", "doc_2"],
        counter_narrative_candidate_ids=[],
        context_document_ids=[],
        warnings=[],
        evidence_coverage_confidence="medium",
    )


def _timeline(investigation_id: str, plan: InvestigationPlan, summary: str) -> TimelineResult:
    return TimelineResult(
        investigation_id=investigation_id,
        plan_snapshot=plan,
        timeline_events=[],
        first_observed_doc_id="doc_1",
        timeline_summary=summary,
        limitations=[],
        confidence_score=0.4,
        confidence_label="medium",
    )


def _analyst(investigation_id: str, plan: InvestigationPlan, summary: str) -> AnalystResult:
    return AnalystResult(
        investigation_id=investigation_id,
        plan_snapshot=plan,
        draft_report_sections=DraftReportSections(
            executive_summary=summary,
            observed_facts="facts",
            reasonable_inferences="inferences",
            timeline_summary="timeline",
            counter_narrative_summary="counter",
            uncertainties="uncertain",
        ),
        candidate_claims=[],
        limitations=[],
        recommended_human_checks=[],
        confidence_score=0.5,
        confidence_label="medium",
    )


def _report(investigation_id: str, plan: InvestigationPlan) -> FinalReportResult:
    return FinalReportResult(
        investigation_id=investigation_id,
        plan_snapshot=plan,
        report_title="Hidden Energy Tax Investigation",
        report_summary="Final persisted report summary.",
        sections=FinalReportSections(
            headline="headline",
            executive_summary="summary",
            observed_facts="facts",
            reasonable_inferences="inferences",
            timeline_summary="timeline",
            counter_narrative_summary="counter",
            limitations="limitations",
            recommended_human_checks="checks",
        ),
        key_claims=[],
        evidence_packet=[],
        limitations=[],
        recommended_human_checks=[],
        confidence_score=0.6,
        confidence_label="medium",
    )


def _receipts(investigation_id: str, plan: InvestigationPlan) -> ReceiptsResult:
    receipt = ReceiptEvidence(
        document_id="doc_1",
        source_name="springfieldgazette.com",
        source_type="local_news",
        title="Hidden energy tax appears locally",
        url="https://springfieldgazette.com/doc_1",
        published_at=datetime(2026, 6, 1, 8, 0, tzinfo=timezone.utc),
        snippet="Hidden energy tax appears locally",
        evidence_span="Hidden energy tax appears locally",
        support_reason="Supports the claim.",
        matched_terms=["hidden energy tax"],
        verification_status="pending",
    )
    return ReceiptsResult(
        investigation_id=investigation_id,
        plan_snapshot=plan,
        claim_receipts=[
            {
                "claim_id": "claim_1",
                "claim_text": "Hidden energy tax appeared locally first.",
                "claim_side": "main",
                "support_status": "supported",
                "support_summary": "Supported",
                "supporting_receipts": [receipt],
                "contradicting_receipts": [],
                "missing_evidence_notes": [],
                "verification_state": "pending",
                "confidence_score": 0.6,
                "caveats": [],
            }
        ],
        counter_claim_receipts=[],
        limitations=[],
        confidence_score=0.6,
        confidence_label="medium",
    )


def _set_updated_at(repo: InvestigationRepository, investigation_id: str, value: str) -> None:
    with sqlite3.connect(repo._db_path) as conn:  # noqa: SLF001 - test helper
        conn.execute(
            "UPDATE investigations SET updated_at = ? WHERE investigation_id = ?",
            (value, investigation_id),
        )


def test_get_recent_investigations_filters_sorts_and_falls_back(tmp_path):
    repo = InvestigationRepository(str(tmp_path / "investigations.sqlite3"))

    planner_only_plan = _plan("planner draft")
    repo.save_plan("inv_planner", planner_only_plan.query_text, planner_only_plan)

    derived_plan = _plan("public housing narrative")
    repo.save_plan("inv_derived", derived_plan.query_text, derived_plan)
    repo.save_retrieval_result(_retrieval("inv_derived", derived_plan), _documents())
    repo.save_timeline_result(_timeline("inv_derived", derived_plan, "Timeline-only summary."))
    repo.save_analyst_result(_analyst("inv_derived", derived_plan, "Analyst fallback summary."))

    report_plan = _plan("hidden energy tax")
    repo.save_plan("inv_report", report_plan.query_text, report_plan)
    repo.save_retrieval_result(_retrieval("inv_report", report_plan), _documents())
    repo.save_timeline_result(_timeline("inv_report", report_plan, "Older timeline summary."))
    repo.save_source_diversity_result(
        SourceDiversityResult(
          investigation_id="inv_report",
          plan_snapshot=report_plan,
          total_documents=2,
          classified_documents=2,
          source_type_distribution={"local_news": 1, "national_news": 1},
          geographic_distribution={"local": 1, "national": 1},
          institution_distribution={"media": 2},
          content_form_distribution={"original_reporting": 2},
          ideology_distribution={"unknown": 2},
          findings=[],
          limitations=[],
          confidence_score=0.5,
          confidence_label="medium",
        )
    )
    repo.save_receipts_result(_receipts("inv_report", report_plan))
    repo.save_final_report_result(_report("inv_report", report_plan))

    demo_plan = _plan("seeded demo")
    repo.save_plan("demo", demo_plan.query_text, demo_plan)
    repo.save_retrieval_result(_retrieval("demo", demo_plan), _documents())

    _set_updated_at(repo, "inv_derived", "2026-06-20T10:00:00+00:00")
    _set_updated_at(repo, "inv_report", "2026-06-20T11:00:00+00:00")
    _set_updated_at(repo, "inv_planner", "2026-06-20T12:00:00+00:00")
    _set_updated_at(repo, "demo", "2026-06-20T13:00:00+00:00")

    results = repo.get_recent_investigations(limit=5)

    assert [item.investigation_id for item in results] == ["inv_report", "inv_derived"]

    newest = results[0]
    assert newest.report_title == "Hidden Energy Tax Investigation"
    assert newest.report_summary == "Final persisted report summary."
    assert newest.receipt_count == 1
    assert newest.source_count == 2

    fallback = results[1]
    assert fallback.report_title == "Public Housing Narrative Investigation"
    assert fallback.report_summary == "Analyst fallback summary."
    assert fallback.receipt_count == 0
    assert fallback.source_count == 2


def test_get_recent_investigations_respects_limit(tmp_path):
    repo = InvestigationRepository(str(tmp_path / "investigations.sqlite3"))

    for index in range(3):
        investigation_id = f"inv_{index}"
        plan = _plan(f"topic {index}")
        repo.save_plan(investigation_id, plan.query_text, plan)
        repo.save_retrieval_result(_retrieval(investigation_id, plan), _documents())
        _set_updated_at(repo, investigation_id, f"2026-06-20T0{index}:00:00+00:00")

    results = repo.get_recent_investigations(limit=2)
    assert len(results) == 2
