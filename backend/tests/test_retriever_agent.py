import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from agents.retriever_agent import RetrieverAgent
from models.document import Document
from models.investigation import (
    FetchFailure,
    InvestigationPlan,
    InvestigationPlanTimeWindow,
    RawPage,
    SearchResult,
)
from services.document_normalizer import DocumentNormalizer
from services.investigation_repository import InvestigationRepository
from services.page_fetcher import HttpPageFetcher
from services.search_provider import CachedSearchProvider, UnconfiguredSearchProvider


def _plan() -> InvestigationPlan:
    return InvestigationPlan(
        query_text="Where did the 'hidden energy tax' narrative come from?",
        topic="hidden energy tax",
        canonical_phrase="hidden energy tax",
        intent="origin",
        entities=["hidden", "energy", "tax"],
        search_queries=["\"hidden energy tax\"", "hidden energy tax narrative"],
        semantic_queries=["Investigate the narrative around hidden energy tax"],
        target_source_types=["blog", "local_news", "national_news"],
        requested_outputs=["timeline", "counter_narratives", "source_diversity"],
        time_window=InvestigationPlanTimeWindow(label="all_time"),
        retrieval_mode="broad",
        risk_notes=[],
        uncertainty_requirements=[],
    )


def test_unconfigured_search_provider_explains_model_search_is_pending():
    provider = UnconfiguredSearchProvider()

    with pytest.raises(RuntimeError, match="model-native search provider"):
        provider.search(
            "hidden energy tax",
            InvestigationPlanTimeWindow(label="all_time"),
            ["blog"],
            5,
        )


def test_cached_search_provider_uses_cache_before_inner_provider():
    calls = {"count": 0}
    cached_result = SearchResult(
        query="hidden energy tax",
        title="Cached story",
        url="https://example.com/cached",
        snippet="Cached result",
        rank=1,
        provider="fake",
        provider_score=0.9,
    )

    class _Cache:
        def get_search(self, provider, query):
            assert provider == "fake"
            assert query == "hidden energy tax"
            return [cached_result.model_dump(mode="json")]

        def set_search(self, provider, query, results):
            raise AssertionError("cache hit should not write")

    class _Provider:
        name = "fake"

        def search(self, query, time_window, source_types, limit):
            calls["count"] += 1
            return []

    provider = CachedSearchProvider(_Provider(), cache=_Cache())
    results = provider.search("hidden energy tax", InvestigationPlanTimeWindow(label="all_time"), ["blog"], 5)

    assert calls["count"] == 0
    assert results == [cached_result]


def test_page_fetcher_rejects_non_html(monkeypatch):
    class _Response:
        status_code = 200
        url = "https://example.com/file.pdf"
        headers = {"content-type": "application/pdf"}
        text = "ignored"

        def raise_for_status(self):
            return None

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url):
            return _Response()

    monkeypatch.setattr("httpx.Client", lambda **kwargs: _Client())
    fetcher = HttpPageFetcher()
    result = fetcher.fetch("https://example.com/file.pdf")
    assert isinstance(result, FetchFailure)
    assert result.error_type == "unsupported_content_type"


def test_document_normalizer_builds_document():
    normalizer = DocumentNormalizer()
    page = RawPage(
        url="https://example.com/story",
        final_url="https://example.com/story",
        status_code=200,
        content_type="text/html",
        html="""
            <html lang="en">
            <head>
              <title>Hidden Energy Tax Story</title>
              <meta name="author" content="Jane Doe">
              <meta property="article:published_time" content="2026-06-03T08:00:00Z">
            </head>
            <body><article><p>The hidden energy tax debate spread quickly.</p></article></body>
            </html>
        """,
        fetched_at=datetime.now(timezone.utc),
    )
    result = SearchResult(
        query="hidden energy tax",
        title="Hidden Energy Tax Story",
        url="https://example.com/story",
        snippet="The hidden energy tax debate spread quickly.",
        rank=1,
        provider="stub",
    )
    doc = normalizer.normalize(page, _plan(), result)
    assert isinstance(doc, Document)
    assert doc.title == "Hidden Energy Tax Story"
    assert doc.author == "Jane Doe"
    assert doc.published_at is not None
    assert "hidden energy tax" in " ".join(doc.phrases)


def test_document_normalizer_normalizes_naive_published_at_to_utc():
    normalizer = DocumentNormalizer()
    page = RawPage(
        url="https://example.com/story",
        final_url="https://example.com/story",
        status_code=200,
        content_type="text/html",
        html="""
            <html lang="en">
            <head>
              <title>Hidden Energy Tax Story</title>
              <meta property="article:published_time" content="2026-06-03T08:00:00">
            </head>
            <body><article><p>The hidden energy tax debate spread quickly.</p></article></body>
            </html>
        """,
        fetched_at=datetime.now(timezone.utc),
    )
    result = SearchResult(
        query="hidden energy tax",
        title="Hidden Energy Tax Story",
        url="https://example.com/story",
        snippet="The hidden energy tax debate spread quickly.",
        rank=1,
        provider="stub",
    )
    doc = normalizer.normalize(page, _plan(), result)
    assert doc.published_at is not None
    assert doc.published_at.tzinfo == timezone.utc


def test_repository_persists_plan_and_result(tmp_path):
    repo = InvestigationRepository(str(tmp_path / "investigations.sqlite3"))
    plan = _plan()
    repo.save_plan("inv_1", plan.query_text, plan)
    loaded = repo.get_plan("inv_1")
    assert loaded is not None
    assert loaded.query_text == plan.query_text


