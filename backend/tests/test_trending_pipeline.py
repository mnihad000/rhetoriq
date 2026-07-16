import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["DEMO_MODE"] = "true"

from fastapi.testclient import TestClient

from api import trending as trending_api
from agents.discovery_agent import DiscoveryAgent
from agents.retriever_agent import RetrievalPreview
from main import app
from models.document import Document
from models.investigation import CoverageSummary, RetrievalRound, SearchResult
from models.trending import (
    DiscoveryQuery,
    DiscoveryRunStats,
    PublishedTrendingSnapshot,
    TopicTimelinePoint,
    TrendingFeedResponse,
    TrendingTopic,
)
from services.investigation_repository import InvestigationRepository
from services.search_provider import MultiSearchProvider
from services.trending_ranker import TrendingRanker
from services.trending_repository import TrendingRepository
from services.trending_service import TrendingService

client = TestClient(app)


def _document(doc_id: str, title: str, url: str, published_at: datetime, source_type: str) -> Document:
    return Document(
        id=doc_id,
        source_id=f"domain:{url.split('/')[2]}",
        source_name=url.split("/")[2],
        source_type=source_type,
        url=url,
        title=title,
        author=None,
        published_at=published_at,
        collected_at=published_at,
        text=title,
        snippet=title,
        language="en",
        content_type="article",
        geographic_scope="national",
        entities=[],
        phrases=["energy tax"],
        metadata={},
    )


def _topic(topic_id: str = "topic_1", *, generated_at: datetime | None = None) -> TrendingTopic:
    generated_at = generated_at or datetime.now(timezone.utc)
    return TrendingTopic(
        id=topic_id,
        title="Energy Tax Debate",
        canonical_phrase="energy tax debate",
        summary="summary",
        related_phrases=["energy tax"],
        status="emerging",
        confidence_label="High",
        confidence_score=0.82,
        source_count=5,
        publisher_count=4,
        first_observed_at=generated_at - timedelta(hours=5),
        latest_observed_at=generated_at - timedelta(hours=1),
        source_diversity_snapshot={"national_news": 3, "commentary": 2},
        timeline=[TopicTimelinePoint(timestamp=generated_at, count=5)],
        velocity_score=2.4,
        persistence_runs=2,
        provider_mix={"test_search": 5},
        supporting_document_ids=["doc1"],
    )


def _snapshot(
    topic_id: str = "topic_1",
    *,
    generated_at: datetime | None = None,
    fresh_for_hours: int = 6,
) -> PublishedTrendingSnapshot:
    generated_at = generated_at or datetime.now(timezone.utc)
    return PublishedTrendingSnapshot(
        snapshot_id=f"snap_{topic_id}",
        state="ready",
        generated_at=generated_at,
        fresh_until=generated_at + timedelta(hours=fresh_for_hours),
        last_completed_run_at=generated_at,
        last_reseed_at=generated_at,
        topics=[_topic(topic_id, generated_at=generated_at)],
    )


class _RuntimeStub:
    redis_available = True

    def __init__(
        self,
        *,
        latest_snapshot: PublishedTrendingSnapshot | None = None,
        acquire_returns: bool = False,
        invalid_snapshot_once: bool = False,
    ) -> None:
        self.latest_snapshot = latest_snapshot
        self.acquire_returns = acquire_returns
        self.invalid_snapshot_once = invalid_snapshot_once
        self.invalid_snapshot_cleared = False
        self.mapping: dict[str, str] = {}
        self.last_error: str | None = None
        self.snapshot_write_count = 0

    def acquire_refresh_lock(self):
        return self.acquire_returns

    def release_refresh_lock(self):
        return None

    def refresh_lock_active(self):
        return False

    def set_latest_snapshot(self, snapshot):
        self.latest_snapshot = snapshot
        self.snapshot_write_count += 1

    def get_latest_snapshot(self):
        if self.invalid_snapshot_once:
            self.invalid_snapshot_once = False
            self.invalid_snapshot_cleared = True
            self.latest_snapshot = None
            return None
        return self.latest_snapshot

    def set_topic_investigation(self, topic_id, investigation_id, ttl_seconds):
        self.mapping[topic_id] = investigation_id

    def get_topic_investigation(self, topic_id):
        return self.mapping.get(topic_id)

    def set_last_error(self, message):
        self.last_error = message

    def get_last_error(self):
        return self.last_error


