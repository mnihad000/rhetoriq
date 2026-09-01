from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Thread
from uuid import uuid4

from agents.discovery_agent import DiscoveryAgent
from agents.model_client import MockModelClient
from agents.planner_agent import plan_investigation
from agents.retriever_agent import RetrieverAgent
from config import get_settings
from models.trending import (
    DiscoveryRunStats,
    PublishedTrendingSnapshot,
    TrendingFeedResponse,
    TrendingInvestigationResponse,
    TrendingStatusResponse,
)
from services.investigation_repository import InvestigationRepository
from services.demo_trending import (
    DEMO_TRENDING_WARNING,
    build_demo_trending_snapshot,
    demo_trending_topics,
)
from services.redis_store import PhraseStore
from services.search_provider import source_name_from_url
from services.trending_cache import TrendingRedisCache
from services.trending_ranker import TrendingRanker
from services.trending_repository import TrendingRepository
from services.trending_runtime import TrendingRuntimeStore

_TRENDING_CANDIDATE_EXPANSION_LIMIT = 3
_TRENDING_CANDIDATE_MIN_DOCS = 3
_TRENDING_CANDIDATE_MIN_SOURCES = 2


class TrendingService:
    def __init__(
        self,
        *,
        repository: TrendingRepository,
        runtime_store: TrendingRuntimeStore,
        discovery_agent: DiscoveryAgent | None = None,
        retriever_agent: RetrieverAgent | None = None,
    ) -> None:
        self._settings = get_settings()
        self._repository = repository
        self._runtime = runtime_store
        self._cache = TrendingRedisCache(self._settings.REDIS_URL)
        self._discovery = discovery_agent or DiscoveryAgent(cache=self._cache)
        self._retriever = retriever_agent or RetrieverAgent(
            InvestigationRepository(self._settings.persistence_target)
        )
        self._ranker = TrendingRanker(
            min_docs=self._settings.TRENDING_MIN_DOCS,
            min_publishers=self._settings.TRENDING_MIN_PUBLISHERS,
            min_source_types=self._settings.TRENDING_MIN_SOURCE_TYPES,
        )
        self._phrase_store = PhraseStore(redis_url=self._settings.REDIS_URL)

    def ensure_warm_async(self) -> None:
        snapshot = self._get_latest_snapshot()
        if snapshot is not None:
            return
        if not self._runtime.acquire_refresh_lock():
            return
        thread = Thread(target=self._run_refresh_thread, kwargs={"is_reseed": True}, daemon=True)
        thread.start()

    def get_feed(self, *, limit: int = 10) -> TrendingFeedResponse:
        now = datetime.now(timezone.utc)
        snapshot = self._get_latest_snapshot()
        last_error = self._runtime.get_last_error()

        if snapshot is None:
            snapshot = self._cache_demo_snapshot(now=now)
            return TrendingFeedResponse(
                state="ready",
                generated_at=snapshot.generated_at,
                fresh_until=snapshot.fresh_until,
                last_completed_run_at=snapshot.last_completed_run_at,
                last_reseed_at=snapshot.last_reseed_at,
                warning=self._snapshot_warning(snapshot, last_error),
                topics=snapshot.topics[:limit],
            )

        if snapshot.fresh_until >= now:
            topics = self._fill_demo_topics(snapshot.topics, limit=limit, now=now)
            return TrendingFeedResponse(
                state="ready",
                generated_at=snapshot.generated_at,
                fresh_until=snapshot.fresh_until,
                last_completed_run_at=snapshot.last_completed_run_at,
                last_reseed_at=snapshot.last_reseed_at,
                warning=self._snapshot_warning(
                    snapshot,
                    self._demo_fill_warning(snapshot.topics, limit, last_error),
                ),
                topics=topics,
            )

        triggered = self._schedule_refresh_if_possible(is_reseed=self._needs_reseed(snapshot, now))
        topics = self._fill_demo_topics(snapshot.topics, limit=limit, now=now) if limit >= 3 else []
        return TrendingFeedResponse(
            state="warming" if triggered else "stale",
            generated_at=snapshot.generated_at,
            fresh_until=snapshot.fresh_until,
            last_completed_run_at=snapshot.last_completed_run_at,
            last_reseed_at=snapshot.last_reseed_at,
            warning=self._snapshot_warning(
                snapshot,
                last_error or "Trending snapshot is stale and a refresh is pending.",
            ),
            topics=topics,
        )

    def get_status(self) -> TrendingStatusResponse:
        snapshot = self._get_latest_snapshot()
        feed = self.get_feed(limit=1)
        return TrendingStatusResponse(
            state=feed.state,
            redis_available=self._runtime.redis_available,
            refresh_lock_active=self._runtime.refresh_lock_active(),
            generated_at=snapshot.generated_at if snapshot else None,
            fresh_until=snapshot.fresh_until if snapshot else None,
            last_completed_run_at=snapshot.last_completed_run_at if snapshot else None,
            last_reseed_at=snapshot.last_reseed_at if snapshot else None,
            last_error=self._runtime.get_last_error(),
            latest_snapshot_id=snapshot.snapshot_id if snapshot else None,
        )

    def refresh_now(self, *, is_reseed: bool = False) -> PublishedTrendingSnapshot:
        return self._refresh(is_reseed=is_reseed)

    def start_investigation_for_topic(
        self,
        topic_id: str,
        investigation_repository,
    ) -> TrendingInvestigationResponse:
        snapshot = self._get_latest_snapshot()
        available_topics = list(snapshot.topics if snapshot is not None else [])
        available_topics.extend(demo_trending_topics())
        topic = next((candidate for candidate in available_topics if candidate.id == topic_id), None)
        if topic is None:
            raise ValueError(f"Trending topic '{topic_id}' not found.")

        ttl_seconds = int(timedelta(hours=self._settings.TRENDING_REFRESH_HOURS).total_seconds())
        cached = self._runtime.get_topic_investigation(topic_id)
        if cached and investigation_repository.investigation_exists(cached):
            return TrendingInvestigationResponse(
                investigation_id=cached,
                reused_existing=True,
                topic_id=topic.id,
                canonical_phrase=topic.canonical_phrase,
            )

        plan = plan_investigation(
            f"Trace the narrative around {topic.canonical_phrase}",
            prior_context={
                "canonical_phrase": topic.canonical_phrase,
                "topic_id": topic.id,
                "related_phrases": topic.related_phrases,
            },
        )
        investigation_id = f"inv_{uuid4().hex}"
        investigation_repository.save_plan(investigation_id, plan.query_text, plan)
        self._runtime.set_topic_investigation(topic_id, investigation_id, ttl_seconds=ttl_seconds)
        return TrendingInvestigationResponse(
            investigation_id=investigation_id,
            reused_existing=False,
            topic_id=topic.id,
            canonical_phrase=topic.canonical_phrase,
        )

    def _schedule_refresh_if_possible(self, *, is_reseed: bool) -> bool:
        if not self._runtime.acquire_refresh_lock():
            return False
        thread = Thread(target=self._run_refresh_thread, kwargs={"is_reseed": is_reseed}, daemon=True)
        thread.start()
        return True

    def _run_refresh_thread(self, *, is_reseed: bool) -> None:
        try:
            self._refresh(is_reseed=is_reseed)
        finally:
            self._runtime.release_refresh_lock()

    def _refresh(self, *, is_reseed: bool) -> PublishedTrendingSnapshot:
        run_id = f"disc_{uuid4().hex}"
        prior_topics = []
        previous = self._get_latest_snapshot()
        if previous is not None:
            prior_topics = self._seedable_prior_topics(previous.topics)
        queries = self._discovery.build_queries(prior_topics=prior_topics, is_reseed=is_reseed)
        self._repository.create_run(run_id, is_reseed=is_reseed, queries=queries)
        try:
            batch = self._discovery.discover(prior_topics=prior_topics, is_reseed=is_reseed)
        except Exception as exc:
            self._repository.complete_run(
                run_id,
                stats=self._empty_stats(query_count=len(queries)),
                warnings=[],
                error=str(exc),
            )
            self._runtime.set_last_error(str(exc))
            raise

        accepted = 0
        duplicates = 0
        for candidate in batch.candidates:
            document = self._discovery.normalize_candidate(candidate)
            document.source_name = candidate.search_result.metadata.get("source_name_hint") or source_name_from_url(document.url)
            record, created = self._repository.save_discovery_document(
                run_id,
                document,
                canonical_url=self._normalize_url(document.url),
                domain=source_name_from_url(document.url),
                provider=candidate.search_result.provider,
                search_query=candidate.search_result.query,
            )
            if created:
                accepted += 1
            else:
                duplicates += 1

        stats = batch.stats.model_copy(
            update={
                "accepted_documents": accepted,
                "duplicate_documents": duplicates + batch.stats.duplicate_documents,
            }
        )
        documents = self._repository.list_discovery_documents()
        run_documents = self._documents_for_run(run_id, documents)
        expansion_stats, expansion_warnings = self._expand_top_candidates(run_id, run_documents)
        stats = stats.model_copy(
            update={
                "query_count": stats.query_count + expansion_stats.query_count,
                "result_count": stats.result_count + expansion_stats.result_count,
                "fetched_pages": stats.fetched_pages + expansion_stats.fetched_pages,
                "accepted_documents": stats.accepted_documents + expansion_stats.accepted_documents,
                "duplicate_documents": stats.duplicate_documents + expansion_stats.duplicate_documents,
            }
        )
        documents = self._repository.list_discovery_documents()

        # Record each document's phrases in Redis phrase counter for spike scores.
        now_ts = datetime.now(timezone.utc)
        for rec in documents:
            doc = rec.document
            ts = doc.published_at or rec.latest_seen_at or now_ts
            for phrase in doc.phrases or []:
                if phrase:
                    self._phrase_store.record_phrase(phrase, ts, doc.id)

        rank_documents = self._documents_for_run(run_id, documents)
        topics = self._ranker.rank(rank_documents, max_topics=self._settings.TRENDING_MAX_TOPICS)
        now = datetime.now(timezone.utc)
        snapshot = PublishedTrendingSnapshot(
            snapshot_id=f"snap_{uuid4().hex}",
            state="ready" if topics else "warming",
            generated_at=now,
            fresh_until=now + timedelta(hours=self._settings.TRENDING_REFRESH_HOURS),
            last_completed_run_at=now,
            last_reseed_at=now if is_reseed else (previous.last_reseed_at if previous else None),
            warning=None if topics else "Discovery run completed but no topics cleared the publish thresholds yet.",
            topics=topics,
        )
        self._repository.complete_run(run_id, stats=stats, warnings=[*batch.warnings, *expansion_warnings])
        self._repository.save_snapshot(snapshot)
        self._runtime.set_latest_snapshot(snapshot)
        self._runtime.set_last_error(None)
        return snapshot

    def _get_latest_snapshot(self) -> PublishedTrendingSnapshot | None:
        snapshot = self._runtime.get_latest_snapshot()
        if snapshot is not None:
            return snapshot

        snapshot = self._repository.get_latest_snapshot()
        if snapshot is not None:
            self._runtime.set_latest_snapshot(snapshot)
        return snapshot

    def _expand_top_candidates(
        self,
        run_id: str,
        documents,
    ) -> tuple[DiscoveryRunStats, list[str]]:
        candidate_phrases = self._ranker.extract_candidate_phrases(documents, top_n=8)
        stats = DiscoveryRunStats()
        warnings: list[str] = []

        for phrase in candidate_phrases[:_TRENDING_CANDIDATE_EXPANSION_LIMIT]:
            plan = plan_investigation(
                f"Trace the narrative around {phrase}",
                prior_context={"canonical_phrase": phrase, "topic_seed": phrase},
                model_client=MockModelClient(),
            )
            preview = self._retriever.expand_candidate(plan, max_rounds=2)
            stats.query_count += sum(len(round_item.queries) for round_item in preview.search_rounds)
            stats.result_count += sum(round_item.discovered_results for round_item in preview.search_rounds)
            stats.fetched_pages += sum(round_item.fetched_pages for round_item in preview.search_rounds)
            warnings.extend(f"candidate:{phrase}:{warning}" for warning in preview.warnings[:6])

            if (
                preview.coverage_summary.total_documents < _TRENDING_CANDIDATE_MIN_DOCS
                or preview.coverage_summary.unique_sources < _TRENDING_CANDIDATE_MIN_SOURCES
            ):
                continue

            for document in preview.documents:
                provider = str((document.metadata or {}).get("provider") or "retriever")
                search_query = str((document.metadata or {}).get("search_query") or phrase)
                _record, created = self._repository.save_discovery_document(
                    run_id,
                    document,
                    canonical_url=self._normalize_url(document.url),
                    domain=source_name_from_url(document.url),
                    provider=provider,
                    search_query=search_query,
                )
                if created:
                    stats.accepted_documents += 1
                else:
                    stats.duplicate_documents += 1

        return stats, warnings

    def _documents_for_run(self, run_id: str, documents):
        return [record for record in documents if run_id in record.seen_run_ids]

    def _seedable_prior_topics(self, topics) -> list[str]:
        selected: list[str] = []
        for topic in topics[:6]:
            if topic.source_count < self._settings.TRENDING_MIN_DOCS:
                continue
            if topic.publisher_count < self._settings.TRENDING_MIN_PUBLISHERS:
                continue
            if not self._ranker.is_seedable_phrase(topic.canonical_phrase):
                continue
            selected.append(topic.canonical_phrase)
        return selected

    def _needs_reseed(self, snapshot: PublishedTrendingSnapshot, now: datetime) -> bool:
        if snapshot.last_reseed_at is None:
            return True
        return snapshot.last_reseed_at <= now - timedelta(hours=self._settings.TRENDING_RESEED_HOURS)

    def _normalize_url(self, url: str) -> str:
        return url.strip().rstrip("/").lower()

    def _snapshot_warning(
        self,
        snapshot: PublishedTrendingSnapshot,
        last_error: str | None,
    ) -> str | None:
        if snapshot.warning and last_error:
            return f"{snapshot.warning} Last error: {last_error}"
        return snapshot.warning or last_error

    def _cache_demo_snapshot(self, *, now: datetime) -> PublishedTrendingSnapshot:
        snapshot = build_demo_trending_snapshot(now=now)
        self._runtime.set_latest_snapshot(snapshot)
        return snapshot

    def _fill_demo_topics(self, topics, *, limit: int, now: datetime) -> list:
        selected = list(topics[:limit])
        if len(selected) >= limit or limit < 3:
            return selected

        seen = {topic.id for topic in selected}
        for topic in demo_trending_topics(now=now):
            if topic.id in seen:
                continue
            selected.append(topic)
            seen.add(topic.id)
            if len(selected) >= limit:
                break
        return selected

    def _demo_fill_warning(
        self,
        topics,
        limit: int,
        last_error: str | None,
    ) -> str | None:
        if limit >= 3 and len(topics) < limit:
            if last_error:
                return f"{DEMO_TRENDING_WARNING} Last error: {last_error}"
            return DEMO_TRENDING_WARNING
        return last_error

    def _empty_stats(self, *, query_count: int):
        return DiscoveryRunStats(query_count=query_count)