def test_retriever_agent_runs_iterative_loop_and_persists(tmp_path):
    repo = InvestigationRepository(str(tmp_path / "investigations.sqlite3"))
    plan = _plan()
    repo.save_plan("inv_1", plan.query_text, plan)

    class _StubProvider:
        name = "stub"

        def search(self, query, time_window, source_types, limit):
            return [
                SearchResult(
                    query=query,
                    title="Hidden Energy Tax Story",
                    url="https://example.com/story",
                    snippet="The hidden energy tax debate spread quickly.",
                    rank=1,
                    provider="stub",
                ),
                SearchResult(
                    query=query,
                    title="Counter framing on the hidden energy tax",
                    url="https://example.com/counter",
                    snippet="Critics however say the policy is long-term infrastructure investment.",
                    rank=2,
                    provider="stub",
                ),
            ]

    class _StubFetcher:
        def fetch(self, url):
            if url.endswith("/story"):
                return RawPage(
                    url=url,
                    final_url=url,
                    status_code=200,
                    content_type="text/html",
                    html="<html><title>Hidden Energy Tax Story</title><body>The hidden energy tax debate spread quickly.</body></html>",
                    fetched_at=datetime.now(timezone.utc),
                )
            return RawPage(
                url=url,
                final_url=url,
                status_code=200,
                content_type="text/html",
                html="<html><title>Counter framing</title><body>However, advocates argue the plan is infrastructure investment.</body></html>",
                fetched_at=datetime.now(timezone.utc),
            )

    agent = RetrieverAgent(
        repository=repo,
        search_provider=_StubProvider(),
        page_fetcher=_StubFetcher(),
        normalizer=DocumentNormalizer(),
    )
    result = agent.retrieve("inv_1", plan, max_rounds=2)
    assert len(result.retrieved_document_ids) >= 2
    assert result.coverage_summary.total_documents >= 2
    assert repo.get_retrieval_result("inv_1") is not None
    persisted_docs = repo.get_retrieved_documents("inv_1")
    assert len(persisted_docs) >= 2
    assert all(doc.source_profile is not None for doc in persisted_docs)


def test_retriever_agent_uses_planner_requested_outputs_in_round_queries(tmp_path):
    repo = InvestigationRepository(str(tmp_path / "investigations.sqlite3"))
    plan = _plan().model_copy(
        update={
            "requested_outputs": ["timeline", "counter_narratives", "source_diversity", "receipts"],
            "target_source_types": ["blog", "local_news", "official_statement", "community_post"],
        }
    )
    repo.save_plan("inv_2", plan.query_text, plan)

    captured_queries: list[list[str]] = []

    class _StubProvider:
        name = "stub"

        def search(self, query, time_window, source_types, limit):
            if not captured_queries or captured_queries[-1] is None:
                pass
            return [
                SearchResult(
                    query=query,
                    title="Hidden Energy Tax Story",
                    url=f"https://example.com/{abs(hash(query))}",
                    snippet="However, critics say the plan is a hidden energy tax on working families.",
                    rank=1,
                    provider="stub",
                )
            ]

    class _StubFetcher:
        def fetch(self, url):
            return RawPage(
                url=url,
                final_url=url,
                status_code=200,
                content_type="text/html",
                html=(
                    "<html><title>Hidden Energy Tax Story</title>"
                    "<body>However, critics say the plan is a hidden energy tax on working families.</body></html>"
                ),
                fetched_at=datetime.now(timezone.utc),
            )

    agent = RetrieverAgent(
        repository=repo,
        search_provider=_StubProvider(),
        page_fetcher=_StubFetcher(),
        normalizer=DocumentNormalizer(),
    )

    round_one = agent._build_round_queries(plan, 1, [])
    round_two = agent._build_round_queries(plan, 2, [])
    assert "\"hidden energy tax\"" in round_one
    assert any("counter narrative" in query for query in round_two)
    assert any("official statement" in query for query in round_two)
    assert any("community response" in query for query in round_two)


def test_retriever_agent_scores_requested_source_types_and_receipt_ready_docs(tmp_path):
    repo = InvestigationRepository(str(tmp_path / "investigations.sqlite3"))
    class _StubProvider:
        name = "stub"

        def search(self, query, time_window, source_types, limit):
            return []

    agent = RetrieverAgent(repository=repo, search_provider=_StubProvider())
    plan = _plan().model_copy(
        update={
            "requested_outputs": ["timeline", "source_diversity", "receipts"],
            "target_source_types": ["official_statement", "blog"],
        }
    )
    docs = [
        Document(
            id="doc_official",
            source_id="src_official",
            source_name="agency.gov",
            source_type="commentary",
            url="https://agency.gov/statement",
            title="Official hidden energy tax response",
            published_at=datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc),
            collected_at=datetime(2026, 6, 19, tzinfo=timezone.utc),
            text="However, officials responded to the hidden energy tax claim.",
            snippet="Officials responded to the hidden energy tax claim.",
            language="en",
            content_type="article",
            geographic_scope="national",
            entities=["agency", "officials"],
            phrases=["hidden energy tax"],
            metadata={},
        ),
        Document(
            id="doc_plain",
            source_id="src_plain",
            source_name="example.com",
            source_type="national_news",
            url="https://example.com/story",
            title="Broader coverage",
            published_at=datetime(2026, 6, 3, 10, 0, tzinfo=timezone.utc),
            collected_at=datetime(2026, 6, 19, tzinfo=timezone.utc),
            text="Coverage expanded with less specific language.",
            snippet=None,
            language="en",
            content_type="article",
            geographic_scope="national",
            entities=["coverage"],
            phrases=[],
            metadata={},
        ),
    ]

    scored = agent._score_documents(docs, plan)
    top_doc, _ = scored[0]
    assert top_doc.id == "doc_official"
    assert "receipt_snippet" in (top_doc.metadata or {}).get("retrieval_reason_tags", [])
