from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import httpx
import pytest

from config import get_settings
from models.investigation import InvestigationPlan, InvestigationPlanTimeWindow, RawPage, SearchResult
from models.research import ResearchActionDecision, ResearchBudgetLimits
from services.autonomous_research import ResearchSupervisor
from services.federal_register import FederalRegisterClient, LEGAL_STATUS_LIMITATION
from services.research_repository import ResearchRepository
from services.research_tools import ResearchToolRegistry
from services.search_provider import (
    SearchProviderUnavailable,
    SearxngSearchProvider,
    UnconfiguredSearchProvider,
)


FIXTURES = Path(__file__).parent / "fixtures" / "b2"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _plan(*, official: bool = True) -> InvestigationPlan:
    return InvestigationPlan(
        query_text="What did agencies publish about clean energy reporting?",
        topic="clean energy reporting",
        intent="general investigation",
        search_queries=["clean energy reporting"],
        semantic_queries=["clean energy reporting rules"],
        target_source_types=["national_news", "government_record"],
        retrieval_lanes=["discovery", "corroboration", *( ["official"] if official else [] )],
        requested_outputs=["timeline", "receipts"],
        time_window=InvestigationPlanTimeWindow(label="recent"),
    )


def _decision(action_type: str, **updates) -> ResearchActionDecision:
    values = {
        "action_type": action_type,
        "retrieval_lane": "official" if action_type == "federal_register_search" else "discovery",
        "query": "clean energy reporting",
        "action_summary": "Collecting evidence for the highest-priority source gap.",
        "expected_evidence": "Dated, attributable, and inspectable source material.",
    }
    values.update(updates)
    return ResearchActionDecision(**values)


def test_searxng_fixture_deduplicates_and_preserves_discovery_provenance(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "SEARCH_PROVIDER_MAX_RETRIES", 1)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_fixture("searxng_results.json"))

    provider = SearxngSearchProvider(
        "https://search.example",
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    )
    results = provider.search(
        "clean energy reporting",
        InvestigationPlanTimeWindow(label="recent"),
        ["government_record"],
        8,
    )

    assert [result.rank for result in results] == [1, 6]
    assert results[0].url == "https://example.gov/rule"
    assert results[0].metadata["source_native_id"] == "search-native-1"
    assert results[0].metadata["evidence_status"] == "discovery_only"
    assert results[0].metadata["source_policy"]["citable"] is False
    assert provider.last_diagnostics["duplicate_results"] == 1
    assert provider.last_diagnostics["malformed_results"] == 3
    assert provider.last_diagnostics["outcome"] == "partial"


def test_searxng_honors_retry_after_and_reports_recovery(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "SEARCH_PROVIDER_MAX_RETRIES", 1)
    calls = 0
    delays: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "1"}, json={"error": "slow down"})
        return httpx.Response(200, json=_fixture("searxng_results.json"))

    provider = SearxngSearchProvider(
        "https://search.example",
        transport=httpx.MockTransport(handler),
        sleep=delays.append,
    )
    results = provider.search("clean energy", InvestigationPlanTimeWindow(label="recent"), [], 1)

    assert len(results) == 1
    assert calls == 2
    assert delays == [1.0]
    assert provider.last_diagnostics["rate_limited"] is True
    assert provider.last_diagnostics["retry_count"] == 1


def test_searxng_malformed_payload_has_explicit_internal_fallback() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": {"not": "a list"}})

    provider = SearxngSearchProvider(
        "https://search.example",
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    )
    with pytest.raises(SearchProviderUnavailable) as exc_info:
        provider.search("clean energy", InvestigationPlanTimeWindow(), [], 3)

    assert exc_info.value.diagnostics["failure_category"] == "malformed_response"
    assert exc_info.value.diagnostics["fallback"] == "internal_corpus"


def test_registry_exposes_visible_search_fallback() -> None:
    registry = ResearchToolRegistry(search_provider=UnconfiguredSearchProvider())
    outcome = registry.execute(_decision("web_search"), _plan(), [])

    assert outcome.documents == []
    assert outcome.candidates == []
    assert outcome.retryable is False
    assert outcome.warning and outcome.warning.startswith("live_web_search_unavailable:")
    assert outcome.receipts[0][1]["fallback"] == "internal_corpus"


def test_federal_register_fixture_normalizes_primary_records_and_deduplicates(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "FEDERAL_REGISTER_PAGE_SIZE", 2)
    monkeypatch.setattr(settings, "FEDERAL_REGISTER_MAX_PAGES", 2)
    monkeypatch.setattr(settings, "FEDERAL_REGISTER_MIN_INTERVAL_SECONDS", 0.0)

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page")
        fixture = "federal_register_page_2.json" if page == "2" else "federal_register_page_1.json"
        return httpx.Response(200, json=_fixture(fixture))

    client = FederalRegisterClient(
        "https://www.federalregister.gov/api/v1",
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    )
    batch = client.search("clean energy", InvestigationPlanTimeWindow(label="recent"), limit=4)

    assert len(batch.documents) == 3
    assert batch.diagnostics["pages_completed"] == 2
    assert batch.diagnostics["duplicate_results"] == 1
    assert batch.diagnostics["malformed_results"] == 1
    first = batch.documents[0]
    assert first.source_type == "government_record"
    assert first.source_profile and first.source_profile.institution_kind == "official"
    assert first.metadata["source_native_id"] == "2026-19001"
    assert first.metadata["acquisition_receipt_valid"] is True
    assert first.metadata["source_policy"]["basis"] == "first_party_public_record_api"
    assert first.published_at == datetime(2026, 9, 10, tzinfo=timezone.utc)
    assert LEGAL_STATUS_LIMITATION in first.metadata["limitations"]
    undated = batch.documents[-1]
    assert undated.published_at is None
    assert any("usable publication date" in item for item in undated.metadata["limitations"])


