"""Database-backed durable jobs built on the event outbox table.

The queue deliberately has no transport dependency.  A claimed row is leased
with an owner and generation, so a late worker cannot acknowledge or mutate a
job after another worker has taken it over.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import re
from typing import Any, Iterator

from services.database import connect, ensure_parent_dir, is_postgres_database


class LeaseLostError(RuntimeError):
    """Raised when a fenced worker attempts to commit after lease loss."""


@dataclass(frozen=True)
class Job:
    event_id: str
    topic: str
    event_json: str
    lease_owner: str
    lease_generation: int
    lease_until: str | None
    attempts: int


_CURRENT_JOB: ContextVar[Job | None] = ContextVar("current_job", default=None)


def current_job() -> Job | None:
    return _CURRENT_JOB.get()


_SECRET_PATTERN = re.compile(
    r"(?i)(password|secret|token|authorization|api[_-]?key)\s*[=:]\s*[^\s,;]+"
)
_URL_CREDENTIAL_PATTERN = re.compile(r"(?i)(://[^:/\s]+:)[^@/\s]+@")


def _safe_error(error: str | BaseException) -> str:
    value = str(error)
    value = _URL_CREDENTIAL_PATTERN.sub(r"\1<redacted>@", value)
    value = _SECRET_PATTERN.sub(r"\1=<redacted>", value)
    value = re.sub(r"(?i)\bBearer\s+[^\s,;\"']+", "Bearer <redacted>", value)
    return value[:500]


def _row_value(row: Any, key: str, index: int):
    if hasattr(row, "keys"):
        return row[key]
    return row[index]


class JobQueue:
    def __init__(self, target: str) -> None:
        self.target = target
        ensure_parent_dir(target)
        if is_postgres_database(target):
            from migrations.runner import run_migrations
            run_migrations(target)
        else:
            self._ensure_sqlite_schema()

    def _connect(self):
        return connect(self.target)

    def _ensure_sqlite_schema(self) -> None:
        """Add job columns to both fresh and pre-queue local databases."""
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS event_outbox (
                    event_id TEXT PRIMARY KEY, topic TEXT NOT NULL,
                    partition_key TEXT NOT NULL DEFAULT '', schema_subject TEXT NOT NULL DEFAULT '',
                    event_json TEXT NOT NULL, correlation_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT, last_error TEXT, created_at TEXT NOT NULL,
                    published_at TEXT, lease_owner TEXT, lease_generation INTEGER NOT NULL DEFAULT 0,
                    lease_until TEXT, finished_at TEXT
                )
                """
            )
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(event_outbox)").fetchall()
            }
            additions = {
                "lease_owner": "TEXT",
                "lease_generation": "INTEGER NOT NULL DEFAULT 0",
                "lease_until": "TEXT",
                "finished_at": "TEXT",
            }
            for column, definition in additions.items():
                if column not in columns:
                    conn.execute(f"ALTER TABLE event_outbox ADD COLUMN {column} {definition}")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_event_outbox_jobs "
                "ON event_outbox(status, next_attempt_at, lease_until, created_at)"
            )
            # Rows published by the old Kafka outbox are durable jobs in the
            # new deployment.  This is idempotent and completed jobs are not
            # touched on subsequent starts.
            conn.execute(
                "UPDATE event_outbox SET status = 'pending', published_at = NULL "
                "WHERE status = 'published'"
            )

    @staticmethod
    def _job_from_row(row: Any) -> Job:
        return Job(
            event_id=_row_value(row, "event_id", 0),
            topic=_row_value(row, "topic", 1),
            event_json=_row_value(row, "event_json", 2),
            lease_owner=_row_value(row, "lease_owner", 3),
            lease_generation=int(_row_value(row, "lease_generation", 4)),
            lease_until=_row_value(row, "lease_until", 5),
            attempts=int(_row_value(row, "attempts", 6)),
        )

    def claim(self, worker_id: str, lease_seconds: float) -> Job | None:
        lease_seconds = max(0.1, float(lease_seconds))
        if is_postgres_database(self.target):
            return self._claim_postgres(worker_id, lease_seconds)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT event_id, topic, event_json, lease_owner, lease_generation,
                       lease_until, attempts
                FROM event_outbox
                WHERE (status = 'pending' AND
                       (next_attempt_at IS NULL OR julianday(next_attempt_at) <= julianday('now')))
                   OR (status = 'running' AND lease_until IS NOT NULL
                       AND julianday(lease_until) <= julianday('now'))
                ORDER BY created_at
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            event_id = _row_value(row, "event_id", 0)
            cursor = conn.execute(
                """
                UPDATE event_outbox
                SET status = 'running', lease_owner = ?,
                    lease_generation = lease_generation + 1,
                    lease_until = strftime('%Y-%m-%d %H:%M:%f', 'now', '+' || ? || ' seconds'),
                    attempts = attempts + 1, finished_at = NULL
                WHERE event_id = ? AND (
                    (status = 'pending' AND
                     (next_attempt_at IS NULL OR julianday(next_attempt_at) <= julianday('now')))
                    OR (status = 'running' AND lease_until IS NOT NULL
                        AND julianday(lease_until) <= julianday('now'))
                )
                """,
                (worker_id, lease_seconds, event_id),
            )
            if cursor.rowcount != 1:
                return None
            claimed = conn.execute(
                """
                SELECT event_id, topic, event_json, lease_owner, lease_generation,
                       lease_until, attempts
                FROM event_outbox WHERE event_id = ?
                """,
                (event_id,),
            ).fetchone()
            return self._job_from_row(claimed)

    def _claim_postgres(self, worker_id: str, lease_seconds: float) -> Job | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                WITH candidate AS (
                    SELECT event_id
                    FROM event_outbox
                    WHERE (status = 'pending' AND
                           (next_attempt_at IS NULL OR next_attempt_at::timestamptz <= clock_timestamp()))
                       OR (status = 'running' AND lease_until IS NOT NULL
                           AND lease_until::timestamptz <= clock_timestamp())
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE event_outbox AS jobs
                SET status = 'running', lease_owner = ?,
                    lease_generation = jobs.lease_generation + 1,
                    lease_until = to_char(
                        (clock_timestamp() + (? * INTERVAL '1 second')) AT TIME ZONE 'UTC',
                        'YYYY-MM-DD HH24:MI:SS.MS'
                    ),
                    attempts = jobs.attempts + 1, finished_at = NULL
                FROM candidate
                WHERE jobs.event_id = candidate.event_id
                RETURNING jobs.event_id, jobs.topic, jobs.event_json, jobs.lease_owner,
                          jobs.lease_generation, jobs.lease_until, jobs.attempts
                """,
                (worker_id, lease_seconds),
            ).fetchone()
            return self._job_from_row(row) if row else None

    def renew(self, job: Job, lease_seconds: float) -> bool:
        lease_seconds = max(0.1, float(lease_seconds))
        with self._connect() as conn:
            if is_postgres_database(self.target):
                cursor = conn.execute(
                    """
                    UPDATE event_outbox
                    SET lease_until = to_char(
                        (clock_timestamp() + (? * INTERVAL '1 second')) AT TIME ZONE 'UTC',
                        'YYYY-MM-DD HH24:MI:SS.MS'
                    )
                    WHERE event_id = ? AND status = 'running' AND lease_owner = ?
                      AND lease_generation = ?
                      AND lease_until::timestamptz > clock_timestamp()
                    """,
                    (lease_seconds, job.event_id, job.lease_owner, job.lease_generation),
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE event_outbox
                    SET lease_until = strftime('%Y-%m-%d %H:%M:%f', 'now', '+' || ? || ' seconds')
                    WHERE event_id = ? AND status = 'running' AND lease_owner = ?
                      AND lease_generation = ?
                      AND lease_until > strftime('%Y-%m-%d %H:%M:%f', 'now')
                    """,
                    (lease_seconds, job.event_id, job.lease_owner, job.lease_generation),
                )
            return cursor.rowcount == 1

    def complete(self, job: Job) -> bool:
        with self._connect() as conn:
            if is_postgres_database(self.target):
                cursor = conn.execute(
                    """
                    UPDATE event_outbox SET status = 'completed', finished_at =
                        to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.MS'),
                        lease_until = NULL, last_error = NULL
                    WHERE event_id = ? AND status = 'running' AND lease_owner = ?
                      AND lease_generation = ? AND lease_until::timestamptz > clock_timestamp()
                    """,
                    (job.event_id, job.lease_owner, job.lease_generation),
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE event_outbox SET status = 'completed', finished_at =
                        strftime('%Y-%m-%d %H:%M:%f', 'now'), lease_until = NULL, last_error = NULL
                    WHERE event_id = ? AND status = 'running' AND lease_owner = ?
                      AND lease_generation = ? AND lease_until > strftime('%Y-%m-%d %H:%M:%f', 'now')
                    """,
                    (job.event_id, job.lease_owner, job.lease_generation),
                )
            return cursor.rowcount == 1

    def fail(self, job: Job, error: str | BaseException, max_attempts: int, backoff_seconds: float) -> bool:
        safe_error = _safe_error(error)
        max_attempts = max(1, int(max_attempts))
        backoff_seconds = max(0.0, float(backoff_seconds))
        with self._connect() as conn:
            if is_postgres_database(self.target):
                cursor = conn.execute(
                    """
                    UPDATE event_outbox
                    SET status = CASE WHEN attempts >= ? THEN 'failed' ELSE 'pending' END,
                        next_attempt_at = CASE WHEN attempts >= ? THEN NULL ELSE
                            to_char((clock_timestamp() + (? * INTERVAL '1 second')) AT TIME ZONE 'UTC',
                                    'YYYY-MM-DD HH24:MI:SS.MS') END,
                        last_error = ?, lease_until = NULL,
                        finished_at = CASE WHEN attempts >= ? THEN
                            to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.MS')
                            ELSE NULL END
                    WHERE event_id = ? AND status = 'running' AND lease_owner = ?
                      AND lease_generation = ? AND lease_until::timestamptz > clock_timestamp()
                    """,
                    (max_attempts, max_attempts, backoff_seconds, safe_error, max_attempts,
                     job.event_id, job.lease_owner, job.lease_generation),
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE event_outbox
                    SET status = CASE WHEN attempts >= ? THEN 'failed' ELSE 'pending' END,
                        next_attempt_at = CASE WHEN attempts >= ? THEN NULL ELSE
                            strftime('%Y-%m-%d %H:%M:%f', 'now', '+' || ? || ' seconds') END,
                        last_error = ?, lease_until = NULL,
                        finished_at = CASE WHEN attempts >= ? THEN strftime('%Y-%m-%d %H:%M:%f', 'now')
                                           ELSE NULL END
                    WHERE event_id = ? AND status = 'running' AND lease_owner = ?
                      AND lease_generation = ?
                      AND lease_until > strftime('%Y-%m-%d %H:%M:%f', 'now')
                    """,
                    (max_attempts, max_attempts, backoff_seconds, safe_error, max_attempts,
                     job.event_id, job.lease_owner, job.lease_generation),
                )
            return cursor.rowcount == 1

    def health(self) -> dict[str, Any]:
        with self._connect() as conn:
            counts = conn.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END), 0) AS pending,
                    COALESCE(SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END), 0) AS running,
                    COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) AS failed,
                    COALESCE(SUM(CASE WHEN status = 'pending' AND attempts > 0 THEN 1 ELSE 0 END), 0) AS retrying
                FROM event_outbox
                """
            ).fetchone()
            oldest = conn.execute(
                "SELECT MIN(created_at) AS oldest FROM event_outbox WHERE status = 'pending'"
            ).fetchone()
        return {
            "status": "ready",
            "pending": int(_row_value(counts, "pending", 0)),
            "running": int(_row_value(counts, "running", 1)),
            "failed": int(_row_value(counts, "failed", 2)),
            "retrying": int(_row_value(counts, "retrying", 3)),
            "oldest_pending_at": _row_value(oldest, "oldest", 0),
        }

    @contextmanager
    def fenced(self, job: Job) -> Iterator[Job]:
        token = _CURRENT_JOB.set(job)
        try:
            yield job
        finally:
            _CURRENT_JOB.reset(token)

