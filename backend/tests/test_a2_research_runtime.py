from __future__ import annotations

import socket
import time
import json
import sqlite3

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

from config import get_settings
from demo_data import DEMO_DOCUMENTS
from models.investigation import InvestigationPlan, InvestigationPlanTimeWindow
from models.research import ResearchActionDecision, ResearchBudgetLimits, ResearchBudgetUsage
from services.autonomous_research import AutonomousResearchEngine, ResearchRunManager
from services.investigation_repository import InvestigationRepository
from services.research_budget import BudgetExceeded, ResearchBudget
from services.research_repository import ResearchRepository
from services.research_tools import ResearchToolRegistry
from services.url_policy import PublicUrlPolicy, UrlPolicyError, registrable_domain


def _plan() -> InvestigationPlan:
    return InvestigationPlan(
        query_text="Trace the origin and spread of the hidden energy tax narrative",
        topic="hidden energy tax",
        canonical_phrase="hidden energy tax",
        intent="origin",
        search_queries=["hidden energy tax origin"],
        semantic_queries=["hidden energy tax provenance"],
        target_source_types=["local_news", "national_news", "official"],
        requested_outputs=["timeline", "receipts", "source_diversity"],
        time_window=InvestigationPlanTimeWindow(label="all_time"),
        retrieval_mode="broad",
    )


def _search_decision() -> ResearchActionDecision:
    return ResearchActionDecision(
        action_type="web_search",
        retrieval_lane="discovery",
        query="hidden energy tax origin",
        action_summary="Searching for independent sources that establish the narrative's origin.",
        expected_evidence="Dated canonical sources from independent publishers.",
    )


def test_research_repository_is_idempotent_and_events_are_monotonic(tmp_path):
    audit = ResearchRepository(str(tmp_path / "research.sqlite3"))
    run = audit.create_run("inv_a2", ResearchBudgetLimits())
    assert audit.create_run("inv_a2", ResearchBudgetLimits()).run_id == run.run_id
    assert audit.claim_run(run.run_id, "worker-a", 45)

    first = audit.start_action(run.run_id, _search_decision(), idempotency_key="stable-key")
    second = audit.start_action(run.run_id, _search_decision(), idempotency_key="stable-key")
    assert first.action_id == second.action_id
    audit.finish_action(first.action_id, status="completed", result_count=2)

    events = audit.list_events(run.run_id)
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert len(audit.list_actions(run.run_id)) == 1


def test_budget_blocks_actions_before_crossing_limits():
    usage = ResearchBudgetUsage(tool_calls=1, canonical_fetches=1)
    budget = ResearchBudget(
        ResearchBudgetLimits(tool_calls=1, canonical_fetches=1),
        usage,
    )
    decision = ResearchActionDecision(
        action_type="canonical_fetch",
        retrieval_lane="corroboration",
        candidate_id="candidate_1234567890abcdef",
        action_summary="Retrieving the canonical source for verification.",
        expected_evidence="A normalized canonical document and receipt.",
    )
    with pytest.raises(BudgetExceeded):
        budget.require_action(decision, "example.com")
    assert usage.tool_calls == 1
    assert usage.canonical_fetches == 1


def test_public_url_policy_blocks_private_network_and_credentials(monkeypatch):
    def resolve(hostname, *_args, **_kwargs):
        address = "10.0.0.8" if hostname == "private.example" else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    policy = PublicUrlPolicy(enforce_robots=False)

    assert policy.validate("https://example.com/evidence") == "https://example.com/evidence"
    with pytest.raises(UrlPolicyError):
        policy.validate("https://private.example/admin")
    with pytest.raises(UrlPolicyError):
        policy.validate("https://user:secret@example.com/evidence")
    with pytest.raises(UrlPolicyError):
        policy.validate("https://example.com/evidence?api_key=do-not-store")
    with pytest.raises(UrlPolicyError):
        policy.validate("file:///etc/passwd")
    assert registrable_domain("https://forums.bbc.co.uk/story") == "bbc.co.uk"


def test_precollected_graph_run_reaches_audited_terminal_state(tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    monkeypatch.setattr(settings, "ENABLE_VECTOR_SEARCH", False)
    monkeypatch.setattr(settings, "ENABLE_INVESTIGATION_CACHE", False)
    monkeypatch.setattr(settings, "RESEARCH_CHECKPOINT_DB_PATH", str(tmp_path / "checkpoints.sqlite3"))

    db_path = str(tmp_path / "investigations.sqlite3")
    repository = InvestigationRepository(db_path)
    audit = ResearchRepository(db_path)
    repository.save_plan("inv_graph", _plan().query_text, _plan())
    run = audit.create_run("inv_graph", ResearchBudgetLimits())
    for document in DEMO_DOCUMENTS[:8]:
        audit.save_document(run.run_id, document)

    assert audit.claim_run(run.run_id, "test-worker", 45)
    AutonomousResearchEngine(repository, audit).execute(run.run_id)

    finished = audit.get_run(run.run_id)
    assert finished is not None
    assert finished.status in {"completed", "insufficient_evidence"}
    assert finished.terminal_decision in {"published", "insufficient_evidence"}
    assert audit.get_evaluation(run.run_id) is not None
    event_types = [event.event_type for event in audit.list_events(run.run_id, limit=500)]
    assert "gate.evaluated" in event_types
    assert "run.completed" in event_types
    assert "run.failed" not in event_types
    assert audit.get_candidate_report(run.run_id) is not None
    terminal_workspace = repository.get_investigation_workspace("inv_graph")
    if finished.status == "insufficient_evidence":
        assert terminal_workspace.report is None
        assert "withhold_report" in {
            event.payload.get("node") for event in audit.list_events(run.run_id, limit=500)
        }

    with sqlite3.connect(settings.RESEARCH_CHECKPOINT_DB_PATH) as checkpoint_connection:
        saver = SqliteSaver(checkpoint_connection)
        checkpoints = list(saver.list({"configurable": {"thread_id": run.run_id}}))
    serialized_checkpoints = json.dumps([item.checkpoint for item in checkpoints], default=str)
    assert '"candidates"' not in serialized_checkpoints
    assert DEMO_DOCUMENTS[0].text[:80] not in serialized_checkpoints

    def forbid_network(*_args, **_kwargs):
        raise AssertionError("recorded replay attempted a live research tool")

    monkeypatch.setattr(ResearchToolRegistry, "execute", forbid_network)
    manager = ResearchRunManager(repository, audit)
    replay = manager.replay("inv_graph", run.run_id)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        replay = audit.get_run(replay.run_id)
        if replay and replay.status not in {"queued", "running"}:
            break
        time.sleep(0.05)
    manager.executor.shutdown(wait=True)

    assert replay is not None
    assert replay.status in {"completed", "insufficient_evidence"}
    comparison = audit.get_replay_comparison(replay.run_id)
    assert comparison is not None
    assert comparison["action_equivalence"] == 1.0
    assert comparison["evaluation_equivalent"] is True
