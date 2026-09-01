from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from config import get_settings
from migrations.runner import run_migrations
from models.document import Document
from services.database import connect, ensure_parent_dir, is_postgres_database
from models.investigation import FinalReportResult, SearchResult
from models.research import (
    ResearchActionDecision,
    ResearchActionRecord,
    ResearchBudgetLimits,
    ResearchBudgetUsage,
    ResearchEvaluation,
    ResearchEvent,
    ResearchRunSummary,
    ResearchTrailResponse,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ResearchRepository:
    """Durable, sanitized run audit store colocated with the MVP SQLite database."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        ensure_parent_dir(db_path)
        if is_postgres_database(db_path):
            run_migrations(db_path)
        else:
            self._init_schema()

    def create_run(
        self,
        investigation_id: str,
        limits: ResearchBudgetLimits,
        *,
        parent_run_id: str | None = None,
        mode: str = "live",
    ) -> ResearchRunSummary:
        existing = self.get_active_run(investigation_id)
        if existing is not None:
            return existing
        run_id = f"run_{uuid4().hex}"
        now = _now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO research_runs (
                    run_id, investigation_id, parent_run_id, mode, plan_version, status, active_node,
                    limits_json, usage_json, warnings_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'queued', 'initialize_run', ?, ?, '[]', ?, ?)
                """,
                (
                    run_id,
                    investigation_id,
                    parent_run_id,
                    mode,
                    get_settings().RESEARCH_PLAN_VERSION,
                    limits.model_dump_json(),
                    ResearchBudgetUsage().model_dump_json(),
                    now,
                    now,
                ),
            )
        self.append_event(run_id, "run.queued", {"investigation_id": investigation_id, "mode": mode})
        return self.get_run(run_id)  # type: ignore[return-value]

    def get_run(self, run_id: str) -> ResearchRunSummary | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM research_runs WHERE run_id = ?", (run_id,)).fetchone()
        return self._row_to_run(row) if row else None

    def get_latest_run(self, investigation_id: str) -> ResearchRunSummary | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM research_runs WHERE investigation_id = ? ORDER BY created_at DESC LIMIT 1",
                (investigation_id,),
            ).fetchone()
        return self._row_to_run(row) if row else None

    def get_active_run(self, investigation_id: str) -> ResearchRunSummary | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM research_runs
                WHERE investigation_id = ? AND status IN ('queued', 'running')
                ORDER BY created_at DESC LIMIT 1
                """,
                (investigation_id,),
            ).fetchone()
        return self._row_to_run(row) if row else None

    def list_resumable_runs(self) -> list[ResearchRunSummary]:
        now = _now().isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM research_runs
                WHERE status = 'queued'
                   OR (status = 'running' AND (lease_until IS NULL OR lease_until < ?))
                ORDER BY created_at
                """,
                (now,),
            ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def claim_run(self, run_id: str, worker_id: str, lease_seconds: int) -> bool:
        now = _now()
        lease_until = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE research_runs
                SET status = 'running', worker_id = ?, lease_until = ?,
                    started_at = COALESCE(started_at, ?), updated_at = ?
                WHERE run_id = ? AND (
                    status = 'queued' OR
                    (status = 'running' AND (lease_until IS NULL OR lease_until < ?)) OR
                    worker_id = ?
                )
                """,
                (worker_id, lease_until, now.isoformat(), now.isoformat(), run_id, now.isoformat(), worker_id),
            )
        if cursor.rowcount:
            self.append_event(run_id, "run.started", {"worker_id": worker_id})
        return bool(cursor.rowcount)

    def renew_lease(self, run_id: str, worker_id: str, lease_seconds: int) -> None:
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE research_runs SET lease_until = ?, updated_at = ?
                WHERE run_id = ? AND worker_id = ? AND status = 'running'
                """,
                ((now + timedelta(seconds=lease_seconds)).isoformat(), now.isoformat(), run_id, worker_id),
            )

    def heartbeat_worker(self, worker_id: str, mode: str, active_run_id: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO research_workers (worker_id, mode, active_run_id, last_seen_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET mode = excluded.mode,
                    active_run_id = excluded.active_run_id, last_seen_at = excluded.last_seen_at
                """,
                (worker_id, mode, active_run_id, _now().isoformat()),
            )

    def recent_workers(self, max_age_seconds: int = 45) -> list[dict[str, Any]]:
        cutoff = (_now() - timedelta(seconds=max_age_seconds)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT worker_id, mode, active_run_id, last_seen_at FROM research_workers "
                "WHERE last_seen_at >= ? ORDER BY last_seen_at DESC",
                (cutoff,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        active_node: str | None = None,
        active_action: str | None = None,
        usage: ResearchBudgetUsage | None = None,
        terminal_decision: str | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        current = self.get_run(run_id)
        if current is None:
            return
        next_status = status or current.status
        completed_at = _now().isoformat() if next_status not in {"queued", "running"} else None
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE research_runs SET status = ?, active_node = ?, active_action = ?,
                    usage_json = ?, terminal_decision = ?, warnings_json = ?,
                    completed_at = COALESCE(?, completed_at), updated_at = ?
                WHERE run_id = ?
                """,
                (
                    next_status,
                    active_node if active_node is not None else current.active_node,
                    active_action if active_action is not None else current.active_action,
                    (usage or current.usage).model_dump_json(),
                    terminal_decision if terminal_decision is not None else current.terminal_decision,
                    json.dumps(warnings if warnings is not None else current.warnings),
                    completed_at,
                    _now().isoformat(),
                    run_id,
                ),
            )

    def append_event(self, run_id: str, event_type: str, payload: dict[str, Any]) -> ResearchEvent:
        created = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM research_events WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            sequence = int(row["next_sequence"])
            conn.execute(
                "INSERT INTO research_events (run_id, sequence, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (run_id, sequence, event_type, json.dumps(payload, default=str), created.isoformat()),
            )
        return ResearchEvent(run_id=run_id, sequence=sequence, event_type=event_type, payload=payload, created_at=created)

    def list_events(self, run_id: str, after_sequence: int = 0, limit: int = 100) -> list[ResearchEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM research_events WHERE run_id = ? AND sequence > ?
                ORDER BY sequence LIMIT ?
                """,
                (run_id, after_sequence, limit),
            ).fetchall()
        return [
            ResearchEvent(
                run_id=row["run_id"], sequence=row["sequence"], event_type=row["event_type"],
                payload=json.loads(row["payload_json"]), created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def start_action(
        self,
        run_id: str,
        decision: ResearchActionDecision,
        provider: str | None = None,
        idempotency_key: str | None = None,
    ) -> ResearchActionRecord:
        if idempotency_key:
            existing = self.get_action_by_idempotency_key(run_id, idempotency_key)
            if existing is not None:
                return existing
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM research_actions WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            sequence = int(row["next_sequence"])
            action_id = f"act_{run_id[4:12]}_{sequence:03d}"
            conn.execute(
                """
                INSERT INTO research_actions (
                    action_id, run_id, sequence, idempotency_key, decision_json, status, provider,
                    document_ids_json, receipt_ids_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'running', ?, '[]', '[]', ?, ?)
                """,
                (
                    action_id, run_id, sequence, idempotency_key, decision.model_dump_json(), provider,
                    now.isoformat(), now.isoformat(),
                ),
            )
        self.append_event(run_id, "action.started", {"action_id": action_id, "action_type": decision.action_type, "summary": decision.action_summary})
        return self.get_action(action_id)  # type: ignore[return-value]

    def finish_action(
        self,
        action_id: str,
        *,
        status: str,
        result_count: int = 0,
        document_ids: list[str] | None = None,
        receipt_ids: list[str] | None = None,
        duration_ms: int | None = None,
        failure_category: str | None = None,
        warning: str | None = None,
    ) -> ResearchActionRecord:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE research_actions SET status = ?, result_count = ?, document_ids_json = ?,
                    receipt_ids_json = ?, duration_ms = ?, failure_category = ?, warning = ?, updated_at = ?
                WHERE action_id = ?
                """,
                (
                    status, result_count, json.dumps(document_ids or []), json.dumps(receipt_ids or []),
                    duration_ms, failure_category, warning, _now().isoformat(), action_id,
                ),
            )
        action = self.get_action(action_id)
        assert action is not None
        event_type = "action.completed" if status in {"completed", "partial"} else "action.failed"
        self.append_event(action.run_id, event_type, {
            "action_id": action_id, "status": status, "result_count": result_count,
            "document_count": len(document_ids or []), "warning": warning,
        })
        return action

    def get_action(self, action_id: str) -> ResearchActionRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM research_actions WHERE action_id = ?", (action_id,)).fetchone()
        return self._row_to_action(row) if row else None

    def get_action_by_idempotency_key(self, run_id: str, idempotency_key: str) -> ResearchActionRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM research_actions WHERE run_id = ? AND idempotency_key = ?",
                (run_id, idempotency_key),
            ).fetchone()
        return self._row_to_action(row) if row else None

    def list_action_receipts(self, action_id: str) -> list[tuple[str, dict[str, Any]]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT kind, payload_json FROM research_receipts WHERE action_id = ? ORDER BY created_at",
                (action_id,),
            ).fetchall()
        return [(row["kind"], json.loads(row["payload_json"])) for row in rows]

    def list_actions(self, run_id: str) -> list[ResearchActionRecord]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM research_actions WHERE run_id = ? ORDER BY sequence", (run_id,)).fetchall()
        return [self._row_to_action(row) for row in rows]

    def save_receipt(self, run_id: str, action_id: str, kind: str, payload: dict[str, Any]) -> str:
        receipt_id = f"rr_{uuid4().hex[:16]}"
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO research_receipts (receipt_id, run_id, action_id, kind, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (receipt_id, run_id, action_id, kind, json.dumps(payload, default=str), _now().isoformat()),
            )
        return receipt_id

    def save_document(self, run_id: str, document: Document) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO research_documents (run_id, doc_id, document_json, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(run_id, doc_id) DO UPDATE SET document_json = excluded.document_json
                """,
                (run_id, document.id, document.model_dump_json(), _now().isoformat()),
            )

    def save_candidate(self, run_id: str, candidate_key: str, result: SearchResult) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO research_candidates (run_id, candidate_id, result_json, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(run_id, candidate_id) DO UPDATE SET result_json = excluded.result_json
                """,
                (run_id, candidate_key, result.model_dump_json(), _now().isoformat()),
            )

    def get_candidates(self, run_id: str) -> list[SearchResult]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT result_json FROM research_candidates WHERE run_id = ? ORDER BY created_at",
                (run_id,),
            ).fetchall()
        return [SearchResult.model_validate_json(row["result_json"]) for row in rows]

    def save_candidate_report(self, run_id: str, report: FinalReportResult) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO research_candidate_reports (run_id, report_json, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET report_json = excluded.report_json,
                    created_at = excluded.created_at
                """,
                (run_id, report.model_dump_json(), _now().isoformat()),
            )

    def get_candidate_report(self, run_id: str) -> FinalReportResult | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT report_json FROM research_candidate_reports WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return FinalReportResult.model_validate_json(row["report_json"]) if row else None

    def get_documents(self, run_id: str) -> list[Document]:
        with self._connect() as conn:
            rows = conn.execute("SELECT document_json FROM research_documents WHERE run_id = ? ORDER BY created_at", (run_id,)).fetchall()
        return [Document.model_validate_json(row["document_json"]) for row in rows]

    def save_evaluation(self, evaluation: ResearchEvaluation) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO research_evaluations (run_id, evaluation_json, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET evaluation_json = excluded.evaluation_json, created_at = excluded.created_at
                """,
                (evaluation.run_id, evaluation.model_dump_json(), evaluation.created_at.isoformat()),
            )

    def record_model_call(
        self,
        run_id: str,
        *,
        provider: str,
        model: str,
        prompt_version: str,
        prompt_hash: str,
        schema_name: str,
        input_tokens: int,
        output_tokens: int,
        estimated_cost: float,
        duration_ms: int,
        status: str,
        error: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO research_model_calls (
                    call_id, run_id, provider, model, prompt_version, prompt_hash,
                    schema_name, input_tokens, output_tokens, estimated_cost,
                    duration_ms, status, error, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"mc_{uuid4().hex}", run_id, provider, model, prompt_version, prompt_hash,
                    schema_name, input_tokens, output_tokens, estimated_cost, duration_ms,
                    status, error, _now().isoformat(),
                ),
            )

    def get_evaluation(self, run_id: str) -> ResearchEvaluation | None:
        with self._connect() as conn:
            row = conn.execute("SELECT evaluation_json FROM research_evaluations WHERE run_id = ?", (run_id,)).fetchone()
        return ResearchEvaluation.model_validate_json(row["evaluation_json"]) if row else None

    def get_trail(self, investigation_id: str, after_sequence: int = 0, limit: int = 100) -> ResearchTrailResponse:
        run = self.get_latest_run(investigation_id)
        if run is None:
            return ResearchTrailResponse()
        events = self.list_events(run.run_id, after_sequence, limit)
        return ResearchTrailResponse(
            run=self.get_run(run.run_id),
            events=events,
            actions=self.list_actions(run.run_id),
            evaluation=self.get_evaluation(run.run_id),
            replay_comparison=self.get_replay_comparison(run.run_id),
            next_sequence=events[-1].sequence if events else after_sequence,
        )

    def record_replay_comparison(self, source_run_id: str, replay_run_id: str, comparison: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO research_replay_comparisons
                (replay_run_id, source_run_id, comparison_json, created_at) VALUES (?, ?, ?, ?)
                """,
                (replay_run_id, source_run_id, json.dumps(comparison), _now().isoformat()),
            )

    def get_replay_comparison(self, replay_run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT source_run_id, comparison_json FROM research_replay_comparisons WHERE replay_run_id = ?",
                (replay_run_id,),
            ).fetchone()
        if row is None:
            return None
        return {"source_run_id": row["source_run_id"], **json.loads(row["comparison_json"])}

    def _row_to_run(self, row: sqlite3.Row) -> ResearchRunSummary:
        action_count = document_count = source_count = last_sequence = 0
        with self._connect() as conn:
            action_count = conn.execute("SELECT COUNT(*) AS n FROM research_actions WHERE run_id = ?", (row["run_id"],)).fetchone()["n"]
            document_count = conn.execute("SELECT COUNT(*) AS n FROM research_documents WHERE run_id = ?", (row["run_id"],)).fetchone()["n"]
            last_sequence = conn.execute("SELECT COALESCE(MAX(sequence), 0) AS n FROM research_events WHERE run_id = ?", (row["run_id"],)).fetchone()["n"]
            docs = conn.execute("SELECT document_json FROM research_documents WHERE run_id = ?", (row["run_id"],)).fetchall()
        if docs:
            source_count = len({Document.model_validate_json(item["document_json"]).source_name for item in docs})
        return ResearchRunSummary(
            run_id=row["run_id"], investigation_id=row["investigation_id"], parent_run_id=row["parent_run_id"],
            mode=row["mode"], plan_version=row["plan_version"], status=row["status"],
            active_node=row["active_node"], active_action=row["active_action"],
            last_event_sequence=last_sequence, limits=ResearchBudgetLimits.model_validate_json(row["limits_json"]),
            usage=ResearchBudgetUsage.model_validate_json(row["usage_json"]), action_count=action_count,
            document_count=document_count, source_count=source_count, terminal_decision=row["terminal_decision"],
            warnings=json.loads(row["warnings_json"]),
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            updated_at=datetime.fromisoformat(row["updated_at"]),
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
        )

    def _row_to_action(self, row) -> ResearchActionRecord:
        return ResearchActionRecord(
            action_id=row["action_id"], run_id=row["run_id"], sequence=row["sequence"],
            idempotency_key=row["idempotency_key"],
            decision=ResearchActionDecision.model_validate_json(row["decision_json"]), status=row["status"],
            provider=row["provider"], result_count=row["result_count"], document_ids=json.loads(row["document_ids_json"]),
            receipt_ids=json.loads(row["receipt_ids_json"]), duration_ms=row["duration_ms"],
            failure_category=row["failure_category"], warning=row["warning"],
            created_at=datetime.fromisoformat(row["created_at"]), updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _connect(self):
        return connect(self.db_path)

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS research_runs (
                    run_id TEXT PRIMARY KEY,
                    investigation_id TEXT NOT NULL,
                    parent_run_id TEXT,
                    mode TEXT NOT NULL,
                    plan_version TEXT NOT NULL DEFAULT 'a2-v1',
                    status TEXT NOT NULL,
                    active_node TEXT,
                    active_action TEXT,
                    limits_json TEXT NOT NULL,
                    usage_json TEXT NOT NULL,
                    terminal_decision TEXT,
                    warnings_json TEXT NOT NULL,
                    worker_id TEXT,
                    lease_until TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_research_runs_investigation ON research_runs(investigation_id, created_at);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_research_run
                    ON research_runs(investigation_id) WHERE status IN ('queued', 'running');
                CREATE TABLE IF NOT EXISTS research_events (
                    run_id TEXT NOT NULL, sequence INTEGER NOT NULL, event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(run_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS research_actions (
                    action_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                    idempotency_key TEXT,
                    decision_json TEXT NOT NULL, status TEXT NOT NULL, provider TEXT,
                    result_count INTEGER NOT NULL DEFAULT 0, document_ids_json TEXT NOT NULL,
                    receipt_ids_json TEXT NOT NULL, duration_ms INTEGER, failure_category TEXT,
                    warning TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(run_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS research_receipts (
                    receipt_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, action_id TEXT NOT NULL,
                    kind TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_documents (
                    run_id TEXT NOT NULL, doc_id TEXT NOT NULL, document_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, PRIMARY KEY(run_id, doc_id)
                );
                CREATE TABLE IF NOT EXISTS research_candidates (
                    run_id TEXT NOT NULL, candidate_id TEXT NOT NULL, result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, PRIMARY KEY(run_id, candidate_id)
                );
                CREATE TABLE IF NOT EXISTS research_candidate_reports (
                    run_id TEXT PRIMARY KEY, report_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_model_calls (
                    call_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, action_id TEXT, provider TEXT,
                    model TEXT, prompt_version TEXT, prompt_hash TEXT, schema_name TEXT,
                    input_tokens INTEGER, output_tokens INTEGER, estimated_cost REAL,
                    duration_ms INTEGER, status TEXT, error TEXT, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_evaluations (
                    run_id TEXT PRIMARY KEY, evaluation_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_replay_comparisons (
                    replay_run_id TEXT PRIMARY KEY, source_run_id TEXT NOT NULL,
                    comparison_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_workers (
                    worker_id TEXT PRIMARY KEY, mode TEXT NOT NULL, active_run_id TEXT,
                    last_seen_at TEXT NOT NULL
                );
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(research_actions)").fetchall()}
            if "idempotency_key" not in columns:
                conn.execute("ALTER TABLE research_actions ADD COLUMN idempotency_key TEXT")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_research_action_idempotency "
                "ON research_actions(run_id, idempotency_key) WHERE idempotency_key IS NOT NULL"
            )
            run_columns = {row["name"] for row in conn.execute("PRAGMA table_info(research_runs)").fetchall()}
            if "plan_version" not in run_columns:
                conn.execute("ALTER TABLE research_runs ADD COLUMN plan_version TEXT NOT NULL DEFAULT 'a2-v1'")
