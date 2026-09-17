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
    TrendingTopic,
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
from services.signal_repository import SignalRepository
from services.flink_health import get_flink_health

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
        self._signals = SignalRepository(self._settings.persistence_target)
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
        stream_feed = self._get_flink_feed(limit=limit, now=now)
        if stream_feed is not None:
            return stream_feed
        snapshot = self._get_latest_snapshot()
        last_error = self._runtime.get_last_error()
        fallback_active = bool(getattr(self._settings, "ENABLE_FLINK_TRENDING", False))
        fallback_warning = None
        if fallback_active:
            fallback_warning = "Flink trending fallback is active; showing the last valid legacy snapshot."

        if fallback_active and snapshot is not None:
            stale = snapshot.fresh_until < now
            return TrendingFeedResponse(
                state="stale" if stale else snapshot.state,
                generated_at=snapshot.generated_at,
                fresh_until=snapshot.fresh_until,
                last_completed_run_at=snapshot.last_completed_run_at,
                last_reseed_at=snapshot.last_reseed_at,
                warning=_join_warnings(fallback_warning, self._snapshot_warning(snapshot, last_error)),
                topics=snapshot.topics[:limit],
                source="legacy",
                fallback_active=True,
            )

        if snapshot is None:
            if fallback_active:
                return TrendingFeedResponse(
                    state="warming",
                    warning="Flink trending fallback is active, but no valid legacy snapshot is available yet.",
                    topics=[],
                    source="legacy",
                    fallback_active=True,
                )
            snapshot = self._cache_demo_snapshot(now=now)
            return TrendingFeedResponse(
                state="ready",
                generated_at=snapshot.generated_at,
                fresh_until=snapshot.fresh_until,
                last_completed_run_at=snapshot.last_completed_run_at,
                last_reseed_at=snapshot.last_reseed_at,
                warning=_join_warnings(fallback_warning, self._snapshot_warning(snapshot, last_error)),
                topics=snapshot.topics[:limit],
                source="legacy",
                fallback_active=fallback_active,
            )

        if snapshot.fresh_until >= now:
            topics = self._fill_demo_topics(snapshot.topics, limit=limit, now=now)
            return TrendingFeedResponse(
                state="ready",
                generated_at=snapshot.generated_at,
                fresh_until=snapshot.fresh_until,
                last_completed_run_at=snapshot.last_completed_run_at,
                last_reseed_at=snapshot.last_reseed_at,
                warning=_join_warnings(
                    fallback_warning,
                    self._snapshot_warning(snapshot, self._demo_fill_warning(snapshot.topics, limit, last_error)),
                ),
                topics=topics,
                source="legacy",
                fallback_active=fallback_active,
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
                last_error or fallback_warning or "Trending snapshot is stale and a refresh is pending.",
            ),
            topics=topics,
            source="legacy",
            fallback_active=fallback_active,
        )

    def get_status(self) -> TrendingStatusResponse:
        snapshot = self._get_latest_snapshot()
        feed = self.get_feed(limit=1)
        try:
            evaluation = self._signals.latest_evaluation()
            signal_health = self._signals.health()
        except Exception:
            evaluation = None
            signal_health = {"status": "unavailable", "signal_revision_count": 0}
        evaluation_at = _parse_datetime(evaluation.get("evaluated_at")) if evaluation else None
        details = evaluation.get("details", {}) if evaluation else {}
        stream_health = get_flink_health(self._settings, evaluation=evaluation, now=datetime.now(timezone.utc))
        try:
            from services.kafka_runtime import kafka_consumer_health
            kafka = kafka_consumer_health(self._settings)
        except Exception:
            kafka = {"status": "unavailable", "lag": None, "dlq_total": None}
        enrichment_group = next((group for group in kafka.get("groups", []) if group.get("role") == "enrichment"), {})
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
            source=feed.source,
            fallback_active=feed.fallback_active,
            pipeline_evaluated_at=evaluation_at,
            flink=stream_health,
            kafka=kafka,
            enrichment={
                "status": signal_health.get("enrichment_status", "unavailable"),
                "workers": signal_health.get("enrichment_workers", []),
                "artifact_failure_count": signal_health.get("artifact_failure_count", 0),
                "backlog": enrichment_group.get("lag"),
                "dlq_depth": kafka.get("dlq_counts", {}).get("documents.enrichment-requested.dlq.v1"),
                **(details.get("enrichment", {}) if isinstance(details, dict) else {}),
            },
            signals={
                "status": "ready" if evaluation else "warming",
                "latest_evaluation": evaluation_at,
                "revision_count": signal_health.get("signal_revision_count", 0),
                **(details.get("signals", {}) if isinstance(details, dict) else {}),
            },
        )

    def _get_flink_feed(self, *, limit: int, now: datetime) -> TrendingFeedResponse | None:
        """Return the stream feed only after both runtime and heartbeat gates pass."""
        if not getattr(self._settings, "ENABLE_FLINK_TRENDING", False):
            return None
        try:
            evaluation = self._signals.latest_evaluation()
        except Exception:
            return None
        health = get_flink_health(self._settings, evaluation=evaluation, now=now)
        evaluated_at = _parse_datetime(evaluation.get("evaluated_at")) if evaluation else None
        max_age = int(getattr(self._settings, "FLINK_HEARTBEAT_MAX_AGE_SECONDS", 1800))
        heartbeat_age = (now - evaluated_at).total_seconds() if evaluated_at else None
        heartbeat_ok = bool(heartbeat_age is not None and 0 <= heartbeat_age <= max_age)
        ready = health.get("status") == "ready" and heartbeat_ok
        if not ready:
            return None
        try:
            projected_signals = self._signals.latest_signals(limit=max(limit * 4, 24))
        except Exception:
            return None
        topics = _topics_from_signals(projected_signals, now=now)[:limit]
        warning = None
        if evaluation and isinstance(evaluation.get("details"), dict):
            warning = evaluation["details"].get("warning")
        return TrendingFeedResponse(
            state="ready" if topics else "warming",
            generated_at=now,
            fresh_until=evaluated_at + timedelta(seconds=max_age) if evaluated_at else None,
            last_completed_run_at=evaluated_at,
            warning=warning or (None if topics else "Flink is healthy but no signal has cleared the publish thresholds yet."),
            topics=topics,
            source="flink",
            fallback_active=False,
            pipeline_evaluated_at=evaluated_at,
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
        if getattr(self._settings, "ENABLE_FLINK_TRENDING", False):
            try:
                available_topics.extend(_topics_from_signals(self._signals.latest_signals(), now=datetime.now(timezone.utc)))
            except Exception:
                # A projection outage should not block manual investigation of
                # a valid legacy topic.
                pass
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


def _parse_datetime(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _join_warnings(*warnings: str | None) -> str | None:
    values = [warning.strip() for warning in warnings if warning and warning.strip()]
    return " ".join(dict.fromkeys(values)) or None


def _display_signal_status(latest: dict, sustained: dict, emerging: dict, payload: dict) -> str:
    status = str(payload.get("status") or latest.get("lifecycle_status") or "")
    if status in {"emerging", "amplifying", "mainstreaming", "declining"}:
        return status
    return "sustained" if sustained else "emerging"


def _topics_from_signals(signals: list[dict], *, now: datetime) -> list:
    """Merge emerging and sustained revisions into one card per phrase."""
    grouped: dict[str, dict] = {}
    for signal in signals:
        if signal.get("lifecycle_status") == "resolved":
            continue
        payload = signal.get("payload") or {}
        phrase = str(payload.get("canonical_phrase") or payload.get("canonical_phrase_text") or signal.get("canonical_phrase_id") or signal.get("signal_id"))
        if phrase not in grouped:
            grouped[phrase] = {"signals": [], "payload": payload}
        grouped[phrase]["signals"].append(signal)

    topics = []
    for phrase, group in grouped.items():
        members = group["signals"]
        payload = group["payload"]
        latest = max(members, key=lambda item: (item.get("signal_revision", 0), item.get("occurred_at") or ""))
        metrics: dict[str, dict] = {}
        for member in members:
            for metric in member.get("metrics", []):
                horizon = metric.get("horizon") or member.get("horizon") or "unknown"
                metrics[horizon] = metric
        if not metrics:
            metrics[str(latest.get("horizon") or "unknown")] = {
                "observed_count": (payload.get("score") or {}).get("observed_count", 0),
                "baseline_count": (payload.get("score") or {}).get("baseline_count", 0),
                "spike": (payload.get("score") or {}).get("spike", 0),
                "confidence": (payload.get("score") or {}).get("confidence", 0),
            }
        emerging = metrics.get("emerging") or metrics.get("6h") or {}
        sustained = metrics.get("sustained") or metrics.get("24h") or {}
        full_metrics = payload.get("horizon_metrics") or {}
        diversity_metric = full_metrics.get("sustained") or full_metrics.get("emerging") or {}
        score = max(float(item.get("confidence", 0) or 0) for item in metrics.values())
        confidence_label = "High" if score >= 0.7 else "Medium" if score >= 0.45 else "Low"
        first = _parse_datetime(payload.get("first_observed_at")) or _parse_datetime(latest.get("occurred_at")) or now
        latest_at = _parse_datetime(payload.get("latest_observed_at")) or _parse_datetime(latest.get("occurred_at")) or now
        docs = list(dict.fromkeys(doc for member in members for doc in member.get("supporting_document_ids", [])))
        limitations = list(dict.fromkeys(item for member in members for item in member.get("coverage_limitations", [])))
        source_count = max(
            max((int(metric.get("observed_count", 0) or 0) for metric in metrics.values()), default=0),
            max((len(member.get("supporting_document_ids", [])) for member in members), default=0),
        )
        publisher_count = max(int((member.get("payload") or {}).get("publisher_diversity", 0) or 0) for member in members)
        title = str(payload.get("title") or phrase).replace("_", " ").strip().title()
        topics.append(TrendingTopic.model_validate({
            "id": latest["signal_id"],
            "title": title,
            "canonical_phrase": phrase,
            "summary": payload.get("summary") or f"{title} is emerging in the monitored dataset.",
            "related_phrases": payload.get("related_phrases") or payload.get("related_phrase_ids") or [],
            "status": _display_signal_status(latest, sustained, emerging, payload),
            "confidence_label": confidence_label,
            "confidence_score": score,
            "source_count": source_count,
            "publisher_count": publisher_count,
            "first_observed_at": first,
            "latest_observed_at": latest_at,
            "source_diversity_snapshot": payload.get("source_diversity_snapshot") or diversity_metric.get("source_type_counts") or {},
            "timeline": [],
            "velocity_score": float(emerging.get("spike", sustained.get("spike", 0)) or 0),
            "persistence_runs": int(payload.get("persistence_runs", diversity_metric.get("baseline_persistence", 0)) or 0),
            "provider_mix": payload.get("provider_mix") or diversity_metric.get("provider_mix") or {},
            "supporting_document_ids": docs,
            "signal_id": latest["signal_id"],
            "signal_revision": latest.get("signal_revision", 0),
            "pipeline_source": "flink",
            "emerging_observed_count": emerging.get("observed_count"),
            "emerging_baseline_count": emerging.get("baseline_count"),
            "emerging_spike": emerging.get("spike"),
            "sustained_observed_count": sustained.get("observed_count"),
            "sustained_baseline_count": sustained.get("baseline_count"),
            "sustained_spike": sustained.get("spike"),
            "event_time_quality": latest.get("event_time_quality") or "unknown",
            "coverage_limitations": limitations,
            "origin_disclaimer": payload.get("origin_disclaimer") or "This signal identifies the first observation in the available dataset, not proven origin.",
        }))
    return sorted(topics, key=lambda topic: topic.confidence_score, reverse=True)
