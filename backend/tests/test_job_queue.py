from __future__ import annotations

from datetime import datetime, timezone
import time

import pytest

from events.worker import DatabaseEventWorker
from models.events import PipelineEvaluationEvent, PipelineEvaluationPayload, PIPELINE_EVALUATED_TOPIC
from services.database import connect
from services.event_store import EventStore
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


def test_legacy_published_rows_are_requeued_once(tmp_path):
    target = str(tmp_path / "legacy.sqlite3")
    queue = JobQueue(target)
    with connect(target) as conn:
        conn.execute(
            "INSERT INTO event_outbox(event_id, topic, event_json, created_at, status, published_at) VALUES (?, ?, ?, ?, 'published', ?)",
            ("legacy-1", PIPELINE_EVALUATED_TOPIC, _pipeline_event("legacy-1"), "2026-01-01 00:00:00.000", "2026-01-01 00:01:00.000"),
        )
    # Reinitialization is the migration boundary; running it twice must not
    # create a second delivery or undo a completed job.
    JobQueue(target)
    with connect(target) as conn:
        row = conn.execute("SELECT status, published_at FROM event_outbox WHERE event_id = ?", ("legacy-1",)).fetchone()
        assert (row[0], row[1]) == ("pending", None)
    job = queue.claim("worker-legacy", 30)
    assert job is not None
    assert queue.complete(job) is True
    JobQueue(target)
    with connect(target) as conn:
        assert conn.execute("SELECT status FROM event_outbox WHERE event_id = ?", ("legacy-1",)).fetchone()[0] == "completed"


@pytest.mark.parametrize("status", ["pending", "published"])
def test_legacy_publication_attempts_do_not_exhaust_processing_budget(tmp_path, status):
    target = str(tmp_path / "legacy-budget.sqlite3")
    store = EventStore(target)  # original schema before the queue upgrade
    event = PipelineEvaluationEvent.model_validate_json(_pipeline_event("legacy-budget"))
    store.enqueue(PIPELINE_EVALUATED_TOPIC, event)
    with connect(target) as conn:
        conn.execute("UPDATE event_outbox SET status=?, attempts=25, next_attempt_at='2099-01-01', last_error='old publisher failure'",
                     (status,))
    queue = JobQueue(target)
    claimed = queue.claim("new-worker", 60, max_attempts=3)
    assert claimed is not None and claimed.attempts == 1 and not claimed.finalize_only
    assert queue.fail(claimed, "processing retry", max_attempts=3, backoff_seconds=0)
    JobQueue(target)
    with connect(target) as conn:
        row = conn.execute("SELECT attempts, legacy_publish_attempts FROM event_outbox WHERE event_id=?", (event.event_id,)).fetchone()
    assert (row[0], row[1]) == (1, 25)


class _RecordingHandlers:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.events = []

    def handler_for(self, _topic):
        return "test-consumer-v1", self.handle

    def handle(self, event):
        self.events.append(event.event_id)
        if self.fail:
            raise RuntimeError("handler failure")


def _pipeline_event(event_id: str = "pipeline-1") -> str:
    event = PipelineEvaluationEvent.create(
        PipelineEvaluationPayload(
            evaluated_at=datetime.now(timezone.utc), status="healthy"
        ),
        producer="test",
        correlation_id="corr-1",
        partition_key="pipeline",
        event_id=event_id,
    )
    return event.model_dump_json()


def test_database_worker_run_once_records_ledger_and_completes_job(tmp_path):
    target = str(tmp_path / "worker.sqlite3")
    queue = JobQueue(target)
    store = EventStore(target)
    event_json = _pipeline_event()
    with connect(target) as conn:
        conn.execute(
            "INSERT INTO event_outbox(event_id, topic, event_json, created_at) VALUES (?, ?, ?, ?)",
            ("pipeline-1", PIPELINE_EVALUATED_TOPIC, event_json, "2026-01-01 00:00:00.000"),
        )
    handlers = _RecordingHandlers()
    worker = DatabaseEventWorker(queue=queue, store=store, handlers=handlers)
    assert worker.run_once() is True
    assert handlers.events == ["pipeline-1"]
    with connect(target) as conn:
        assert conn.execute("SELECT status FROM event_outbox WHERE event_id = ?", ("pipeline-1",)).fetchone()[0] == "completed"
        assert conn.execute("SELECT status FROM consumed_events WHERE event_id = ?", ("pipeline-1",)).fetchone()[0] == "completed"


def test_worker_does_not_execute_after_retry_budget_is_exhausted(tmp_path, monkeypatch):
    target = str(tmp_path / "retry.sqlite3")
    queue = JobQueue(target)
    store = EventStore(target)
    with connect(target) as conn:
        conn.execute(
            "INSERT INTO event_outbox(event_id, topic, event_json, created_at) VALUES (?, ?, ?, ?)",
            ("pipeline-1", PIPELINE_EVALUATED_TOPIC, _pipeline_event(), "2026-01-01 00:00:00.000"),
        )
    handlers = _RecordingHandlers(fail=True)
    worker = DatabaseEventWorker(queue=queue, store=store, handlers=handlers)
    monkeypatch.setattr(worker.settings, "JOB_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(worker.settings, "JOB_RETRY_BACKOFF_SECONDS", 0)
    assert worker.run_once() is False
    assert worker.run_once() is False
    assert worker.run_once() is False
    assert handlers.events == ["pipeline-1", "pipeline-1"]
    with connect(target) as conn:
        row = conn.execute("SELECT status, attempts FROM event_outbox WHERE event_id = ?", ("pipeline-1",)).fetchone()
        assert (row[0], row[1]) == ("failed", 2)


def test_expired_claim_at_retry_limit_is_finalized_without_handler_execution(tmp_path, monkeypatch):
    target = str(tmp_path / "expired.sqlite3")
    queue = JobQueue(target)
    store = EventStore(target)
    with connect(target) as conn:
        conn.execute(
            "INSERT INTO event_outbox(event_id, topic, event_json, created_at) VALUES (?, ?, ?, ?)",
            ("pipeline-1", PIPELINE_EVALUATED_TOPIC, _pipeline_event(), "2026-01-01 00:00:00.000"),
        )
    for _ in range(2):
        claimed = queue.claim("crashed-worker", 30, max_attempts=2)
        assert claimed is not None and not claimed.finalize_only
        with connect(target) as conn:
            conn.execute("UPDATE event_outbox SET lease_until = '2000-01-01' WHERE event_id = ?", (claimed.event_id,))
    handlers = _RecordingHandlers()
    worker = DatabaseEventWorker(queue=queue, store=store, handlers=handlers)
    monkeypatch.setattr(worker.settings, "JOB_MAX_ATTEMPTS", 2)
    assert worker.run_once() is False
    assert handlers.events == []
    assert queue.health()["failed"] == 1