def test_multi_provider_orchestrates_discovery_and_enrichment():
    class _DiscoveryProvider:
        name = "discovery_stub"

        def __init__(self):
            self.calls = []

        def search(self, query, time_window, source_types, limit):
            self.calls.append(("discovery", query, limit))
            return []

    class _EnrichmentProvider:
        name = "enrichment_stub"

        def __init__(self):
            self.calls = []

        def search(self, query, time_window, source_types, limit):
            self.calls.append(("enrichment", query, limit))
            return []

    discovery = _DiscoveryProvider()
    enrichment = _EnrichmentProvider()
    provider = MultiSearchProvider(discovery_provider=discovery, enrichment_provider=enrichment)

    provider.search_discovery("energy tax", type("TW", (), {"label": "recent"})(), [], 4)
    provider.search_enrichment("energy tax counter narrative", type("TW", (), {"label": "recent"})(), [], 3)

    assert discovery.calls == [("discovery", "energy tax", 4)]
    assert enrichment.calls == [("enrichment", "energy tax counter narrative", 3)]
    assert provider.provider_mix == {
        "discovery": "discovery_stub",
        "enrichment": "enrichment_stub",
    }


def test_discovery_agent_builds_broad_queries_and_reuses_prior_topics():
    agent = DiscoveryAgent.__new__(DiscoveryAgent)

    queries = agent.build_queries(prior_topics=["SpaceX IPO"], is_reseed=False)
    query_texts = [query.query for query in queries]

    assert "breaking news today" in query_texts
    assert "latest politics" in query_texts
    assert "\"SpaceX IPO\"" in query_texts
    assert "SpaceX IPO official statement" in query_texts
    assert "housing" not in query_texts


def test_discovery_agent_uses_specific_title_window_for_generic_seed():
    agent = DiscoveryAgent.__new__(DiscoveryAgent)
    result = SearchResult(
        query="housing",
        title="Housing affordability crisis grows in major cities",
        url="https://example.com/housing",
        snippet="Housing affordability crisis grows in major cities.",
        rank=1,
        provider="test_search",
    )

    phrase = agent._canonical_phrase("housing", result)

    assert phrase == "housing affordability crisis"


def test_trending_repository_and_ranker_build_publishable_topic(tmp_path):
    repo = TrendingRepository(str(tmp_path / "trending.sqlite3"))
    now = datetime(2026, 6, 20, 18, 0, tzinfo=timezone.utc)
    queries = [DiscoveryQuery(query="energy tax", provider_role="discovery", topic_seed="energy")]

    repo.create_run("disc_1", is_reseed=True, queries=queries)
    repo.create_run("disc_2", is_reseed=False, queries=queries)

    docs = [
        _document("doc1", "Energy tax debate grows", "https://a.com/story-1", now - timedelta(hours=4), "national_news"),
        _document("doc2", "Energy tax debate expands", "https://b.com/story-2", now - timedelta(hours=3), "commentary"),
        _document("doc3", "Local energy tax debate spreads", "https://c.com/story-3", now - timedelta(hours=2), "local_news"),
        _document("doc4", "Energy tax debate prompts response", "https://d.com/story-4", now - timedelta(hours=1), "national_news"),
    ]

    for doc in docs[:2]:
        repo.save_discovery_document("disc_1", doc, canonical_url=doc.url, domain=doc.source_name, provider="test_search", search_query="energy tax")
    for doc in docs:
        repo.save_discovery_document("disc_2", doc, canonical_url=doc.url, domain=doc.source_name, provider="test_search", search_query="energy tax narrative")

    ranker = TrendingRanker()
    topics = ranker.rank(repo.list_discovery_documents(), now=now, max_topics=5)

    assert topics
    assert topics[0].canonical_phrase
    assert topics[0].source_count >= 4
    assert topics[0].publisher_count >= 3
    assert topics[0].persistence_runs >= 2


