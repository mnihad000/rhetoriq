from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from models.trending import DiscoveryDocumentRecord, TopicTimelinePoint, TrendingTopic
from services.phrase_extractor import extract_top_phrases
from services.narrative_scoring import narrative_confidence

_GENERIC_TOPIC_TOKENS = {
    "analysis", "article", "articles", "breaking", "coverage", "debate",
    "debates", "housing", "implications", "industry", "latest", "live",
    "market", "narrative", "narratives", "news", "official", "opinion",
    "policy", "political", "politics", "public", "reaction", "research",
    "review", "statement", "story", "stories", "topic", "topics", "update",
    "updates", "world", "counter", "around",
}
_REFERENCE_DOMAINS = {
    "dictionary.com",
    "imdb.com",
    "play.google.com",
}
_GENERIC_TITLE_PATTERNS = (
    "apps on google play",
    "breaking news, latest news",
    "definition & meaning",
    "industry news for",
)
_MAX_PUBLISHED_DOCUMENT_AGE = timedelta(days=10)


def _topic_id(phrase: str) -> str:
    return "topic_" + hashlib.md5(phrase.encode()).hexdigest()[:10]


class TrendingRanker:
    def __init__(
        self,
        *,
        min_docs: int = 4,
        min_publishers: int = 3,
        min_source_types: int = 2,
    ) -> None:
        self._min_docs = min_docs
        self._min_publishers = min_publishers
        self._min_source_types = min_source_types

    def rank(
        self,
        documents: list[DiscoveryDocumentRecord],
        *,
        now: datetime | None = None,
        max_topics: int = 10,
    ) -> list[TrendingTopic]:
        now = now or datetime.now(timezone.utc)
        live_docs = [
            doc for doc in documents
            if (doc.document.published_at or doc.latest_seen_at) and self._is_rankable_document(doc, now)
        ]
        if not live_docs:
            return []

        phrases = self._extract_ranked_phrases(live_docs)

        selected_topics: list[TrendingTopic] = []
        seen_phrases: set[str] = set()
        for phrase, _count in phrases:
            normalized = phrase.lower().strip()
            if not self._is_publishable_phrase(normalized):
                continue
            if not normalized or any(normalized in seen or seen in normalized for seen in seen_phrases):
                continue
            matched = self._match_docs(normalized, live_docs)
            if not matched:
                continue

            source_count = len(matched)
            publishers = {doc.domain or urlparse(doc.document.url).netloc.lower() for doc in matched}
            source_diversity = Counter(doc.document.source_type for doc in matched)
            persistence_runs = len({run_id for doc in matched for run_id in doc.seen_run_ids})
            velocity_score = self._velocity_score(matched, now)
            cross_source = len(source_diversity)
            high_velocity_exception = velocity_score >= 2.5 and cross_source >= self._min_source_types
            if source_count < self._min_docs:
                continue
            if len(publishers) < self._min_publishers:
                continue
            if cross_source < self._min_source_types and not high_velocity_exception:
                continue
            if persistence_runs < 2 and not high_velocity_exception:
                continue

            provider_mix = Counter(provider for doc in matched for provider in doc.providers)
            confidence_score, confidence_label = self._confidence(
                source_count=source_count,
                publisher_count=len(publishers),
                source_type_count=cross_source,
                persistence_runs=persistence_runs,
                velocity_score=velocity_score,
                provider_mix=provider_mix,
            )
            title = self._title_case_phrase(phrase)
            first_observed = min(doc.document.published_at or doc.first_seen_at for doc in matched)
            latest_observed = max(doc.document.published_at or doc.latest_seen_at for doc in matched)
            related_phrases = self._related_phrases(normalized, matched)
            topic = TrendingTopic(
                id=_topic_id(normalized),
                title=title,
                canonical_phrase=normalized,
                summary=self._summary(title, source_diversity, source_count, len(publishers), velocity_score),
                related_phrases=related_phrases,
                status=self._status(velocity_score, source_diversity, latest_observed, now),
                confidence_label=confidence_label,
                confidence_score=confidence_score,
                source_count=source_count,
                publisher_count=len(publishers),
                first_observed_at=first_observed,
                latest_observed_at=latest_observed,
                source_diversity_snapshot=dict(source_diversity),
                timeline=self._timeline(matched, now),
                velocity_score=velocity_score,
                persistence_runs=persistence_runs,
                provider_mix=dict(provider_mix),
                supporting_document_ids=[doc.doc_id for doc in matched[:12]],
            )
            selected_topics.append(topic)
            seen_phrases.add(normalized)
            if len(selected_topics) >= max_topics:
                break

        selected_topics.sort(key=lambda topic: (topic.velocity_score, topic.confidence_score, topic.source_count), reverse=True)
        return selected_topics[:max_topics]

    def extract_candidate_phrases(
        self,
        documents: list[DiscoveryDocumentRecord],
        *,
        top_n: int = 8,
        min_doc_freq: int = 2,
    ) -> list[str]:
        now = datetime.now(timezone.utc)
        live_docs = [
            doc for doc in documents
            if (doc.document.published_at or doc.latest_seen_at) and self._is_rankable_document(doc, now)
        ]
        if not live_docs:
            return []

        selected: list[str] = []
        seen_phrases: set[str] = set()
        for phrase, _count in self._extract_ranked_phrases(live_docs, top_n=max(top_n * 3, 24), min_doc_freq=min_doc_freq):
            normalized = phrase.lower().strip()
            if not self._is_publishable_phrase(normalized):
                continue
            if any(normalized in seen or seen in normalized for seen in seen_phrases):
                continue
            matched = self._match_docs(normalized, live_docs)
            if len(matched) < min_doc_freq:
                continue
            selected.append(normalized)
            seen_phrases.add(normalized)
            if len(selected) >= top_n:
                break
        return selected

    def is_seedable_phrase(self, phrase: str) -> bool:
        normalized = phrase.lower().strip()
        return self._is_publishable_phrase(normalized)

    def _fallback_phrases(self, documents: list[DiscoveryDocumentRecord]) -> list[tuple[str, int]]:
        counts: Counter[str] = Counter()
        for record in documents:
            phrases = record.document.phrases or []
            for phrase in phrases:
                if 2 <= len(phrase.split()) <= 4:
                    counts[phrase.lower()] += 1
        return counts.most_common(50)

    def _extract_ranked_phrases(
        self,
        documents: list[DiscoveryDocumentRecord],
        *,
        top_n: int = 150,
        min_doc_freq: int = 2,
    ) -> list[tuple[str, int]]:
        texts = [
            " ".join(
                part for part in [doc.document.title, " ".join((doc.document.phrases or [])[:4])] if part
            )
            for doc in documents
        ]
        phrases = extract_top_phrases(texts, ngram_range=(2, 4), top_n=top_n, min_doc_freq=min_doc_freq)
        if not phrases:
            phrases = self._fallback_phrases(documents)
        return phrases

    def _match_docs(self, phrase: str, documents: list[DiscoveryDocumentRecord]) -> list[DiscoveryDocumentRecord]:
        matched: list[DiscoveryDocumentRecord] = []
        for record in documents:
            haystack = " ".join(
                [
                    record.document.title.lower(),
                    (record.document.snippet or "").lower(),
                    " ".join((record.document.phrases or [])).lower(),
                ]
            )
            if phrase in haystack:
                matched.append(record)
        matched.sort(key=lambda record: record.document.published_at or record.latest_seen_at, reverse=True)
        return matched

    def _velocity_score(self, documents: list[DiscoveryDocumentRecord], now: datetime) -> float:
        recent_cutoff = now - timedelta(hours=6)
        prior_cutoff = now - timedelta(hours=12)
        recent = sum(1 for doc in documents if (doc.document.published_at or doc.latest_seen_at) >= recent_cutoff)
        prior = sum(
            1
            for doc in documents
            if prior_cutoff <= (doc.document.published_at or doc.latest_seen_at) < recent_cutoff
        )
        return round(recent / max(1, prior), 2)

    def _confidence(
        self,
        *,
        source_count: int,
        publisher_count: int,
        source_type_count: int,
        persistence_runs: int,
        velocity_score: float,
        provider_mix: Counter[str],
    ) -> tuple[float, str]:
        return narrative_confidence(
            source_count=source_count, publisher_count=publisher_count,
            source_type_count=source_type_count, persistence_runs=persistence_runs,
            velocity_score=velocity_score, provider_mix=provider_mix,
        )

    def _status(
        self,
        velocity_score: float,
        diversity: Counter[str],
        latest_observed: datetime,
        now: datetime,
    ) -> str:
        if latest_observed < now - timedelta(hours=18):
            return "declining"
        if velocity_score >= 2.5 and len(diversity) >= 2:
            return "emerging"
        if velocity_score >= 1.6 and len(diversity) >= 2:
            return "amplifying"
        if velocity_score >= 1.0 and diversity.get("national_news", 0) > 0:
            return "mainstreaming"
        return "steady"

    def _summary(
        self,
        title: str,
        diversity: Counter[str],
        source_count: int,
        publisher_count: int,
        velocity_score: float,
    ) -> str:
        mix = ", ".join(f"{source_type.replace('_', ' ')}: {count}" for source_type, count in diversity.most_common(3))
        return (
            f"{title} is appearing across {source_count} sources from {publisher_count} publishers "
            f"with a {velocity_score}x recent velocity. Source mix: {mix}."
        )

    def _timeline(
        self,
        documents: list[DiscoveryDocumentRecord],
        now: datetime,
    ) -> list[TopicTimelinePoint]:
        buckets: dict[str, int] = defaultdict(int)
        for doc in documents:
            observed = doc.document.published_at or doc.latest_seen_at
            bucket = observed.strftime("%Y-%m-%d")
            buckets[bucket] += 1
        points: list[TopicTimelinePoint] = []
        for offset in range(6, -1, -1):
            day = (now - timedelta(days=offset)).strftime("%Y-%m-%d")
            points.append(
                TopicTimelinePoint(
                    timestamp=datetime.fromisoformat(f"{day}T00:00:00+00:00"),
                    count=buckets.get(day, 0),
                )
            )
        return points

    def _related_phrases(self, phrase: str, documents: list[DiscoveryDocumentRecord]) -> list[str]:
        counts: Counter[str] = Counter()
        for doc in documents:
            for candidate in doc.document.phrases or []:
                normalized = candidate.lower().strip()
                if normalized == phrase or len(normalized.split()) < 2:
                    continue
                counts[normalized] += 1
        return [value for value, _count in counts.most_common(5)]

    def _title_case_phrase(self, phrase: str) -> str:
        return " ".join(token.capitalize() for token in phrase.split())

    def _is_publishable_phrase(self, phrase: str) -> bool:
        tokens = phrase.split()
        if len(tokens) < 2:
            return False
        if len(set(tokens)) == 1:
            return False
        return any(token not in _GENERIC_TOPIC_TOKENS for token in tokens)

    def _is_rankable_document(self, record: DiscoveryDocumentRecord, now: datetime) -> bool:
        title = record.document.title.strip()
        if len(title.split()) < 2:
            return False

        published_at = record.document.published_at
        if published_at is not None and published_at < now - _MAX_PUBLISHED_DOCUMENT_AGE:
            return False

        domain = (record.domain or urlparse(record.document.url).netloc).lower()
        if any(domain == blocked or domain.endswith(f".{blocked}") for blocked in _REFERENCE_DOMAINS):
            return False

        title_lower = title.lower()
        if any(pattern in title_lower for pattern in _GENERIC_TITLE_PATTERNS):
            return False

        path = urlparse(record.document.url).path.strip("/")
        if not path and self._looks_like_generic_homepage(title_lower):
            return False

        return True

    def _looks_like_generic_homepage(self, title: str) -> bool:
        if "| substack" in title:
            return True
        generic_title_tokens = {
            token
            for token in re.findall(r"[a-z0-9][a-z0-9'\-]{1,}", title)
            if token not in {"fifa", "spacex", "supreme", "congress", "white", "house"}
        }
        return generic_title_tokens.issubset(_GENERIC_TOPIC_TOKENS | {"today", "latest", "videos"})

