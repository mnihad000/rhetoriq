from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any

from models.events import EventEnvelope
from services.database import connect, ensure_parent_dir, is_postgres_database
from migrations.runner import run_migrations


def _now() -> datetime:
    return datetime.now(timezone.utc)


def payload_hash(value: bytes | str | dict[str, Any] | EventEnvelope) -> str:
    if isinstance(value, EventEnvelope):
        payload = value.payload
        semantic_hash = (payload.get("semantic_output_hash") if isinstance(payload, dict)
                         else getattr(payload, "semantic_output_hash", None))
        if semantic_hash:
            # Flink replays preserve semantics while publication time changes.
            raw = json.dumps(value.model_dump(mode="json", exclude={"produced_at"}),
                             sort_keys=True, separators=(",", ":")).encode("utf-8")
        else:
            raw = value.model_dump_json().encode("utf-8")
    elif isinstance(value, bytes):
        raw = value
    elif isinstance(value, str):
        raw = value.encode("utf-8")
    else:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class OutboxRecord:
    event_id: str
    topic: str
    partition_key: str
    schema_subject: str
    event_json: str
    correlation_id: str
    attempts: int


class EventStore:
    """Durable outbox, consumer idempotency ledger, and replay audit store."""

    def __init__(self, target: str) -> None:
        self.target = target
        ensure_parent_dir(target)
        if is_postgres_database(target):
            run_migrations(target)
        else:
            self._init_sqlite()

    def enqueue(
        self,
        topic: str,
        event: EventEnvelope,
        *,
        connection=None,
    ) -> bool:
        values = (
            event.event_id,
            topic,
            event.partition_key,
            f"{topic}-value",
            event.model_dump_json(),
            event.correlation_id,
            _now().isoformat(),
        )
        query = """
            INSERT INTO event_outbox (
                event_id, topic, partition_key, schema_subject, event_json,
                correlation_id, status, attempts, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?)
            ON CONFLICT(event_id) DO NOTHING
        """
        if connection is not None:
            cursor = connection.execute(query, values)
            return bool(cursor.rowcount)
        with self._connect() as conn:
            cursor = conn.execute(query, values)
            return bool(cursor.rowcount)

    def pending(self, limit: int = 100) -> list[OutboxRecord]:
        now = _now().isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT event_id, topic, partition_key, schema_subject, event_json,
                       correlation_id, attempts
                FROM event_outbox
                WHERE status = 'pending'
                  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                ORDER BY created_at
                LIMIT ?
                """,
                (now, limit),
            ).fetchall()
        return [OutboxRecord(**dict(row)) for row in rows]

    def mark_published(self, event_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE event_outbox
                SET status = 'published', published_at = ?, last_error = NULL
                WHERE event_id = ?
                """,
                (_now().isoformat(), event_id),
            )

    def mark_publish_failed(self, event_id: str, error: str, *, backoff_seconds: float) -> None:
        retry_at = (_now() + timedelta(seconds=max(0.0, backoff_seconds))).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE event_outbox
                SET status = 'pending', attempts = attempts + 1,
                    next_attempt_at = ?, last_error = ?
                WHERE event_id = ?
                """,
                (retry_at, error[:500], event_id),
            )

    def begin_consumption(
        self,
        consumer_name: str,
        event: EventEnvelope,
        *,
        topic: str,
        partition: int | None = None,
        offset: int | None = None,
        replay_namespace: str | None = None,
    ) -> bool:
        scoped_consumer = f"{consumer_name}:replay:{replay_namespace}" if replay_namespace else consumer_name
        digest = payload_hash(event)
        now = _now().isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_sha256, status, attempt_count FROM consumed_events WHERE consumer_name = ? AND event_id = ?",
                (scoped_consumer, event.event_id),
            ).fetchone()
            if row is not None:
                if row["payload_sha256"] != digest:
                    raise ValueError("An event_id was redelivered with a different payload")
                if row["status"] == "completed":
                    return False
                conn.execute(
                    """
                    UPDATE consumed_events
                    SET status = 'processing', attempt_count = attempt_count + 1,
                        last_error = NULL, started_at = ?
                    WHERE consumer_name = ? AND event_id = ?
                    """,
                    (now, scoped_consumer, event.event_id),
                )
                return True
            conn.execute(
                """
                INSERT INTO consumed_events (
                    consumer_name, event_id, payload_sha256, topic, partition_id,
                    offset_id, status, attempt_count, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'processing', 1, ?)
                """,
                (scoped_consumer, event.event_id, digest, topic, partition, offset, now),
            )
        return True

    def complete_consumption(self, consumer_name: str, event_id: str, *, replay_namespace: str | None = None) -> None:
        scoped_consumer = f"{consumer_name}:replay:{replay_namespace}" if replay_namespace else consumer_name
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE consumed_events
                SET status = 'completed', completed_at = ?, last_error = NULL
                WHERE consumer_name = ? AND event_id = ?
                """,
                (_now().isoformat(), scoped_consumer, event_id),
            )

    def fail_consumption(
        self,
        consumer_name: str,
        event_id: str,
        error: str,
        *,
        replay_namespace: str | None = None,
    ) -> None:
        scoped_consumer = f"{consumer_name}:replay:{replay_namespace}" if replay_namespace else consumer_name
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE consumed_events SET status = 'failed', last_error = ?
                WHERE consumer_name = ? AND event_id = ?
                """,
                (error[:500], scoped_consumer, event_id),
            )

    def health(self) -> dict[str, Any]:
        with self._connect() as conn:
            pending = conn.execute("SELECT COUNT(*) AS n FROM event_outbox WHERE status = 'pending'").fetchone()["n"]
            published = conn.execute("SELECT COUNT(*) AS n FROM event_outbox WHERE status = 'published'").fetchone()["n"]
            retrying = conn.execute(
                "SELECT COUNT(*) AS n FROM event_outbox WHERE status = 'pending' AND attempts > 0"
            ).fetchone()["n"]
            publish_attempts = conn.execute(
                "SELECT COALESCE(SUM(attempts), 0) AS n FROM event_outbox"
            ).fetchone()["n"]
            failed = conn.execute("SELECT COUNT(*) AS n FROM consumed_events WHERE status = 'failed'").fetchone()["n"]
            consumption_attempts = conn.execute(
                "SELECT COALESCE(SUM(attempt_count), 0) AS n FROM consumed_events"
            ).fetchone()["n"]
            oldest = conn.execute(
                "SELECT MIN(created_at) AS oldest FROM event_outbox WHERE status = 'pending'"
            ).fetchone()["oldest"]
        return {
            "status": "ready",
            "pending": pending,
            "published": published,
            "retrying": retrying,
            "publish_attempts": publish_attempts,
            "consumption_attempts": consumption_attempts,
            "failed_consumptions": failed,
            "oldest_pending_at": oldest,
        }

    def create_replay_job(
        self,
        replay_job_id: str,
        topic: str,
        *,
        correlation_id: str | None,
        partition: int | None,
        start_offset: int | None,
        end_offset: int | None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO event_replay_jobs (
                    replay_job_id, topic, correlation_id, partition_id, start_offset,
                    end_offset, status, requested_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?)
                """,
                (replay_job_id, topic, correlation_id, partition, start_offset, end_offset, _now().isoformat()),
            )

    def finish_replay_job(
        self,
        replay_job_id: str,
        *,
        processed_count: int,
        skipped_count: int,
        error: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE event_replay_jobs
                SET status = ?, completed_at = ?, processed_count = ?, skipped_count = ?, error = ?
                WHERE replay_job_id = ?
                """,
                (
                    "failed" if error else "completed",
                    _now().isoformat(),
                    processed_count,
                    skipped_count,
                    error[:500] if error else None,
                    replay_job_id,
                ),
            )

    def _connect(self):
        return connect(self.target)

    def _init_sqlite(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS event_outbox (
                    event_id TEXT PRIMARY KEY, topic TEXT NOT NULL, partition_key TEXT NOT NULL,
                    schema_subject TEXT NOT NULL, event_json TEXT NOT NULL, correlation_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT, last_error TEXT, created_at TEXT NOT NULL, published_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_event_outbox_pending
                    ON event_outbox(status, next_attempt_at, created_at);
                CREATE INDEX IF NOT EXISTS idx_event_outbox_correlation
                    ON event_outbox(correlation_id, created_at);
                CREATE TABLE IF NOT EXISTS consumed_events (
                    consumer_name TEXT NOT NULL, event_id TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
                    topic TEXT NOT NULL, partition_id INTEGER, offset_id INTEGER, status TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 1, last_error TEXT, started_at TEXT NOT NULL,
                    completed_at TEXT, PRIMARY KEY (consumer_name, event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_consumed_events_status
                    ON consumed_events(consumer_name, status, started_at);
                CREATE TABLE IF NOT EXISTS event_replay_jobs (
                    replay_job_id TEXT PRIMARY KEY, topic TEXT NOT NULL, correlation_id TEXT,
                    partition_id INTEGER, start_offset INTEGER, end_offset INTEGER, status TEXT NOT NULL,
                    requested_at TEXT NOT NULL, completed_at TEXT, processed_count INTEGER NOT NULL DEFAULT 0,
                    skipped_count INTEGER NOT NULL DEFAULT 0, error TEXT
                );
                """
            )
