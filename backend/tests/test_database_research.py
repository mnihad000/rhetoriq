"""Real graph execution over durable database jobs, without external services."""
from __future__ import annotations

import pytest

from config import get_settings
from events.worker import DatabaseEventWorker
from models.research import ResearchActionDecision
from services.autonomous_research import AutonomousResearchEngine, ResearchRunManager, ResearchSupervisor
from services.database import connect
from services.event_handlers import EventHandlers
from services.investigation_repository import InvestigationRepository
from services.job_queue import JobQueue, LeaseLostError
from services.research_repository import ResearchRepository
from services.research_tools import ResearchToolRegistry, ToolOutcome
from tests.projection_fixtures import build_seed_documents
from tests.test_a2_research_runtime import _plan


def setup_runtime(tmp_path, monkeypatch, target_override=None):
    settings = get_settings()
    target = target_override or str(tmp_path / "research.sqlite3")
    for name, value in {
        "DEMO_MODE": True, "DATABASE_URL": target_override or "", "INVESTIGATION_DB_PATH": target,
        "RESEARCH_CHECKPOINT_DB_PATH": str(tmp_path / "checkpoints.sqlite3"),
        "ENABLE_VECTOR_SEARCH": False, "ENABLE_INVESTIGATION_CACHE": False,
        "ENABLE_B5_RETRIEVAL": False, "JOB_RETRY_BACKOFF_SECONDS": 0.01,
    }.items():
        monkeypatch.setattr(settings, name, value)
    repository = InvestigationRepository(target)
    audit = ResearchRepository(target)
    manager = ResearchRunManager(repository, audit)
    monkeypatch.setattr("services.autonomous_research.get_research_manager", lambda: manager)
    plan = _plan()
    repository.save_plan("inv_database", plan.query_text, plan)
    queue = JobQueue(target)
    worker = DatabaseEventWorker(queue=queue, store=audit.events, handlers=EventHandlers(target))
    calls = []

    def acquire(_registry, decision, _plan, _candidates):
        calls.append(decision.action_type)
        return ToolOutcome(provider="recorded", documents=build_seed_documents("research", 4))

    monkeypatch.setattr(ResearchToolRegistry, "execute", acquire)
    monkeypatch.setattr(ResearchSupervisor, "select", lambda *_args, **_kwargs: ResearchActionDecision(
        action_type="federal_register_search", retrieval_lane="official", query="public records",
        action_summary="Retrieve recorded first-party evidence for the requested investigation.",
        expected_evidence="Captured source text and acquisition receipts.",
    ))
    return manager, audit, repository, queue, worker, calls


def drain_until_terminal(worker, audit, run_id, limit=300):
    for _ in range(limit):
        run = audit.get_run(run_id)
        if run.status not in {"queued", "running"}:
            return run
        worker.run_once()
    raise AssertionError("Database worker did not reach a terminal research outcome")


def test_database_worker_runs_graph_and_replays_recorded_evidence_without_network(tmp_path, monkeypatch):
    manager, audit, repository, queue, worker, calls = setup_runtime(tmp_path, monkeypatch)
    run = manager.start("inv_database")
    assert run.status == "queued"
    assert calls == []  # admission never executes research
    with pytest.raises(LeaseLostError):
        manager.execute_queued(run.run_id)
    assert audit.get_run(run.run_id).status == "queued"
    terminal = drain_until_terminal(worker, audit, run.run_id)
    assert terminal.status in {"completed", "insufficient_evidence"}
    assert len(audit.get_documents(run.run_id)) == 4
    assert audit.list_actions(run.run_id)[0].receipt_ids
    assert audit.get_evaluation(run.run_id) is not None
    assert calls == ["federal_register_search"]
    events = audit.list_events(run.run_id, 0, 500)
    assert [item.sequence for item in events] == list(range(1, len(events) + 1))
    assert any(item.event_type == "run.completed" for item in events)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Recorded replay must not acquire new sources")

    monkeypatch.setattr(ResearchToolRegistry, "execute", forbidden)
    replay = manager.replay("inv_database", run.run_id)
    assert replay.status == "queued"
    replay_terminal = drain_until_terminal(worker, audit, replay.run_id)
    assert replay_terminal.status in {"completed", "insufficient_evidence"}
    assert len(audit.get_documents(replay.run_id)) == 4
    assert audit.get_replay_comparison(replay.run_id)["status"] == "completed"


def test_crash_retry_resumes_checkpoint_without_reacquiring_sources(tmp_path, monkeypatch):
    manager, audit, repository, queue, worker, calls = setup_runtime(tmp_path, monkeypatch)
    original = AutonomousResearchEngine._build_artifacts
    crashed = []

    def fail_once(engine, state):
        if not crashed:
            crashed.append(True)
            raise RuntimeError("Injected worker interruption before artifact construction")
        return original(engine, state)

    monkeypatch.setattr(AutonomousResearchEngine, "_build_artifacts", fail_once)
    run = manager.start("inv_database")
    assert worker.run_once() is False
    assert audit.get_run(run.run_id).status == "queued"
    assert len(audit.get_documents(run.run_id)) == 4
    with connect(audit.db_path) as db:
        db.execute("UPDATE event_outbox SET next_attempt_at = NULL WHERE topic='investigations.requested.v1'")
    terminal = drain_until_terminal(worker, audit, run.run_id)
    assert terminal.status in {"completed", "insufficient_evidence"}
    assert calls == ["federal_register_search"]
    with connect(audit.db_path) as db:
        request = db.execute("SELECT attempts, status FROM event_outbox WHERE topic='investigations.requested.v1'").fetchone()
    assert request["attempts"] == 2
    assert request["status"] == "completed"


def test_completed_result_survives_crash_before_job_ack_at_retry_limit(tmp_path, monkeypatch):
    manager, audit, repository, queue, worker, calls = setup_runtime(tmp_path, monkeypatch)
    original = queue.complete

    def crash_before_ack(_job):
        raise RuntimeError("Injected interruption before durable job acknowledgment")

    monkeypatch.setattr(queue, "complete", crash_before_ack)
    run = manager.start("inv_database")
    with pytest.raises(RuntimeError):
        worker.run_once()
    status = audit.get_run(run.run_id).status
    assert status in {"completed", "insufficient_evidence"}
    monkeypatch.setattr(queue, "complete", original)
    with connect(audit.db_path) as db:
        db.execute("UPDATE event_outbox SET attempts=?, lease_until='2000-01-01 00:00:00' WHERE topic='investigations.requested.v1'",
                   (get_settings().JOB_MAX_ATTEMPTS,))
    assert worker.run_once() is True
    assert audit.get_run(run.run_id).status == status
    assert calls == ["federal_register_search"]
    with connect(audit.db_path) as db:
        assert db.execute("SELECT status FROM event_outbox WHERE topic='investigations.requested.v1'").fetchone()["status"] == "completed"
