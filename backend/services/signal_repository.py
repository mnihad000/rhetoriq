"""Idempotent B4 projections for processed documents and narrative signals.

The projector deliberately accepts event dictionaries as well as Pydantic event
objects. This lets a rolling deployment project newer event fields while older
API workers still use the B3 event models.
"""

from __future__ import annotations

from contextlib import nullcontext

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from migrations.runner import run_migrations
from services.database import connect, ensure_parent_dir, is_postgres_database


def _event_dict(event: Any) -> dict[str, Any]:
    if hasattr(event, "model_dump"):
        return event.model_dump(mode="json")
    if isinstance(event, dict):
        return dict(event)
    raise TypeError("event must be a mapping or Pydantic model")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _number(value: Any, default: float = 0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class SignalRepository:
    def __init__(self, target: str) -> None:
        self.target = target
        ensure_parent_dir(target)
        if is_postgres_database(target):
            run_migrations(target)
        else:
            self._init_schema()

    def project_processed(self, event: Any, *, connection=None) -> bool:
        """Project one processed-document event, returning False on replay."""
        envelope = _event_dict(event)
        payload = envelope.get("payload") or {}
        document = payload.get("document") or {}
        event_id = str(envelope.get("event_id") or self._stable_id("processed", envelope))
        processing = payload.get("processing") or {}
        receipt = payload.get("enrichment_receipt") or payload.get("enrichment")
        semantic_hash = payload.get("semantic_output_hash") or processing.get("semantic_output_hash")
        if semantic_hash is None and isinstance(receipt, dict):
            semantic_hash = receipt.get("output_hash")
        projected_at = _now()
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            existing = conn.execute(
                "SELECT event_json FROM processed_document_lineage WHERE event_id = ?", (event_id,)
            ).fetchone()
            if existing is not None:
                if json.loads(existing["event_json"]).get("payload") != payload:
                    raise ValueError(f"processed event {event_id} has a different semantic payload")
                return False
            conn.execute(
                """
                INSERT INTO processed_document_lineage
                    (event_id, document_id, raw_event_id, semantic_output_hash,
                     enrichment_receipt_json, document_json, event_json,
                     occurred_at, projected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO NOTHING
                """,
                (
                    event_id,
                    str(document.get("id") or "unknown"),
                    processing.get("raw_event_id"),
                    semantic_hash,
                    _json(receipt) if receipt is not None else None,
                    _json(document),
                    _json(envelope),
                    envelope.get("occurred_at"),
                    projected_at,
                ),
            )
        return True

    def project_signal(self, event: Any) -> bool:
        """Project a signal revision and its horizon/supporting-document rows."""
        envelope = _event_dict(event)
        payload = envelope.get("payload") or {}
        signal_id = str(payload.get("signal_id") or envelope.get("signal_id") or "")
        if not signal_id:
            raise ValueError("signal event is missing payload.signal_id")
        revision_raw = payload.get("signal_revision", payload.get("revision", 1))
        try:
            revision = max(1, int(revision_raw))
        except (TypeError, ValueError):
            revision = 1
        score = payload.get("score") or {}
        horizon = payload.get("horizon") or _horizon_from_payload(payload)
        canonical = str(payload.get("canonical_phrase_id") or payload.get("canonical_phrase") or signal_id)
        occurred_at = envelope.get("occurred_at")
        produced_at = envelope.get("produced_at")
        limitations = list(payload.get("coverage_limitations") or payload.get("limitations") or [])
        projected_at = _now()
        baseline = payload.get("baseline_statistics") or {
            "observed_count": score.get("observed_count", 0),
            "baseline_count": score.get("baseline_count", 0),
            "spike": score.get("spike", 0),
        }
        lineage = payload.get("artifact_lineage") or payload.get("artifact_refs") or []
        lifecycle = str(payload.get("lifecycle_status") or payload.get("status") or "active")
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT payload_json, event_json FROM signal_revisions WHERE signal_id = ? AND signal_revision = ?",
                (signal_id, revision),
            ).fetchone()
            if existing is not None:
                if existing["payload_json"] != _json(payload):
                    raise ValueError(f"signal revision {signal_id}/{revision} has a different semantic payload")
                return False
            conn.execute(
                """
                INSERT INTO signal_revisions
                    (signal_id, signal_revision, canonical_phrase_id, lifecycle_status,
                     horizon, scoring_version, event_time_quality, baseline_statistics_json,
                     artifact_lineage_json, payload_json, event_json, occurred_at,
                     produced_at, projected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(signal_id, signal_revision) DO NOTHING
                """,
                (
                    signal_id, revision, canonical, lifecycle, horizon,
                    payload.get("scoring_version") or payload.get("score_version"),
                    payload.get("event_time_quality") or "unknown",
                    _json(baseline), _json(lineage), _json(payload), _json(envelope),
                    occurred_at, produced_at, projected_at,
                ),
            )
            nested_metrics = payload.get("horizon_metrics")
            if isinstance(nested_metrics, dict):
                metric_entries = [(str(key), value or {}) for key, value in nested_metrics.items()]
            elif isinstance(nested_metrics, list):
                metric_entries = [
                    (str(item.get("horizon") or "unknown"), item)
                    for item in nested_metrics if isinstance(item, dict)
                ]
            else:
                metric_entries = [(horizon, score)]
            for metric_horizon, metric in metric_entries or [(horizon, score)]:
                metric_score = metric.get("score") if isinstance(metric.get("score"), dict) else metric
                conn.execute(
                    """
                    INSERT INTO signal_horizon_metrics
                        (signal_id, signal_revision, horizon, observed_count, baseline_count,
                         spike, confidence, source_diversity, publisher_diversity,
                         observed_window_json, baseline_window_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(signal_id, signal_revision, horizon) DO UPDATE SET
                        observed_count=excluded.observed_count,
                        baseline_count=excluded.baseline_count,
                        spike=excluded.spike,
                        confidence=excluded.confidence,
                        source_diversity=excluded.source_diversity,
                        publisher_diversity=excluded.publisher_diversity,
                        observed_window_json=excluded.observed_window_json,
                        baseline_window_json=excluded.baseline_window_json
                    """,
                    (
                        signal_id, revision, metric_horizon,
                        int(_number(metric_score.get("observed_count"))),
                        _number(metric_score.get("baseline_count", metric_score.get("baseline_mean"))),
                        _number(metric_score.get("spike")),
                        _number(metric_score.get("confidence")),
                        int(_number(metric.get("source_diversity", metric.get("source_type_count", payload.get("source_diversity"))))),
                        int(_number(metric.get("publisher_diversity", metric.get("publisher_count", payload.get("publisher_diversity"))))),
                        _json(metric.get("observed_window", payload.get("observed_window"))) if metric.get("observed_window", payload.get("observed_window")) is not None else None,
                        _json(metric.get("baseline_window", payload.get("baseline_window"))) if metric.get("baseline_window", payload.get("baseline_window")) is not None else None,
                    ),
                )
            for document_id in payload.get("supporting_document_ids") or []:
                conn.execute(
                    """
                    INSERT INTO signal_supporting_documents(signal_id, signal_revision, document_id)
                    VALUES (?, ?, ?) ON CONFLICT(signal_id, signal_revision, document_id) DO NOTHING
                    """, (signal_id, revision, str(document_id)),
                )
            for limitation in limitations:
                conn.execute(
                    """
                    INSERT INTO signal_limitations(signal_id, signal_revision, limitation)
                    VALUES (?, ?, ?) ON CONFLICT(signal_id, signal_revision, limitation) DO NOTHING
                    """, (signal_id, revision, str(limitation)),
                )
        return True

    def record_evaluation(self, evaluated_at: datetime | str, details: dict[str, Any]) -> bool:
        """Store an evaluation heartbeat, deduplicating identical reports."""
        evaluated = evaluated_at.isoformat() if isinstance(evaluated_at, datetime) else str(evaluated_at)
        details_json = _json(details)
        heartbeat_id = "eval_" + hashlib.sha256(f"{evaluated}|{details_json}".encode()).hexdigest()[:32]
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM evaluation_heartbeats WHERE heartbeat_id = ?", (heartbeat_id,)
            ).fetchone()
            if existing is not None:
                return False
            conn.execute(
                """
                INSERT INTO evaluation_heartbeats(heartbeat_id, evaluated_at, details_json, created_at)
                VALUES (?, ?, ?, ?) ON CONFLICT(heartbeat_id) DO NOTHING
                """, (heartbeat_id, evaluated, details_json, _now()),
            )
        return True

    def project_late(self, event: Any) -> bool:
        envelope = _event_dict(event)
        payload = envelope.get("payload") or {}
        event_id = str(envelope.get("event_id") or self._stable_id("late", envelope))
        with self._connect() as conn:
            existing = conn.execute("SELECT 1 FROM late_document_audit WHERE event_id = ?", (event_id,)).fetchone()
            if existing is not None:
                return False
            conn.execute(
                """
                INSERT INTO late_document_audit(event_id, document_id, reason, event_json, occurred_at, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(event_id) DO NOTHING
                """,
                (event_id, payload.get("document_id") or (payload.get("document") or {}).get("id"),
                 payload.get("reason") or payload.get("late_reason"), _json(envelope), envelope.get("occurred_at"), _now()),
            )
        return True

    def latest_evaluation(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT heartbeat_id, evaluated_at, details_json, created_at FROM evaluation_heartbeats ORDER BY evaluated_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return {
            "heartbeat_id": row["heartbeat_id"],
            "evaluated_at": row["evaluated_at"],
            "details": json.loads(row["details_json"]),
            "created_at": row["created_at"],
        }

    def latest_signals(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM signal_revisions ORDER BY signal_revision DESC, occurred_at DESC"
            ).fetchall()
            selected: dict[str, Any] = {}
            for row in rows:
                signal_id = row["signal_id"]
                if signal_id not in selected:
                    selected[signal_id] = row
            rows = list(selected.values())[:limit]
            result = []
            for row in rows:
                metrics = conn.execute(
                    "SELECT * FROM signal_horizon_metrics WHERE signal_id = ? AND signal_revision = ?",
                    (row["signal_id"], row["signal_revision"]),
                ).fetchall()
                docs = conn.execute(
                    "SELECT document_id FROM signal_supporting_documents WHERE signal_id = ? AND signal_revision = ?",
                    (row["signal_id"], row["signal_revision"]),
                ).fetchall()
                limitations = conn.execute(
                    "SELECT limitation FROM signal_limitations WHERE signal_id = ? AND signal_revision = ?",
                    (row["signal_id"], row["signal_revision"]),
                ).fetchall()
                result.append({
                    "signal_id": row["signal_id"],
                    "signal_revision": row["signal_revision"],
                    "canonical_phrase_id": row["canonical_phrase_id"],
                    "lifecycle_status": row["lifecycle_status"],
                    "horizon": row["horizon"],
                    "scoring_version": row["scoring_version"],
                    "event_time_quality": row["event_time_quality"],
                    "payload": json.loads(row["payload_json"]),
                    "metrics": [dict(metric) for metric in metrics],
                    "supporting_document_ids": [doc["document_id"] for doc in docs],
                    "coverage_limitations": [item["limitation"] for item in limitations],
                    "occurred_at": row["occurred_at"],
                    "produced_at": row["produced_at"],
                })
            return result

    def health(self) -> dict[str, Any]:
        evaluation = self.latest_evaluation()
        with self._connect() as conn:
            signals = conn.execute("SELECT COUNT(*) AS count FROM signal_revisions").fetchone()["count"]
            processed = conn.execute("SELECT COUNT(*) AS count FROM processed_document_lineage").fetchone()["count"]
            late = conn.execute("SELECT COUNT(*) AS count FROM late_document_audit").fetchone()["count"]
            if is_postgres_database(self.target):
                artifact_exists = conn.execute("SELECT to_regclass('public.enrichment_artifacts') AS name").fetchone()["name"] is not None
            else:
                artifact_exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'enrichment_artifacts'"
                ).fetchone() is not None
            failures = 0
            if artifact_exists:
                # The enrichment-owned table exposes failure_state; keep this
                # query independent of optional status columns.
                failures = conn.execute(
                    "SELECT COUNT(*) AS count FROM enrichment_artifacts WHERE failure_state IS NOT NULL"
                ).fetchone()["count"]
            if is_postgres_database(self.target):
                failure_table_exists = conn.execute("SELECT to_regclass('public.enrichment_artifact_failures') AS name").fetchone()["name"] is not None
            else:
                failure_table_exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='enrichment_artifact_failures'").fetchone() is not None
            if failure_table_exists:
                failures += conn.execute("SELECT COUNT(*) AS count FROM enrichment_artifact_failures").fetchone()["count"]
            if is_postgres_database(self.target):
                worker_table_exists = conn.execute("SELECT to_regclass('public.enrichment_worker_health') AS name").fetchone()["name"] is not None
            else:
                worker_table_exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='enrichment_worker_health'").fetchone() is not None
            workers = [dict(row) for row in conn.execute(
                "SELECT worker_id,state,budget_paused,heartbeat_at FROM enrichment_worker_health ORDER BY heartbeat_at DESC LIMIT 50"
            ).fetchall()] if worker_table_exists else []
        now = datetime.now(timezone.utc)
        for worker in workers:
            timestamp = worker["heartbeat_at"]
            if not isinstance(timestamp, datetime):
                timestamp = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            worker["heartbeat_age_seconds"] = max(0, (now - timestamp).total_seconds())
            worker["stale"] = (now - timestamp).total_seconds() > 120
        active = [worker for worker in workers if not worker["stale"]]
        enrichment_status = "unavailable" if not active else "degraded" if any(worker["budget_paused"] or worker["state"] != "healthy" for worker in active) else "ready"
        return {"status": "ready", "signal_revision_count": signals, "processed_document_count": processed,
                "late_event_count": late, "artifact_failure_count": failures, "latest_evaluation": evaluation,
                "enrichment_status": enrichment_status, "enrichment_workers": workers}

    def _connect(self):
        return connect(self.target)

    def _init_schema(self) -> None:
        migration = Path(__file__).resolve().parents[1] / "migrations" / "004_b4_signals.sql"
        with self._connect() as conn:
            conn.executescript(migration.read_text(encoding="utf-8"))

    @staticmethod
    def _stable_id(prefix: str, event: dict[str, Any]) -> str:
        return f"{prefix}_{hashlib.sha256(_json(event).encode()).hexdigest()[:32]}"


def _horizon_from_payload(payload: dict[str, Any]) -> str:
    label = str(payload.get("window") or payload.get("horizon_label") or "")
    if label in {"emerging", "6h", "six_hour", "six-hour"}:
        return "emerging"
    if label in {"sustained", "24h", "twenty_four_hour", "24-hour"}:
        return "sustained"
    return "unknown"
