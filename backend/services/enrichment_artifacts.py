"""Durable, immutable enrichment artifacts and a shared budget ledger."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import tempfile
from typing import Any

from services.database import connect, ensure_parent_dir, is_postgres_database


def _target(target: str, prefix: str) -> str:
    if target != ":memory:":
        return target
    with tempfile.NamedTemporaryFile(prefix=prefix, suffix=".sqlite3", delete=False) as file:
        return file.name


ARTIFACTS_DDL = """
CREATE TABLE IF NOT EXISTS enrichment_artifacts (
    artifact_key TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL UNIQUE,
    input_hash TEXT NOT NULL,
    pipeline_version TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    output_hash TEXT NOT NULL,
    artifact_json TEXT NOT NULL,
    failure_state TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_enrichment_artifacts_input ON enrichment_artifacts(input_hash);
CREATE TABLE IF NOT EXISTS enrichment_budget_windows (
    budget_name TEXT NOT NULL,
    window_start TEXT NOT NULL,
    request_limit INTEGER NOT NULL,
    spend_limit_micros INTEGER NOT NULL,
    reserved_requests INTEGER NOT NULL DEFAULT 0,
    reserved_spend_micros INTEGER NOT NULL DEFAULT 0,
    used_requests INTEGER NOT NULL DEFAULT 0,
    used_spend_micros INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (budget_name, window_start)
);
CREATE TABLE IF NOT EXISTS enrichment_worker_health (
    worker_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    budget_paused INTEGER NOT NULL DEFAULT 0,
    heartbeat_at TEXT NOT NULL,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS enrichment_artifact_failures (
    failure_id TEXT PRIMARY KEY,
    request_event_id TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    pipeline_version TEXT NOT NULL,
    failure_state TEXT NOT NULL,
    attempt_count INTEGER NOT NULL,
    failed_at TEXT NOT NULL
);
"""


def artifact_identity_key(
    *, input_hash: str, pipeline_version: str, provider: str, model: str,
    prompt_version: str, schema_version: str, embedding_model: str,
) -> str:
    identity = {
        "input_hash": input_hash,
        "pipeline_version": pipeline_version,
        "provider": provider,
        "model": model,
        "prompt_version": prompt_version,
        "schema_version": schema_version,
        "embedding_model": embedding_model,
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class EnrichmentArtifactStore:
    """SQLite/PostgreSQL store whose keys make writes idempotent and immutable."""

    def __init__(self, target: str = ":memory:") -> None:
        self.target = _target(target, "rhetoriq-enrichment-")
        ensure_parent_dir(self.target)
        if is_postgres_database(self.target):
            from migrations.runner import run_migrations
            run_migrations(self.target)
        with connect(self.target) as db:
            db.executescript(ARTIFACTS_DDL)

    def put(self, receipt: Any, *, pipeline_version: str = "b4-v1") -> Any:
        data = receipt.model_dump(mode="json") if hasattr(receipt, "model_dump") else dict(receipt)
        identity = {key: data[key] for key in (
            "input_hash", "provider", "model", "prompt_version", "schema_version", "embedding_model",
        )}
        identity["pipeline_version"] = pipeline_version
        key = artifact_identity_key(**identity)
        artifact_id = str(data.get("artifact_id") or f"artifact_{key[:24]}")
        data["artifact_id"] = artifact_id
        output_hash = str(data["output_hash"])
        encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        now = datetime.now(timezone.utc).isoformat()
        with connect(self.target) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT artifact_json FROM enrichment_artifacts WHERE artifact_key = ?", (key,)).fetchone()
            if row:
                existing = json.loads(row["artifact_json"])
                if existing != data:
                    raise ValueError("Enrichment artifact identity is immutable")
                return receipt.__class__.model_validate(existing) if hasattr(receipt.__class__, "model_validate") else existing
            db.execute(
                """INSERT INTO enrichment_artifacts
                (artifact_key, artifact_id, input_hash, pipeline_version, provider, model,
                 prompt_version, schema_version, embedding_model, output_hash, artifact_json,
                 failure_state, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                 ON CONFLICT (artifact_key) DO NOTHING""",
                (key, artifact_id, data["input_hash"], pipeline_version, data["provider"], data["model"],
                 data["prompt_version"], data["schema_version"], data["embedding_model"], output_hash,
                 encoded, data.get("failure_state"), now),
            )
            persisted = db.execute(
                "SELECT artifact_json FROM enrichment_artifacts WHERE artifact_key = ?", (key,)
            ).fetchone()
            if persisted:
                existing = json.loads(persisted["artifact_json"])
                if existing != data:
                    raise ValueError("Enrichment artifact identity is immutable")
        return receipt

    def record_failure(self, request: Any, error: Exception, *, attempt_count: int, pipeline_version: str) -> None:
        state = type(error).__name__
        identity = f"{request.event_id}|{pipeline_version}|{attempt_count}|{state}"
        failure_id = hashlib.sha256(identity.encode()).hexdigest()
        with connect(self.target) as db:
            db.execute("""INSERT INTO enrichment_artifact_failures
                (failure_id, request_event_id, input_hash, pipeline_version, failure_state, attempt_count, failed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(failure_id) DO NOTHING""",
                (failure_id, request.event_id, request.payload.input_hash, pipeline_version, state, attempt_count,
                 datetime.now(timezone.utc).isoformat()))

    def get(self, *, input_hash: str, pipeline_version: str, provider: str, model: str,
            prompt_version: str, schema_version: str, embedding_model: str) -> dict[str, Any] | None:
        key = artifact_identity_key(
            input_hash=input_hash, pipeline_version=pipeline_version, provider=provider,
            model=model, prompt_version=prompt_version, schema_version=schema_version,
            embedding_model=embedding_model,
        )
        with connect(self.target) as db:
            row = db.execute("SELECT artifact_json FROM enrichment_artifacts WHERE artifact_key = ?", (key,)).fetchone()
        return json.loads(row["artifact_json"]) if row else None

    def get_by_input_hash(self, input_hash: str, *, pipeline_version: str | None = None,
                          prompt_version: str | None = None, schema_version: str | None = None,
                          embedding_model: str | None = None) -> list[dict[str, Any]]:
        with connect(self.target) as db:
            clauses = ["input_hash = ?"]
            params: list[Any] = [input_hash]
            for field, value in (("pipeline_version", pipeline_version), ("prompt_version", prompt_version),
                                 ("schema_version", schema_version), ("embedding_model", embedding_model)):
                if value is not None:
                    clauses.append(f"{field} = ?")
                    params.append(value)
            rows = db.execute(
                f"SELECT artifact_json FROM enrichment_artifacts WHERE {' AND '.join(clauses)} ORDER BY created_at",
                params,
            ).fetchall()
        return [json.loads(row["artifact_json"]) for row in rows]


class BudgetExceeded(RuntimeError):
    pass


class BudgetReservation:
    def __init__(self, ledger: "EnrichmentBudgetLedger", key: tuple[str, str], requests: int, spend_micros: int):
        self.ledger, self.key, self.requests, self.spend_micros = ledger, key, requests, spend_micros
        self._settled = False

    def settle(self, *, used_requests: int | None = None, used_spend_micros: int | None = None) -> None:
        if self._settled:
            return
        self.ledger.settle(self, used_requests=used_requests, used_spend_micros=used_spend_micros)
        self._settled = True


class EnrichmentBudgetLedger:
    """Atomic durable request/spend reservations shared by worker processes."""

    def __init__(self, target: str = ":memory:") -> None:
        self.target = _target(target, "rhetoriq-budget-")
        ensure_parent_dir(self.target)
        with connect(self.target) as db:
            db.executescript(ARTIFACTS_DDL)

    @staticmethod
    def _window_start(now: datetime, window_seconds: int) -> str:
        epoch = int(now.timestamp())
        start = epoch - (epoch % window_seconds)
        return datetime.fromtimestamp(start, timezone.utc).isoformat()

    def reserve(self, *, budget_name: str = "default", request_cost: int = 1,
                spend_micros: int = 0, request_limit: int = 0,
                spend_limit_micros: int = 0, now: datetime | None = None,
                window_seconds: int = 60) -> BudgetReservation:
        if request_cost < 1 or spend_micros < 0:
            raise ValueError("Budget costs must be positive requests and non-negative spend")
        window = self._window_start(now or datetime.now(timezone.utc), window_seconds)
        key = (budget_name, window)
        with connect(self.target) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """INSERT INTO enrichment_budget_windows
                (budget_name, window_start, request_limit, spend_limit_micros)
                VALUES (?, ?, ?, ?) ON CONFLICT (budget_name, window_start) DO NOTHING""",
                (budget_name, window, request_limit, spend_limit_micros),
            )
            changed = db.execute(
                """UPDATE enrichment_budget_windows SET
                reserved_requests = reserved_requests + ?, reserved_spend_micros = reserved_spend_micros + ?
                WHERE budget_name = ? AND window_start = ?
                  AND (? = 0 OR used_requests + reserved_requests + ? <= request_limit)
                  AND (? = 0 OR used_spend_micros + reserved_spend_micros + ? <= spend_limit_micros)""",
                (request_cost, spend_micros, *key, request_limit, request_cost, spend_limit_micros, spend_micros),
            )
            if changed.rowcount != 1:
                raise BudgetExceeded(f"Enrichment budget exhausted for {budget_name}")
        return BudgetReservation(self, key, request_cost, spend_micros)

    def settle(self, reservation: BudgetReservation, *, used_requests: int | None = None,
               used_spend_micros: int | None = None) -> None:
        used_requests = reservation.requests if used_requests is None else max(0, used_requests)
        used_spend_micros = reservation.spend_micros if used_spend_micros is None else max(0, used_spend_micros)
        with connect(self.target) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """UPDATE enrichment_budget_windows SET
                reserved_requests = reserved_requests - ?, reserved_spend_micros = reserved_spend_micros - ?,
                used_requests = used_requests + ?, used_spend_micros = used_spend_micros + ?
                WHERE budget_name = ? AND window_start = ?""",
                (reservation.requests, reservation.spend_micros, used_requests, used_spend_micros, *reservation.key),
            )


# Compatibility names for integrations that use repository terminology.
ArtifactStore = EnrichmentArtifactStore
BudgetLedger = EnrichmentBudgetLedger
