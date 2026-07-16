from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone

from models.document import Document
from models.investigation import (
    AgentDebateResult,
    AnalystResult,
    ClaimCounterpointResult,
    ClaimLedgerResult,
    CounterNarrativeResult,
    FinalReportResult,
    GapAnalysisResult,
    GapLedgerResult,
    InvestigationPlan,
    RecentInvestigationSummary,
    ProvenanceTraceResult,
    ResearchLoopRunResult,
    InvestigationWorkspace,
    NarrativeFamilyResult,
    ReceiptsResult,
    RetrievalResult,
    SkepticReviewResult,
    SourceDiversityResult,
    TimelineResult,
)


class InvestigationRepository:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._ensure_parent_dir()
        self._init_schema()

    def save_plan(self, investigation_id: str, query_text: str, plan: InvestigationPlan) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO investigations (
                    investigation_id, query_text, status, current_stage, plan_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(investigation_id) DO UPDATE SET
                    query_text=excluded.query_text,
                    status=excluded.status,
                    current_stage=excluded.current_stage,
                    plan_json=excluded.plan_json,
                    updated_at=excluded.updated_at
                """,
                (
                    investigation_id,
                    query_text,
                    "planning_completed",
                    "planner",
                    plan.model_dump_json(),
                    now,
                    now,
                ),
            )

    def get_plan(self, investigation_id: str) -> InvestigationPlan | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT plan_json FROM investigations WHERE investigation_id = ?",
                (investigation_id,),
            ).fetchone()
        if not row or not row["plan_json"]:
            return None
        return InvestigationPlan.model_validate_json(row["plan_json"])

    def investigation_exists(self, investigation_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM investigations WHERE investigation_id = ?",
                (investigation_id,),
            ).fetchone()
        return row is not None

    def get_investigation_workspace(
        self,
        investigation_id: str,
    ) -> InvestigationWorkspace | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT investigation_id, query_text, status, current_stage, created_at, updated_at
                FROM investigations
                WHERE investigation_id = ?
                """,
                (investigation_id,),
            ).fetchone()

        if not row:
            return None

        return InvestigationWorkspace(
            investigation_id=row["investigation_id"],
            query_text=row["query_text"],
            status=row["status"],
            current_stage=row["current_stage"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            plan=self.get_plan(investigation_id),
            retrieval=self.get_retrieval_result(investigation_id),
            retrieved_documents=self.get_retrieved_documents(investigation_id),
            source_diversity=self.get_source_diversity_result(investigation_id),
            timeline=self.get_timeline_result(investigation_id),
            counter_narratives=self.get_counter_narrative_result(investigation_id),
            narrative_family=self.get_narrative_family_result(investigation_id),
            gap_analysis=self.get_gap_analysis_result(investigation_id),
            skeptic_review=self.get_skeptic_review_result(investigation_id),
            claim_ledger=self.get_claim_ledger_result(investigation_id),
            gap_ledger=self.get_gap_ledger_result(investigation_id),
            provenance_trace=self.get_provenance_trace_result(investigation_id),
            research_loop=self.get_research_loop_run_result(investigation_id),
            analyst=self.get_analyst_result(investigation_id),
            claim_counterpoints=self.get_claim_counterpoint_result(investigation_id),
            receipts=self.get_receipts_result(investigation_id),
            agent_debate=self.get_agent_debate_result(investigation_id),
            report=self.get_final_report_result(investigation_id),
        )

    def get_recent_investigations(self, limit: int = 6) -> list[RecentInvestigationSummary]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT investigation_id, query_text, status, updated_at, plan_json
                FROM investigations
                WHERE investigation_id LIKE 'inv_%' AND status != 'planning_completed'
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        results: list[RecentInvestigationSummary] = []
        for row in rows:
            investigation_id = row["investigation_id"]
            plan = (
                InvestigationPlan.model_validate_json(row["plan_json"])
                if row["plan_json"]
                else None
            )
            retrieval = self.get_retrieval_result(investigation_id)
            report = self.get_final_report_result(investigation_id)
            analyst = self.get_analyst_result(investigation_id)
            timeline = self.get_timeline_result(investigation_id)
            receipts = self.get_receipts_result(investigation_id)

            report_title = (
                report.report_title
                if report is not None
                else self._derive_recent_title(plan, row["query_text"])
            )
            report_summary = None
            if report is not None:
                report_summary = report.report_summary
            elif analyst is not None:
                report_summary = analyst.draft_report_sections.executive_summary
            elif timeline is not None:
                report_summary = timeline.timeline_summary

            results.append(
                RecentInvestigationSummary(
                    investigation_id=investigation_id,
                    query_text=row["query_text"],
                    status=row["status"],
                    updated_at=datetime.fromisoformat(row["updated_at"]),
                    report_title=report_title,
                    report_summary=report_summary,
                    receipt_count=self._count_receipts(receipts),
                    source_count=self._count_sources(retrieval),
                )
            )
        return results

    def save_retrieval_result(self, result: RetrievalResult, documents: list[Document]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE investigations
                SET status = ?, current_stage = ?, updated_at = ?
                WHERE investigation_id = ?
                """,
                ("retrieval_completed", "retriever", now, result.investigation_id),
            )
            conn.execute(
                """
                INSERT INTO retrieval_results (investigation_id, result_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(investigation_id) DO UPDATE SET
                    result_json=excluded.result_json,
                    updated_at=excluded.updated_at
                """,
                (result.investigation_id, result.model_dump_json(), now, now),
            )
            conn.execute(
                "DELETE FROM retrieved_documents WHERE investigation_id = ?",
                (result.investigation_id,),
            )
            conn.execute(
                "DELETE FROM retrieval_rounds WHERE investigation_id = ?",
                (result.investigation_id,),
            )
            conn.execute(
                "DELETE FROM duplicate_candidates WHERE investigation_id = ?",
                (result.investigation_id,),
            )
            conn.execute(
                "DELETE FROM search_results WHERE investigation_id = ?",
                (result.investigation_id,),
            )
            for doc in documents:
                conn.execute(
                    """
                    INSERT INTO retrieved_documents (investigation_id, doc_id, document_json, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (result.investigation_id, doc.id, doc.model_dump_json(), now),
                )
            for retrieval_round in result.search_rounds:
                conn.execute(
                    """
                    INSERT INTO retrieval_rounds (investigation_id, round_number, round_json, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        result.investigation_id,
                        retrieval_round.round_number,
                        retrieval_round.model_dump_json(),
                        now,
                    ),
                )
            for duplicate in result.possible_duplicate_pairs:
                conn.execute(
                    """
                    INSERT INTO duplicate_candidates (
                        investigation_id, left_doc_id, right_doc_id, duplicate_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        result.investigation_id,
                        duplicate.left_doc_id,
                        duplicate.right_doc_id,
                        duplicate.model_dump_json(),
                        now,
                    ),
                )

    def save_search_results(self, investigation_id: str, round_number: int, results: list[dict]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            for result in results:
                conn.execute(
                    """
                    INSERT INTO search_results (investigation_id, round_number, url, result_json, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(investigation_id, round_number, url) DO UPDATE SET
                        result_json=excluded.result_json
                    """,
                    (
                        investigation_id,
                        round_number,
                        result["url"],
                        json.dumps(result),
                        now,
                    ),
                )

    def get_retrieval_result(self, investigation_id: str) -> RetrievalResult | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT result_json FROM retrieval_results WHERE investigation_id = ?",
                (investigation_id,),
            ).fetchone()
        if not row:
            return None
        return RetrievalResult.model_validate_json(row["result_json"])

    def get_retrieved_documents(self, investigation_id: str) -> list[Document]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT document_json
                FROM retrieved_documents
                WHERE investigation_id = ?
                ORDER BY doc_id
                """,
                (investigation_id,),
            ).fetchall()
        return [Document.model_validate_json(row["document_json"]) for row in rows]

    def save_timeline_result(self, result: TimelineResult) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE investigations
                SET status = ?, current_stage = ?, updated_at = ?
                WHERE investigation_id = ?
                """,
                ("timeline_completed", "timeline", now, result.investigation_id),
            )
            conn.execute(
                """
                INSERT INTO timeline_results (investigation_id, result_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(investigation_id) DO UPDATE SET
                    result_json=excluded.result_json,
                    updated_at=excluded.updated_at
                """,
                (result.investigation_id, result.model_dump_json(), now, now),
            )

    def get_timeline_result(self, investigation_id: str) -> TimelineResult | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT result_json FROM timeline_results WHERE investigation_id = ?",
                (investigation_id,),
            ).fetchone()
        if not row:
            return None
        return TimelineResult.model_validate_json(row["result_json"])

    def save_source_diversity_result(self, result: SourceDiversityResult) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE investigations
                SET status = ?, current_stage = ?, updated_at = ?
                WHERE investigation_id = ?
                """,
                ("source_diversity_completed", "source_diversity", now, result.investigation_id),
            )
            conn.execute(
                """
                INSERT INTO source_diversity_results (investigation_id, result_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(investigation_id) DO UPDATE SET
                    result_json=excluded.result_json,
                    updated_at=excluded.updated_at
                """,
                (result.investigation_id, result.model_dump_json(), now, now),
            )

    def get_source_diversity_result(self, investigation_id: str) -> SourceDiversityResult | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT result_json FROM source_diversity_results WHERE investigation_id = ?",
                (investigation_id,),
            ).fetchone()
        if not row:
            return None
        return SourceDiversityResult.model_validate_json(row["result_json"])

    def save_counter_narrative_result(self, result: CounterNarrativeResult) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE investigations
                SET status = ?, current_stage = ?, updated_at = ?
                WHERE investigation_id = ?
                """,
                ("counter_narrative_completed", "counter_narrative", now, result.investigation_id),
            )
            conn.execute(
                """
                INSERT INTO counter_narrative_results (investigation_id, result_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(investigation_id) DO UPDATE SET
                    result_json=excluded.result_json,
                    updated_at=excluded.updated_at
                """,
                (result.investigation_id, result.model_dump_json(), now, now),
            )

    def get_counter_narrative_result(self, investigation_id: str) -> CounterNarrativeResult | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT result_json FROM counter_narrative_results WHERE investigation_id = ?",
                (investigation_id,),
            ).fetchone()
        if not row:
            return None
        return CounterNarrativeResult.model_validate_json(row["result_json"])

    def save_narrative_family_result(self, result: NarrativeFamilyResult) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE investigations
                SET status = ?, current_stage = ?, updated_at = ?
                WHERE investigation_id = ?
                """,
                ("narrative_family_completed", "narrative_family", now, result.investigation_id),
            )
            conn.execute(
                """
                INSERT INTO narrative_family_results (investigation_id, result_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(investigation_id) DO UPDATE SET
                    result_json=excluded.result_json,
                    updated_at=excluded.updated_at
                """,
                (result.investigation_id, result.model_dump_json(), now, now),
            )

    def get_narrative_family_result(self, investigation_id: str) -> NarrativeFamilyResult | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT result_json FROM narrative_family_results WHERE investigation_id = ?",
                (investigation_id,),
            ).fetchone()
        if not row:
            return None
        return NarrativeFamilyResult.model_validate_json(row["result_json"])

    def save_gap_analysis_result(self, result: GapAnalysisResult) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE investigations
                SET status = ?, current_stage = ?, updated_at = ?
                WHERE investigation_id = ?
                """,
                ("gap_analysis_completed", "gap_analysis", now, result.investigation_id),
            )
            conn.execute(
                """
                INSERT INTO gap_analysis_results (investigation_id, result_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(investigation_id) DO UPDATE SET
                    result_json=excluded.result_json,
                    updated_at=excluded.updated_at
                """,
                (result.investigation_id, result.model_dump_json(), now, now),
            )

    def get_gap_analysis_result(self, investigation_id: str) -> GapAnalysisResult | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT result_json FROM gap_analysis_results WHERE investigation_id = ?",
                (investigation_id,),
            ).fetchone()
        if not row:
            return None
        return GapAnalysisResult.model_validate_json(row["result_json"])

    def save_skeptic_review_result(self, result: SkepticReviewResult) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE investigations
                SET status = ?, current_stage = ?, updated_at = ?
                WHERE investigation_id = ?
                """,
                ("skeptic_completed", "skeptic", now, result.investigation_id),
            )
            conn.execute(
                """
                INSERT INTO skeptic_review_results (investigation_id, result_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(investigation_id) DO UPDATE SET
                    result_json=excluded.result_json,
                    updated_at=excluded.updated_at
                """,
                (result.investigation_id, result.model_dump_json(), now, now),
            )

    def get_skeptic_review_result(self, investigation_id: str) -> SkepticReviewResult | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT result_json FROM skeptic_review_results WHERE investigation_id = ?",
                (investigation_id,),
            ).fetchone()
        if not row:
            return None
        return SkepticReviewResult.model_validate_json(row["result_json"])

    def save_claim_ledger_result(self, result: ClaimLedgerResult) -> None:
        self._save_json_artifact("claim_ledger_results", result.investigation_id, result.model_dump_json())

    def get_claim_ledger_result(self, investigation_id: str) -> ClaimLedgerResult | None:
        return self._load_json_artifact("claim_ledger_results", investigation_id, ClaimLedgerResult)

    def save_gap_ledger_result(self, result: GapLedgerResult) -> None:
        self._save_json_artifact("gap_ledger_results", result.investigation_id, result.model_dump_json())

    def get_gap_ledger_result(self, investigation_id: str) -> GapLedgerResult | None:
        return self._load_json_artifact("gap_ledger_results", investigation_id, GapLedgerResult)

    def save_provenance_trace_result(self, result: ProvenanceTraceResult) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE investigations
                SET status = ?, current_stage = ?, updated_at = ?
                WHERE investigation_id = ?
                """,
                ("provenance_completed", "provenance", now, result.investigation_id),
            )
            conn.execute(
                """
                INSERT INTO provenance_trace_results (investigation_id, result_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(investigation_id) DO UPDATE SET
                    result_json=excluded.result_json,
                    updated_at=excluded.updated_at
                """,
                (result.investigation_id, result.model_dump_json(), now, now),
            )

    def get_provenance_trace_result(self, investigation_id: str) -> ProvenanceTraceResult | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT result_json FROM provenance_trace_results WHERE investigation_id = ?",
                (investigation_id,),
            ).fetchone()
        if not row:
            return None
        return ProvenanceTraceResult.model_validate_json(row["result_json"])

    def save_research_loop_run_result(self, result: ResearchLoopRunResult) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE investigations
                SET status = ?, current_stage = ?, updated_at = ?
                WHERE investigation_id = ?
                """,
                ("research_loop_completed", "research_loop", now, result.investigation_id),
            )
            conn.execute(
                """
                INSERT INTO research_loop_results (investigation_id, result_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(investigation_id) DO UPDATE SET
                    result_json=excluded.result_json,
                    updated_at=excluded.updated_at
                """,
                (result.investigation_id, result.model_dump_json(), now, now),
            )

    def get_research_loop_run_result(self, investigation_id: str) -> ResearchLoopRunResult | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT result_json FROM research_loop_results WHERE investigation_id = ?",
                (investigation_id,),
            ).fetchone()
        if not row:
            return None
        return ResearchLoopRunResult.model_validate_json(row["result_json"])

    def save_analyst_result(self, result: AnalystResult) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE investigations
                SET status = ?, current_stage = ?, updated_at = ?
                WHERE investigation_id = ?
                """,
                ("analyst_completed", "analyst", now, result.investigation_id),
            )
            conn.execute(
                """
                INSERT INTO analyst_results (investigation_id, result_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(investigation_id) DO UPDATE SET
                    result_json=excluded.result_json,
                    updated_at=excluded.updated_at
                """,
                (result.investigation_id, result.model_dump_json(), now, now),
            )

    def get_analyst_result(self, investigation_id: str) -> AnalystResult | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT result_json FROM analyst_results WHERE investigation_id = ?",
                (investigation_id,),
            ).fetchone()
        if not row:
            return None
        return AnalystResult.model_validate_json(row["result_json"])

    def save_final_report_result(self, result: FinalReportResult, *, update_stage: bool = True) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            if update_stage:
                conn.execute(
                    """
                    UPDATE investigations
                    SET status = ?, current_stage = ?, updated_at = ?
                    WHERE investigation_id = ?
                    """,
                    ("report_completed", "report", now, result.investigation_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE investigations
                    SET updated_at = ?
                    WHERE investigation_id = ?
                    """,
                    (now, result.investigation_id),
                )
            conn.execute(
                """
                INSERT INTO final_report_results (investigation_id, result_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(investigation_id) DO UPDATE SET
                    result_json=excluded.result_json,
                    updated_at=excluded.updated_at
                """,
                (result.investigation_id, result.model_dump_json(), now, now),
            )

    def save_claim_counterpoint_result(self, result: ClaimCounterpointResult) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE investigations
                SET status = ?, current_stage = ?, updated_at = ?
                WHERE investigation_id = ?
                """,
                ("claim_counterpoint_completed", "claim_counterpoint", now, result.investigation_id),
            )
            conn.execute(
                """
                INSERT INTO claim_counterpoint_results (investigation_id, result_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(investigation_id) DO UPDATE SET
                    result_json=excluded.result_json,
                    updated_at=excluded.updated_at
                """,
                (result.investigation_id, result.model_dump_json(), now, now),
            )

    def get_claim_counterpoint_result(self, investigation_id: str) -> ClaimCounterpointResult | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT result_json FROM claim_counterpoint_results WHERE investigation_id = ?",
                (investigation_id,),
            ).fetchone()
        if not row:
            return None
        return ClaimCounterpointResult.model_validate_json(row["result_json"])

    def save_receipts_result(self, result: ReceiptsResult) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE investigations
                SET status = ?, current_stage = ?, updated_at = ?
                WHERE investigation_id = ?
                """,
                ("receipts_completed", "receipts", now, result.investigation_id),
            )
            conn.execute(
                """
                INSERT INTO receipts_results (investigation_id, result_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(investigation_id) DO UPDATE SET
                    result_json=excluded.result_json,
                    updated_at=excluded.updated_at
                """,
                (result.investigation_id, result.model_dump_json(), now, now),
            )

    def get_receipts_result(self, investigation_id: str) -> ReceiptsResult | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT result_json FROM receipts_results WHERE investigation_id = ?",
                (investigation_id,),
            ).fetchone()
        if not row:
            return None
        return ReceiptsResult.model_validate_json(row["result_json"])

    def save_agent_debate_result(self, result: AgentDebateResult) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE investigations
                SET status = ?, current_stage = ?, updated_at = ?
                WHERE investigation_id = ?
                """,
                ("agent_debate_completed", "agent_debate", now, result.investigation_id),
            )
            conn.execute(
                """
                INSERT INTO agent_debate_results (investigation_id, result_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(investigation_id) DO UPDATE SET
                    result_json=excluded.result_json,
                    updated_at=excluded.updated_at
                """,
                (result.investigation_id, result.model_dump_json(), now, now),
            )

    def get_agent_debate_result(self, investigation_id: str) -> AgentDebateResult | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT result_json FROM agent_debate_results WHERE investigation_id = ?",
                (investigation_id,),
            ).fetchone()
        if not row:
            return None
        return AgentDebateResult.model_validate_json(row["result_json"])

    def get_final_report_result(self, investigation_id: str) -> FinalReportResult | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT result_json FROM final_report_results WHERE investigation_id = ?",
                (investigation_id,),
            ).fetchone()
        if not row:
            return None
        return FinalReportResult.model_validate_json(row["result_json"])

    def _save_json_artifact(self, table_name: str, investigation_id: str, payload: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO {table_name} (investigation_id, result_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(investigation_id) DO UPDATE SET
                    result_json=excluded.result_json,
                    updated_at=excluded.updated_at
                """,
                (investigation_id, payload, now, now),
            )
            conn.execute(
                """
                UPDATE investigations
                SET updated_at = ?
                WHERE investigation_id = ?
                """,
                (now, investigation_id),
            )

    def _load_json_artifact(self, table_name: str, investigation_id: str, model_cls):
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT result_json FROM {table_name} WHERE investigation_id = ?",
                (investigation_id,),
            ).fetchone()
        if not row:
            return None
        return model_cls.model_validate_json(row["result_json"])

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_parent_dir(self) -> None:
        if self._db_path == ":memory:":
            return
        parent = os.path.dirname(self._db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    def _count_receipts(self, receipts: ReceiptsResult | None) -> int:
        if receipts is None:
            return 0

        seen: set[str] = set()
        for review in [*receipts.claim_receipts, *receipts.counter_claim_receipts]:
            for item in [*review.supporting_receipts, *review.contradicting_receipts]:
                seen.add(f"{review.claim_id}:{item.document_id}")
        return len(seen)

    def _count_sources(self, retrieval: RetrievalResult | None) -> int:
        if retrieval is None:
            return 0
        if retrieval.coverage_summary.total_documents:
            return retrieval.coverage_summary.total_documents
        return len(retrieval.retrieved_document_ids)

    def _derive_recent_title(self, plan: InvestigationPlan | None, query_text: str) -> str:
        if plan is not None and plan.topic.strip():
            return f"{plan.topic.strip().title()} Investigation"
        return query_text.strip() or "Investigation"

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS investigations (
                    investigation_id TEXT PRIMARY KEY,
                    query_text TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_stage TEXT NOT NULL,
                    plan_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS retrieval_results (
                    investigation_id TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS retrieved_documents (
                    investigation_id TEXT NOT NULL,
                    doc_id TEXT NOT NULL,
                    document_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (investigation_id, doc_id)
                );

                CREATE TABLE IF NOT EXISTS retrieval_rounds (
                    investigation_id TEXT NOT NULL,
                    round_number INTEGER NOT NULL,
                    round_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (investigation_id, round_number)
                );

                CREATE TABLE IF NOT EXISTS duplicate_candidates (
                    investigation_id TEXT NOT NULL,
                    left_doc_id TEXT NOT NULL,
                    right_doc_id TEXT NOT NULL,
                    duplicate_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (investigation_id, left_doc_id, right_doc_id)
                );

                CREATE TABLE IF NOT EXISTS search_results (
                    investigation_id TEXT NOT NULL,
                    round_number INTEGER NOT NULL,
                    url TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (investigation_id, round_number, url)
                );

                CREATE TABLE IF NOT EXISTS timeline_results (
                    investigation_id TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS source_diversity_results (
                    investigation_id TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS counter_narrative_results (
                    investigation_id TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS narrative_family_results (
                    investigation_id TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS analyst_results (
                    investigation_id TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS gap_analysis_results (
                    investigation_id TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS skeptic_review_results (
                    investigation_id TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS claim_ledger_results (
                    investigation_id TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS gap_ledger_results (
                    investigation_id TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS provenance_trace_results (
                    investigation_id TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS research_loop_results (
                    investigation_id TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS claim_counterpoint_results (
                    investigation_id TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS receipts_results (
                    investigation_id TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_debate_results (
                    investigation_id TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS final_report_results (
                    investigation_id TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