def test_trending_ranker_avoids_boundary_duplicate_phrase_artifacts(tmp_path):
    repo = TrendingRepository(str(tmp_path / "trending.sqlite3"))
    now = datetime(2026, 6, 20, 18, 0, tzinfo=timezone.utc)
    queries = [DiscoveryQuery(query="housing", provider_role="discovery", topic_seed="housing")]
    repo.create_run("disc_1", is_reseed=True, queries=queries)
    repo.create_run("disc_2", is_reseed=False, queries=queries)

    docs = [
        _document("doc1", "Housing affordability crisis deepens", "https://a.com/story-1", now - timedelta(hours=4), "national_news"),
        _document("doc2", "Housing affordability crisis spreads", "https://b.com/story-2", now - timedelta(hours=3), "commentary"),
        _document("doc3", "Housing affordability crisis hits renters", "https://c.com/story-3", now - timedelta(hours=2), "local_news"),
        _document("doc4", "Housing affordability crisis prompts debate", "https://d.com/story-4", now - timedelta(hours=1), "national_news"),
    ]

    for doc in docs[:2]:
        repo.save_discovery_document("disc_1", doc, canonical_url=doc.url, domain=doc.source_name, provider="test_search", search_query="housing")
    for doc in docs:
        repo.save_discovery_document("disc_2", doc, canonical_url=doc.url, domain=doc.source_name, provider="test_search", search_query="housing crisis")

    ranker = TrendingRanker()
    topics = ranker.rank(repo.list_discovery_documents(), now=now, max_topics=5)

    assert topics
    assert topics[0].canonical_phrase != "housing housing"
    assert topics[0].title != "Housing Housing"


def test_trending_service_reuses_topic_investigation_from_runtime(tmp_path):
    repo = TrendingRepository(str(tmp_path / "trending.sqlite3"))
    investigation_repo = InvestigationRepository(str(tmp_path / "investigations.sqlite3"))

    runtime = _RuntimeStub()
    service = TrendingService(repository=repo, runtime_store=runtime)
    snapshot = _snapshot("topic_1")
    repo.save_snapshot(snapshot)

    first = service.start_investigation_for_topic("topic_1", investigation_repo)
    second = service.start_investigation_for_topic("topic_1", investigation_repo)

    assert first.reused_existing is False
    assert second.reused_existing is True
    assert first.investigation_id == second.investigation_id


def test_trending_service_reads_snapshot_from_runtime_before_sqlite(tmp_path, monkeypatch):
    repo = TrendingRepository(str(tmp_path / "trending.sqlite3"))
    runtime = _RuntimeStub(latest_snapshot=_snapshot("topic_runtime"))
    service = TrendingService(repository=repo, runtime_store=runtime)

    monkeypatch.setattr(
        repo,
        "get_latest_snapshot",
        lambda: (_ for _ in ()).throw(AssertionError("sqlite should not be read")),
    )

    feed = service.get_feed(limit=1)

    assert feed.state == "ready"
    assert len(feed.topics) == 1
    assert feed.topics[0].id == "topic_runtime"


def test_trending_service_falls_back_to_sqlite_and_rehydrates_runtime(tmp_path):
    repo = TrendingRepository(str(tmp_path / "trending.sqlite3"))
    snapshot = _snapshot("topic_sqlite")
    repo.save_snapshot(snapshot)
    runtime = _RuntimeStub()
    service = TrendingService(repository=repo, runtime_store=runtime)

    feed = service.get_feed(limit=1)

    assert feed.state == "ready"
    assert feed.topics[0].id == "topic_sqlite"
    assert runtime.latest_snapshot is not None
    assert runtime.latest_snapshot.snapshot_id == snapshot.snapshot_id
    assert runtime.snapshot_write_count == 1


