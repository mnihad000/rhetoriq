"""Opt-in PostgreSQL qualification in a new, disposable database per test."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pytest

from services.database import connect
from services.event_store import EventStore
from services.job_queue import JobQueue, LeaseLostError
from tests.projection_fixtures import build_seed_document
from services.event_factory import raw_event_from_document

pytestmark = pytest.mark.integration


@pytest.fixture
def disposable_database():
    target = os.getenv("POSTGRES_TEST_DATABASE_URL", "")
    if not target:
        pytest.skip("POSTGRES_TEST_DATABASE_URL is required")
    parsed = urlsplit(target)
    if parsed.scheme not in {"postgres", "postgresql"} or "test" not in parsed.path.lower():
        pytest.fail("Queue integration requires an explicitly disposable PostgreSQL test database")
    import psycopg
    from psycopg import sql
    name = "rhetoriq_jobs_test_" + uuid4().hex
    with psycopg.connect(target, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    isolated = urlunsplit(parsed._replace(path="/" + name))
    try:
        yield isolated
    finally:
        with psycopg.connect(target, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def seed(target):
    store = EventStore(target)
    event = raw_event_from_document(build_seed_document("queue", 0), producer="test", correlation_id="queue-test")
    store.enqueue("raw.documents.v1", event)
    return event


def test_postgres_claim_takeover_and_commit_fencing(disposable_database):
    target = disposable_database
    queue = JobQueue(target)
    event = seed(target)
    with ThreadPoolExecutor(max_workers=6) as pool:
        claims = list(pool.map(lambda index: queue.claim(f"worker-{index}", 60), range(6)))
    claimed = [job for job in claims if job is not None]
    assert len(claimed) == 1
    first = claimed[0]
    with connect(target) as db:
        db.execute("CREATE TABLE job_commit_probe (value TEXT)")
        db.execute("UPDATE event_outbox SET lease_until='2000-01-01 00:00:00' WHERE event_id=?", (event.event_id,))
    second = queue.claim("takeover", 60)
    assert second is not None and second.lease_generation == first.lease_generation + 1
    with pytest.raises(LeaseLostError), queue.fenced(first), connect(target) as db:
        db.execute("INSERT INTO job_commit_probe(value) VALUES ('stale')")
    with connect(target) as db:
        assert db.execute("SELECT COUNT(*) AS n FROM job_commit_probe").fetchone()["n"] == 0
    assert queue.complete(first) is False
    assert queue.complete(second) is True
    assert queue.claim("third", 60) is None


def test_postgres_graph_execution_and_recorded_replay(disposable_database, tmp_path, monkeypatch):
    from tests.test_database_research import setup_runtime, drain_until_terminal
    from services.research_tools import ResearchToolRegistry

    manager, audit, repository, queue, worker, calls = setup_runtime(tmp_path, monkeypatch, disposable_database)
    run = manager.start("inv_database")
    assert drain_until_terminal(worker, audit, run.run_id).status in {"completed", "insufficient_evidence"}
    assert len(audit.get_documents(run.run_id)) == 4

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Recorded replay must use saved evidence")

    monkeypatch.setattr(ResearchToolRegistry, "execute", forbidden)
    replay = manager.replay("inv_database", run.run_id)
    assert drain_until_terminal(worker, audit, replay.run_id).status in {"completed", "insufficient_evidence"}
    assert audit.get_replay_comparison(replay.run_id)["status"] == "completed"
