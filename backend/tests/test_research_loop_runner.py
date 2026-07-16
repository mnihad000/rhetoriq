import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient

from agents.retriever_agent import RetrieverAgent
from main import app
from api import narratives as narratives_api
from config import get_settings
from models.document import Document
from models.investigation import InvestigationPlan, InvestigationPlanTimeWindow
from services.document_normalizer import DocumentNormalizer
from services.investigation_repository import InvestigationRepository
from services.research_loop_runner import InvestigationRunner

client = TestClient(app)


def _plan() -> InvestigationPlan:
    return InvestigationPlan(
        query_text="Where did the hidden energy tax narrative come from?",
        topic="hidden energy tax",
        primary_question="Where did the hidden energy tax narrative come from?",
        canonical_phrase="hidden energy tax",
        intent="origin",
        entities=["hidden", "energy", "tax"],
        target_source_types=["blog", "local_news", "national_news", "speech_transcript"],
        requested_outputs=["timeline", "counter_narratives", "source_diversity", "report", "receipts"],
        search_queries=["\"hidden energy tax\"", "hidden energy tax origin"],
        semantic_queries=["Investigate hidden energy tax origin"],
        time_window=InvestigationPlanTimeWindow(label="all_time"),
        retrieval_mode="broad",
        risk_notes=[],
        uncertainty_requirements=[],
    )


def _html(title: str, body: str, published: str) -> str:
    return (
        f"<html><head><title>{title}</title>"
        f"<meta property='article:published_time' content='{published}'></head>"
        f"<body><article><p>{body}</p></article></body></html>"
    )


class _StubProvider:
    name = "stub"

    def search(self, query, time_window, source_types, limit):
        results = []
        if any(term in query for term in ("earliest", "origin", "\"hidden energy tax\"")):
            results.append(
                narratives_api.SearchResult(
                    query=query,
                    title="Local anchor",
                    url="https://example.com/local-anchor",
                    snippet="The hidden energy tax phrase appears in a local post.",
                    rank=1,
                    provider="stub",
                )
            )
        if any(term in query for term in ("reporting", "independent", "coverage")):
            results.append(
                narratives_api.SearchResult(
                    query=query,
                    title="Independent reporting",
                    url="https://example.com/independent-report",
                    snippet="Independent reporting repeats the hidden energy tax framing.",
                    rank=1,
                    provider="stub",
                )
            )
        if any(term in query for term in ("rebuttal", "fact check", "criticism", "debunking")):
            results.append(
                narratives_api.SearchResult(
                    query=query,
                    title="Counter frame",
                    url="https://example.com/counter-frame",
                    snippet="However, supporters say the policy provides long-term savings.",
                    rank=1,
                    provider="stub",
                )
            )
        if any(term in query for term in ("official", "press release", "transcript")):
            results.append(
                narratives_api.SearchResult(
                    query=query,
                    title="Official remarks",
                    url="https://state.example.gov/remarks",
                    snippet="Public comments have described this as a hidden energy tax.",
                    rank=1,
                    provider="stub",
                )
            )
        return results[:limit]


class _StubFetcher:
    def fetch(self, url):
        if "local-anchor" in url:
            return narratives_api.RawPage(
                url=url,
                final_url=url,
                status_code=200,
                content_type="text/html",
                html=_html("Local anchor", "The hidden energy tax phrase appears in a local post.", "2026-06-01T08:00:00Z"),
                fetched_at=datetime.now(timezone.utc),
            )
        if "independent-report" in url:
            return narratives_api.RawPage(
                url=url,
                final_url=url,
                status_code=200,
                content_type="text/html",
                html=_html("Independent reporting", "Independent reporting repeats the hidden energy tax framing.", "2026-06-01T13:00:00Z"),
                fetched_at=datetime.now(timezone.utc),
            )
        if "counter-frame" in url:
            return narratives_api.RawPage(
                url=url,
                final_url=url,
                status_code=200,
                content_type="text/html",
                html=_html("Counter frame", "However, supporters say the policy provides long-term savings.", "2026-06-01T15:00:00Z"),
                fetched_at=datetime.now(timezone.utc),
            )
        return narratives_api.RawPage(
            url=url,
            final_url=url,
            status_code=200,
            content_type="text/html",
            html=_html("Official remarks", "Public comments have described this as a hidden energy tax.", "2026-06-01T17:00:00Z"),
            fetched_at=datetime.now(timezone.utc),
        )