def test_trending_service_replaces_malformed_runtime_snapshot_from_sqlite(tmp_path):
    repo = TrendingRepository(str(tmp_path / "trending.sqlite3"))
    snapshot = _snapshot("topic_rehydrated")
    repo.save_snapshot(snapshot)
    runtime = _RuntimeStub(invalid_snapshot_once=True)
    service = TrendingService(repository=repo, runtime_store=runtime)

    feed = service.get_feed(limit=1)

    assert feed.state == "ready"
    assert feed.topics[0].id == "topic_rehydrated"
    assert runtime.invalid_snapshot_cleared is True
    assert runtime.latest_snapshot is not None
    assert runtime.latest_snapshot.snapshot_id == snapshot.snapshot_id


def test_trending_service_refresh_writes_snapshot_to_runtime_and_sqlite(tmp_path):
    repo = TrendingRepository(str(tmp_path / "trending.sqlite3"))
    runtime = _RuntimeStub()

    class _DiscoveryStub:
        def build_queries(self, prior_topics, is_reseed):
            return [DiscoveryQuery(query="energy tax", provider_role="discovery", topic_seed="energy")]

        def discover(self, prior_topics, is_reseed):
            return type(
                "DiscoveryBatch",
                (),
                {
                    "candidates": [],
                    "stats": DiscoveryRunStats(query_count=1),
                    "warnings": [],
                },
            )()

    service = TrendingService(
        repository=repo,
        runtime_store=runtime,
        discovery_agent=_DiscoveryStub(),
    )

    snapshot = service.refresh_now(is_reseed=True)
    persisted = repo.get_latest_snapshot()

    assert runtime.latest_snapshot is not None
    assert runtime.latest_snapshot.snapshot_id == snapshot.snapshot_id
    assert persisted is not None
    assert persisted.snapshot_id == snapshot.snapshot_id


