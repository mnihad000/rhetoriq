from __future__ import annotations

import time

import pytest

from services.database import connect
from services.job_queue import JobQueue, LeaseLostError


def _seed(target: str, event_id: str = "event-1") -> None:
    with connect(target) as conn:
        conn.execute(
            "INSERT INTO event_outbox(event_id, topic, event_json, created_at) VALUES (?, ?, ?, ?)",
            (event_id, "documents.processed.v1", "{}", "2026-01-01 00:00:00.000"),
        )


def test_claim_excludes_other_workers_and_completion_is_fenced(tmp_path):
    target = str(tmp_path / "jobs.sqlite3")
    queue = JobQueue(target)
    _seed(target)

    first = queue.claim("worker-a", 30)
    assert first is not None
    assert queue.claim("worker-b", 30) is None
    assert queue.complete(first) is True
    assert queue.claim("worker-b", 30) is None


def test_expired_lease_can_be_taken_over_but_stale_ack_is_rejected(tmp_path):
    target = str(tmp_path / "jobs.sqlite3")
    queue = JobQueue(target)
    _seed(target)
    first = queue.claim("worker-a", 30)
    assert first is not None
    with connect(target) as conn:
        conn.execute("UPDATE event_outbox SET lease_until = '2000-01-01 00:00:00.000' WHERE event_id = ?", (first.event_id,))
    second = queue.claim("worker-b", 30)
    assert second is not None and second.lease_generation > first.lease_generation
    assert queue.complete(first) is False
    assert queue.complete(second) is True


def test_stale_artifact_transaction_rolls_back_before_commit(tmp_path):
    target = str(tmp_path / "jobs.sqlite3")
    queue = JobQueue(target)
    _seed(target)
    job = queue.claim("worker-a", 1)
    assert job is not None
    with pytest.raises(LeaseLostError):
        with queue.fenced(job):
            with connect(target) as conn:
                conn.execute("CREATE TABLE artifact(value TEXT)")
                conn.execute("INSERT INTO artifact(value) VALUES ('stale')")
                time.sleep(1.2)
    with connect(target) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'artifact'").fetchone() is None


def test_retry_exhaustion_is_visible_and_error_is_sanitized(tmp_path):
    target = str(tmp_path / "jobs.sqlite3")
    queue = JobQueue(target)
    _seed(target)
    job = queue.claim("worker-a", 30)
    assert job is not None
    assert queue.fail(job, "password=hunter2", max_attempts=1, backoff_seconds=0) is True
    health = queue.health()
    assert health["failed"] == 1
    with connect(target) as conn:
        error = conn.execute("SELECT last_error FROM event_outbox WHERE event_id = ?", (job.event_id,)).fetchone()[0]
    assert "hunter2" not in error


def test_pending_jobs_survive_queue_restart(tmp_path):
    target = str(tmp_path / "jobs.sqlite3")
    first = JobQueue(target)
    _seed(target)
    assert first.health()["pending"] == 1
    second = JobQueue(target)
    job = second.claim("worker-after-restart", 30)
    assert job is not None