def test_investigation_runner_persists_research_loop_artifacts(tmp_path, monkeypatch):
    repo = InvestigationRepository(str(tmp_path / "investigations.sqlite3"))
    plan = _plan()
    repo.save_plan("inv_loop", plan.query_text, plan)

    settings = get_settings()
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-key")

    retriever = RetrieverAgent(
        repository=repo,
        search_provider=_StubProvider(),
        page_fetcher=_StubFetcher(),
        normalizer=DocumentNormalizer(),
    )
    runner = InvestigationRunner(repository=repo, retriever=retriever)

    workspace = runner.run("inv_loop", plan, force_refresh=True)

    assert workspace.research_loop is not None
    assert workspace.research_loop.final_decision in {"completed", "completed_with_softening", "insufficient_evidence"}
    assert workspace.gap_analysis is not None
    assert workspace.provenance_trace is not None
    assert workspace.skeptic_review is not None
    assert workspace.claim_ledger is not None
    assert workspace.report is not None
    assert workspace.report.confidence_dimensions is not None


def test_runner_returns_configuration_missing_without_live_model(tmp_path, monkeypatch):
    repo = InvestigationRepository(str(tmp_path / "investigations.sqlite3"))
    plan = _plan()
    repo.save_plan("inv_missing_cfg", plan.query_text, plan)

    settings = get_settings()
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    monkeypatch.setattr(settings, "GROQ_API_KEY", "")

    retriever = RetrieverAgent(
        repository=repo,
        search_provider=_StubProvider(),
        page_fetcher=_StubFetcher(),
        normalizer=DocumentNormalizer(),
    )
    runner = InvestigationRunner(repository=repo, retriever=retriever)

    workspace = runner.run("inv_missing_cfg", plan, force_refresh=True)
    assert workspace.research_loop is not None
    assert workspace.research_loop.final_decision == "configuration_missing"


def test_run_endpoint_returns_workspace_with_research_loop(tmp_path, monkeypatch):
    repo = InvestigationRepository(str(tmp_path / "investigations.sqlite3"))
    monkeypatch.setattr(narratives_api, "_investigation_repo", repo)
    settings = get_settings()
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-key")

    def _stub_retriever_agent():
        return RetrieverAgent(
            repository=repo,
            search_provider=_StubProvider(),
            page_fetcher=_StubFetcher(),
            normalizer=DocumentNormalizer(),
        )

    monkeypatch.setattr(narratives_api, "_build_retriever_agent", _stub_retriever_agent)
    monkeypatch.setattr(
        narratives_api,
        "_build_investigation_runner",
        lambda: InvestigationRunner(repository=repo, retriever=_stub_retriever_agent()),
    )

    plan_response = client.post("/api/investigate", json={"query_text": _plan().query_text})
    assert plan_response.status_code == 200
    investigation_id = plan_response.json()["investigation_id"]

    response = client.post(f"/api/investigations/{investigation_id}/run", json={})
    assert response.status_code == 200
    payload = response.json()
    deadline = time.monotonic() + 5
    while payload["research_loop"] is None and time.monotonic() < deadline:
        time.sleep(0.05)
        payload = client.get(f"/api/investigations/{investigation_id}").json()

    assert payload["research_loop"] is not None
    assert payload["gap_analysis"] is not None
    assert payload["provenance_trace"] is not None
    assert payload["report"] is not None