def test_trending_service_uses_retriever_to_expand_open_ended_candidates(tmp_path):
    repo = TrendingRepository(str(tmp_path / "trending.sqlite3"))
    runtime = _RuntimeStub()
    now = datetime.now(timezone.utc)
    initial_docs = [
        _document("doc1", "SpaceX IPO chatter spreads across investors", "https://a.com/story-1", now - timedelta(hours=3), "national_news"),
        _document("doc2", "Analysts track SpaceX IPO chatter", "https://b.com/story-2", now - timedelta(hours=2), "commentary"),
    ]

    class _DiscoveryStub:
        def __init__(self, docs):
            self._docs = docs
            self._index = 0

        def build_queries(self, prior_topics, is_reseed):
            return [DiscoveryQuery(query="breaking news today", provider_role="discovery", topic_seed="breaking news today")]

        def discover(self, prior_topics, is_reseed):
            candidates = []
            for index, _doc in enumerate(self._docs, start=1):
                candidates.append(
                    type(
                        "DiscoveryCandidateStub",
                        (),
                        {
                            "search_result": SearchResult(
                                query="breaking news today",
                                title=f"candidate-{index}",
                                url=f"https://candidate-{index}.com/story",
                                snippet="candidate",
                                rank=index,
                                provider="test_search",
                                metadata={"source_name_hint": None},
                            )
                        },
                    )()
                )
            return type(
                "DiscoveryBatch",
                (),
                {
                    "candidates": candidates,
                    "stats": DiscoveryRunStats(query_count=1, result_count=len(self._docs), fetched_pages=len(self._docs), accepted_documents=len(self._docs)),
                    "warnings": [],
                },
            )()

        def normalize_candidate(self, candidate):
            document = self._docs[self._index]
            self._index += 1
            return document

    class _RetrieverStub:
        def __init__(self):
            self.calls = []

        def expand_candidate(self, plan, max_rounds=2):
            phrase = plan.canonical_phrase or plan.topic
            self.calls.append(phrase)
            docs = [
                _document("doc3", "Fund managers debate SpaceX IPO timing", "https://c.com/story-3", now - timedelta(hours=1), "national_news"),
                _document("doc4", "Retail traders react to SpaceX IPO chatter", "https://d.com/story-4", now - timedelta(minutes=50), "blog"),
                _document("doc5", "Bankers discuss SpaceX IPO prospects", "https://e.com/story-5", now - timedelta(minutes=40), "commentary"),
            ]
            for document in docs:
                document.metadata = {"provider": "retriever", "search_query": phrase}
                document.phrases = ["spacex ipo", "spacex ipo chatter"]
            return RetrievalPreview(
                documents=docs,
                coverage_summary=CoverageSummary(
                    total_documents=3,
                    unique_sources=3,
                    source_type_distribution={"national_news": 1, "blog": 1, "commentary": 1},
                    has_counter_narrative_candidates=False,
                    has_timeline_coverage=True,
                    exact_phrase_hits=3,
                    search_rounds_completed=2,
                ),
                warnings=[],
                search_rounds=[
                    RetrievalRound(
                        round_number=1,
                        queries=[phrase],
                        provider="stub",
                        discovered_results=3,
                        fetched_pages=3,
                        accepted_documents=3,
                        new_documents=3,
                        warnings=[],
                    )
                ],
            )

    for document in initial_docs:
        document.metadata = {"provider": "test_search", "search_query": "breaking news today"}
        document.phrases = ["spacex ipo", "spacex ipo chatter"]

    retriever = _RetrieverStub()
    service = TrendingService(
        repository=repo,
        runtime_store=runtime,
        discovery_agent=_DiscoveryStub(initial_docs),
        retriever_agent=retriever,
    )

    snapshot = service.refresh_now(is_reseed=True)

    assert retriever.calls
    assert any("spacex ipo" in call for call in retriever.calls)
    assert snapshot.topics
    assert any("spacex ipo" in topic.canonical_phrase for topic in snapshot.topics)


def test_trending_service_only_reseeds_publishable_prior_topics(tmp_path):
    repo = TrendingRepository(str(tmp_path / "trending.sqlite3"))
    runtime = _RuntimeStub()
    generated_at = datetime.now(timezone.utc)
    repo.save_snapshot(
        PublishedTrendingSnapshot(
            snapshot_id="snap_prior",
            state="ready",
            generated_at=generated_at,
            fresh_until=generated_at + timedelta(hours=6),
            last_completed_run_at=generated_at,
            last_reseed_at=generated_at,
            topics=[
                _topic("topic_world_cup", generated_at=generated_at).model_copy(
                    update={"title": "World Cup", "canonical_phrase": "world cup"}
                ),
                _topic("topic_public_opinion", generated_at=generated_at).model_copy(
                    update={"title": "Public Opinion", "canonical_phrase": "public opinion"}
                ),
                _topic("topic_around_world", generated_at=generated_at).model_copy(
                    update={"title": "Around World", "canonical_phrase": "around world"}
                ),
            ],
        )
    )

    class _DiscoveryStub:
        def __init__(self):
            self.received_prior_topics = []

        def build_queries(self, prior_topics, is_reseed):
            self.received_prior_topics = list(prior_topics)
            return [DiscoveryQuery(query="energy tax", provider_role="discovery", topic_seed="energy")]

        def discover(self, prior_topics, is_reseed):
            return type(
                "DiscoveryBatch",
                (),
                {
                    "candidates": [],
                    "stats": DiscoveryRunStats(query_count=1),
                    "warnings": [],
                },
            )()

    discovery = _DiscoveryStub()
    service = TrendingService(repository=repo, runtime_store=runtime, discovery_agent=discovery)

    service.refresh_now(is_reseed=True)

    assert discovery.received_prior_topics == ["world cup"]