def test_registry_returns_federal_register_documents_and_receipts(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "FEDERAL_REGISTER_PAGE_SIZE", 20)
    monkeypatch.setattr(settings, "FEDERAL_REGISTER_MAX_PAGES", 1)
    monkeypatch.setattr(settings, "FEDERAL_REGISTER_MIN_INTERVAL_SECONDS", 0.0)

    client = FederalRegisterClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json=_fixture("federal_register_page_1.json"))
        ),
        sleep=lambda _seconds: None,
    )
    registry = ResearchToolRegistry(
        search_provider=UnconfiguredSearchProvider(),
        federal_register=client,
    )
    outcome = registry.execute(_decision("federal_register_search"), _plan(), [])

    assert len(outcome.documents) == 2
    receipt_kinds = [kind for kind, _payload in outcome.receipts]
    assert receipt_kinds == ["primary_source", "primary_source", "provider_status"]
    receipt = outcome.receipts[0][1]
    assert receipt["provider"] == "federal_register"
    assert receipt["source_native_id"] == "2026-19001"
    assert receipt["rank"] == 1
    assert receipt["canonical_url"] == outcome.documents[0].url
    assert receipt["fetch_outcome"]["status"] == "success"
    assert receipt["limitations"]


def test_supervisor_fallback_selects_primary_source_for_official_gap() -> None:
    supervisor = ResearchSupervisor(audit=object())
    candidate = SearchResult(
        query="clean energy",
        title="A discovery lead",
        url="https://news.example/story",
        rank=1,
        provider="searxng",
    )
    decision = supervisor._fallback(
        {"action_count": 1, "warnings": [], "attempted_candidate_ids": []},
        _plan(official=True),
        [candidate],
        [],
    )
    assert decision.action_type == "federal_register_search"
    assert decision.retrieval_lane == "official"


def test_supervisor_uses_internal_corpus_when_live_search_is_unavailable() -> None:
    supervisor = ResearchSupervisor(audit=object())
    decision = supervisor._fallback(
        {
            "action_count": 1,
            "warnings": ["live_web_search_unavailable: current broad-web coverage is limited."],
            "attempted_candidate_ids": [],
        },
        _plan(official=False),
        [],
        [],
    )
    assert decision.action_type == "internal_search"
    assert "web_search" not in supervisor._allowed_actions({"warnings": ["live_web_search_unavailable:"]})


def test_canonical_fetch_preserves_discovery_and_policy_provenance() -> None:
    candidate = SearchResult(
        query="clean energy reporting",
        title="Agency rule",
        url="https://agency.gov/rule",
        snippet="A proposed reporting rule.",
        rank=3,
        provider="searxng",
        metadata={"source_native_id": "native-3", "published_date": "2026-09-01T00:00:00Z"},
    )

    class Fetcher:
        def fetch(self, _url: str) -> RawPage:
            return RawPage(
                url=candidate.url,
                final_url="https://agency.gov/rule-final",
                status_code=200,
                content_type="text/html",
                html="<html lang='en'><head><title>Final agency rule</title></head><body>Rule text.</body></html>",
                fetched_at=datetime(2026, 9, 14, tzinfo=timezone.utc),
            )

    registry = ResearchToolRegistry(
        search_provider=UnconfiguredSearchProvider(),
        fetcher=Fetcher(),  # type: ignore[arg-type]
    )
    from services.research_tools import candidate_id

    outcome = registry.execute(
        _decision(
            "canonical_fetch",
            retrieval_lane="corroboration",
            query=None,
            candidate_id=candidate_id(candidate),
        ),
        _plan(),
        [candidate],
    )

    document = outcome.documents[0]
    assert document.url == "https://agency.gov/rule-final"
    assert document.source_type == "government_record"
    assert document.metadata["source_native_id"] == "native-3"
    assert document.metadata["search_rank"] == 3
    assert document.metadata["source_policy"]["decision"] == "allow"
    assert document.metadata["fetch_outcome"]["status"] == "success"
    assert document.metadata["acquisition_receipt_valid"] is True
    receipt = outcome.receipts[0][1]
    assert receipt["query"] == candidate.query
    assert receipt["rank"] == 3
    assert receipt["canonical_url"] == document.url


def test_repository_persists_primary_source_document_and_receipt(tmp_path, monkeypatch) -> None:
    synced: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "services.postgres_corpus.sync_document",
        lambda _target, document, *, source_kind: synced.append((document.id, source_kind)),
    )
    repository = ResearchRepository(str(tmp_path / "b2.sqlite3"))
    run = repository.create_run("inv_b2", ResearchBudgetLimits())
    action = repository.start_action(run.run_id, _decision("federal_register_search"), provider="federal_register")
    document = FederalRegisterClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json=_fixture("federal_register_page_1.json"))
        ),
        sleep=lambda _seconds: None,
    ).search("clean energy", InvestigationPlanTimeWindow(), limit=1).documents[0]

    repository.save_document(run.run_id, document)
    receipt_id = repository.save_receipt(
        run.run_id,
        action.action_id,
        "primary_source",
        {"document_id": document.id, "source_native_id": document.metadata["source_native_id"]},
    )
    repository.finish_action(
        action.action_id,
        status="completed",
        result_count=1,
        document_ids=[document.id],
        receipt_ids=[receipt_id],
    )

    assert repository.get_documents(run.run_id)[0].id == document.id
    assert repository.list_action_receipts(action.action_id)[0][1]["source_native_id"] == "2026-19001"
    assert synced == [(document.id, "research")]