def test_trending_service_ranks_current_run_documents_only(tmp_path):
    repo = TrendingRepository(str(tmp_path / "trending.sqlite3"))
    runtime = _RuntimeStub()
    now = datetime.now(timezone.utc)
    queries = [DiscoveryQuery(query="around world", provider_role="discovery", topic_seed="around world")]
    repo.create_run("disc_old", is_reseed=True, queries=queries)
    stale_docs = [
        _document("old1", "Public Opinion research roundup", "https://a.com/old-1", now - timedelta(hours=3), "national_news"),
        _document("old2", "Public Opinion trends report", "https://b.com/old-2", now - timedelta(hours=3), "commentary"),
        _document("old3", "Public Opinion survey dashboard", "https://c.com/old-3", now - timedelta(hours=2), "local_news"),
        _document("old4", "Public Opinion review", "https://d.com/old-4", now - timedelta(hours=1), "national_news"),
    ]
    for doc in stale_docs:
        repo.save_discovery_document("disc_old", doc, canonical_url=doc.url, domain=doc.source_name, provider="test_search", search_query="public opinion")

    fresh_docs = [
        _document("doc1", "SpaceX IPO chatter spreads", "https://e.com/story-1", now - timedelta(hours=4), "national_news"),
        _document("doc2", "Analysts track SpaceX IPO timing", "https://f.com/story-2", now - timedelta(hours=3), "commentary"),
        _document("doc3", "Retail traders react to SpaceX IPO", "https://g.com/story-3", now - timedelta(hours=2), "blog"),
        _document("doc4", "Bankers discuss SpaceX IPO prospects", "https://h.com/story-4", now - timedelta(hours=1), "national_news"),
    ]

    class _DiscoveryStub:
        def __init__(self, docs):
            self._docs = docs
            self._index = 0

        def build_queries(self, prior_topics, is_reseed):
            return [DiscoveryQuery(query="breaking news today", provider_role="discovery", topic_seed="breaking news today")]

        def discover(self, prior_topics, is_reseed):
            candidates = []
            for index, _doc in enumerate(self._docs, start=1):
                candidates.append(
                    type(
                        "DiscoveryCandidateStub",
                        (),
                        {
                            "search_result": SearchResult(
                                query="breaking news today",
                                title=f"candidate-{index}",
                                url=f"https://candidate-{index}.com/story",
                                snippet="candidate",
                                rank=index,
                                provider="test_search",
                                metadata={"source_name_hint": None},
                            )
                        },
                    )()
                )
            return type(
                "DiscoveryBatch",
                (),
                {
                    "candidates": candidates,
                    "stats": DiscoveryRunStats(query_count=1, result_count=len(self._docs), fetched_pages=len(self._docs), accepted_documents=len(self._docs)),
                    "warnings": [],
                },
            )()

        def normalize_candidate(self, candidate):
            document = self._docs[self._index]
            self._index += 1
            return document

    class _RetrieverStub:
        def expand_candidate(self, plan, max_rounds=2):
            return RetrievalPreview(
                documents=[],
                coverage_summary=CoverageSummary(),
                warnings=[],
                search_rounds=[],
            )

    for document in fresh_docs:
        document.metadata = {"provider": "test_search", "search_query": "breaking news today"}
        document.phrases = ["spacex ipo", "spacex ipo chatter"]

    snapshot = TrendingService(
        repository=repo,
        runtime_store=runtime,
        discovery_agent=_DiscoveryStub(fresh_docs),
        retriever_agent=_RetrieverStub(),
    ).refresh_now(is_reseed=True)

    assert snapshot.topics
    assert all("public opinion" not in topic.canonical_phrase for topic in snapshot.topics)
    assert any("spacex ipo" in topic.canonical_phrase for topic in snapshot.topics)


def test_trending_ranker_filters_generic_reference_artifacts(tmp_path):
    repo = TrendingRepository(str(tmp_path / "trending.sqlite3"))
    now = datetime(2026, 6, 21, 9, 0, tzinfo=timezone.utc)
    queries = [DiscoveryQuery(query="public reaction today", provider_role="discovery", topic_seed="public reaction today")]
    repo.create_run("disc_1", is_reseed=True, queries=queries)
    docs = [
        _document("doc1", "Opinion Today | Substack", "https://opiniontoday.substack.com", now - timedelta(hours=4), "blog"),
        _document("doc2", "WORLD Definition & Meaning | Dictionary.com", "https://www.dictionary.com/browse/world", now - timedelta(hours=3), "national_news"),
        _document("doc3", "A Counter - Apps on Google Play", "https://play.google.com/store/apps/details?id=com.pra.counter&hl=en_US", now - timedelta(hours=2), "national_news"),
        _document("doc4", "HousingWire - Industry News for Housing Professionals", "https://www.housingwire.com", now - timedelta(hours=1), "national_news"),
    ]
    docs[0].phrases = ["public opinion"]
    docs[1].phrases = ["around world"]
    docs[2].phrases = ["narrative around counter"]
    docs[3].phrases = ["housing market"]

    for doc in docs:
        repo.save_discovery_document("disc_1", doc, canonical_url=doc.url, domain=doc.source_name, provider="test_search", search_query="public reaction today")

    topics = TrendingRanker().rank(repo.list_discovery_documents(), now=now, max_topics=5)

    assert topics == []


def test_trending_service_starts_topic_investigation_from_runtime_snapshot(tmp_path):
    repo = TrendingRepository(str(tmp_path / "trending.sqlite3"))
    investigation_repo = InvestigationRepository(str(tmp_path / "investigations.sqlite3"))
    runtime = _RuntimeStub(latest_snapshot=_snapshot("topic_runtime_only"))
    service = TrendingService(repository=repo, runtime_store=runtime)

    response = service.start_investigation_for_topic("topic_runtime_only", investigation_repo)

    assert response.topic_id == "topic_runtime_only"
    assert response.reused_existing is False
    assert response.investigation_id.startswith("inv_")
    assert runtime.mapping["topic_runtime_only"] == response.investigation_id


def test_trending_service_preserves_stale_snapshot_semantics(tmp_path):
    repo = TrendingRepository(str(tmp_path / "trending.sqlite3"))
    generated_at = datetime.now(timezone.utc) - timedelta(hours=12)
    runtime = _RuntimeStub(
        latest_snapshot=_snapshot(
            "topic_stale",
            generated_at=generated_at,
            fresh_for_hours=6,
        ),
        acquire_returns=False,
    )
    service = TrendingService(repository=repo, runtime_store=runtime)

    feed = service.get_feed(limit=1)

    assert feed.state == "stale"
    assert feed.topics == []
    assert feed.generated_at == generated_at


def test_trending_endpoint_returns_live_envelope_without_demo_cards(monkeypatch):
    monkeypatch.setattr(
        trending_api,
        "_service",
        type(
            "StubService",
            (),
            {
                "get_feed": lambda self, limit=6: TrendingFeedResponse(
                    state="warming",
                    warning="Discovery pipeline is warming up.",
                    topics=[],
                )
            },
        )(),
    )

    response = client.get("/api/trending")
    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "warming"
    assert payload["topics"] == []
    assert "Hidden Energy Tax" not in str(payload)


def test_trending_endpoint_serves_runtime_backed_snapshot(monkeypatch, tmp_path):
    repo = TrendingRepository(str(tmp_path / "trending.sqlite3"))
    runtime = _RuntimeStub(latest_snapshot=_snapshot("topic_api"))
    service = TrendingService(repository=repo, runtime_store=runtime)
    monkeypatch.setattr(trending_api, "_service", service)

    response = client.get("/api/trending?limit=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "ready"
    assert len(payload["topics"]) == 1
    assert payload["topics"][0]["id"] == "topic_api"
