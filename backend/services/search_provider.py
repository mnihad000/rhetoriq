from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

from config import get_settings

from models.investigation import InvestigationPlanTimeWindow, SearchResult
from services.provider_http import ProviderRequestError, RetryingJsonClient
from services.url_policy import has_embedded_credentials


_TRACKING_QUERY_KEYS = {
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref_src", "utm_campaign",
    "utm_content", "utm_medium", "utm_source", "utm_term",
}


class SearchProvider(ABC):
    """Boundary for live internet search implementations."""

    name: str

    @abstractmethod
    def search(
        self,
        query: str,
        time_window: InvestigationPlanTimeWindow,
        source_types: list[str],
        limit: int,
    ) -> list[SearchResult]:
        raise NotImplementedError

    def health(self, *, probe: bool = False) -> dict[str, Any]:
        return {
            "provider": self.name,
            "status": "unknown",
            "fallback": "internal_corpus",
            "probe_performed": probe,
        }


class UnconfiguredSearchProvider(SearchProvider):
    """Explicit degraded provider used when live search is intentionally unavailable."""

    name = "not_configured"

    def search(
        self,
        query: str,
        time_window: InvestigationPlanTimeWindow,
        source_types: list[str],
        limit: int,
    ) -> list[SearchResult]:
        diagnostics = {
            "provider": self.name,
            "outcome": "unavailable",
            "failure_category": "not_configured",
            "detail": "Live broad-web search is not configured.",
            "fallback": "internal_corpus",
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        raise SearchProviderUnavailable(
            "Live broad-web search is not configured; internal corpus retrieval remains available.",
            diagnostics,
        )

    def health(self, *, probe: bool = False) -> dict[str, Any]:
        return {
            "provider": self.name,
            "status": "unavailable",
            "failure_category": "not_configured",
            "fallback": "internal_corpus",
            "probe_performed": False,
        }


class SearchProviderUnavailable(RuntimeError):
    def __init__(self, message: str, diagnostics: dict[str, Any]) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


class SearxngSearchProvider(SearchProvider):
    name = "searxng"

    def __init__(
        self,
        base_url: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep=None,
    ) -> None:
        settings = get_settings()
        self._base_url = (base_url or settings.SEARXNG_BASE_URL).rstrip("/")
        kwargs: dict[str, Any] = {}
        if sleep is not None:
            kwargs["sleep"] = sleep
        self._client = RetryingJsonClient(
            self.name,
            timeout_seconds=settings.SEARCH_PROVIDER_TIMEOUT_SECONDS,
            max_retries=settings.SEARCH_PROVIDER_MAX_RETRIES,
            backoff_seconds=settings.PROVIDER_RETRY_BACKOFF_SECONDS,
            max_backoff_seconds=settings.PROVIDER_MAX_BACKOFF_SECONDS,
            min_interval_seconds=settings.SEARXNG_MIN_INTERVAL_SECONDS,
            transport=transport,
            **kwargs,
        )
        self.last_diagnostics: dict[str, Any] = {
            "provider": self.name,
            "outcome": "unknown",
            "fallback": "internal_corpus",
        }

    def search(
        self,
        query: str,
        time_window: InvestigationPlanTimeWindow,
        source_types: list[str],
        limit: int,
    ) -> list[SearchResult]:
        params: dict[str, str | int] = {
            "q": query,
            "format": "json",
            "safesearch": 1,
        }
        time_range = {
            "today": "day",
            "this_week": "month",
            "this_month": "month",
            "recent": "month",
        }.get(time_window.label)
        if time_range:
            params["time_range"] = time_range
        try:
            payload, request_diagnostics = self._client.get_json(
                f"{self._base_url}/search",
                params=params,
                headers={"Accept": "application/json", "User-Agent": "RhetoriQ/0.3 (+public evidence research)"},
            )
        except ProviderRequestError as exc:
            self.last_diagnostics = {
                **exc.diagnostics.as_dict(),
                "query": query,
                "fallback": "internal_corpus",
            }
            raise SearchProviderUnavailable(str(exc), self.last_diagnostics) from exc

        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            self.last_diagnostics = {
                **request_diagnostics.as_dict(),
                "outcome": "unavailable",
                "failure_category": "malformed_response",
                "detail": "SearXNG response did not contain a results array.",
                "query": query,
                "fallback": "internal_corpus",
            }
            raise SearchProviderUnavailable(
                "SearXNG returned a malformed response; internal corpus retrieval remains available.",
                self.last_diagnostics,
            )

        malformed_count = 0
        duplicate_count = 0
        self.last_diagnostics = {
            **request_diagnostics.as_dict(),
            "query": query,
            "time_filter": time_range,
            "unresponsive_engines": payload.get("unresponsive_engines") or [],
            "suggestion_count": len(payload.get("suggestions") or []),
            "fallback": "internal_corpus",
        }
        output: list[SearchResult] = []
        seen_urls: set[str] = set()
        for rank, item in enumerate(raw_results, start=1):
            if len(output) >= limit:
                break
            if not isinstance(item, dict):
                malformed_count += 1
                continue
            url = _canonical_result_url(str(item.get("url") or "").strip())
            title = str(item.get("title") or "").strip()
            if not url or not title or urlparse(url).scheme not in {"http", "https"}:
                malformed_count += 1
                continue
            if has_embedded_credentials(url):
                malformed_count += 1
                continue
            if url in seen_urls:
                duplicate_count += 1
                continue
            seen_urls.add(url)
            collected_at = datetime.now(timezone.utc).isoformat()
            output.append(
                SearchResult(
                    query=query,
                    title=title,
                    url=url,
                    snippet=str(item.get("content") or "").strip() or None,
                    rank=rank,
                    provider=self.name,
                    provider_score=float(item["score"]) if item.get("score") is not None else None,
                    metadata={
                        "engines": item.get("engines") or ([item.get("engine")] if item.get("engine") else []),
                        "category": item.get("category"),
                        "published_date": item.get("publishedDate"),
                        "source_native_id": item.get("id"),
                        "canonical_url": url,
                        "collected_at": collected_at,
                        "time_filter": time_range,
                        "unresponsive_engines": self.last_diagnostics["unresponsive_engines"],
                        "source_types_requested": source_types,
                        "evidence_status": "discovery_only",
                        "requires_canonical_revalidation": True,
                        "source_policy": {
                            "decision": "allow_discovery",
                            "basis": "approved_self_operated_search",
                            "citable": False,
                        },
                    },
                )
            )
        self.last_diagnostics.update({
            "accepted_results": len(output),
            "malformed_results": malformed_count,
            "duplicate_results": duplicate_count,
            "outcome": "partial" if self.last_diagnostics["unresponsive_engines"] or malformed_count else "success",
        })
        return output

    def health(self, *, probe: bool = False) -> dict[str, Any]:
        if probe:
            try:
                self.search(
                    "rhetoriq health check",
                    InvestigationPlanTimeWindow(label="recent"),
                    [],
                    1,
                )
            except SearchProviderUnavailable:
                pass
        outcome = self.last_diagnostics.get("outcome", "unknown")
        return {
            **self.last_diagnostics,
            "status": "ready" if outcome in {"success", "partial"} else "unavailable" if outcome == "unavailable" else "unknown",
            "probe_performed": probe,
        }


class MultiSearchProvider:
    def __init__(
        self,
        discovery_provider: SearchProvider | None = None,
        enrichment_provider: SearchProvider | None = None,
        cache=None,
    ) -> None:
        self.discovery_provider = discovery_provider or UnconfiguredSearchProvider()
        self.enrichment_provider = enrichment_provider or UnconfiguredSearchProvider()
        self._cache = cache

    def search_discovery(
        self,
        query: str,
        time_window: InvestigationPlanTimeWindow,
        source_types: list[str],
        limit: int,
    ) -> list[SearchResult]:
        return self._cached_search(
            self.discovery_provider,
            query,
            time_window,
            source_types,
            limit,
        )

    def search_enrichment(
        self,
        query: str,
        time_window: InvestigationPlanTimeWindow,
        source_types: list[str],
        limit: int,
    ) -> list[SearchResult]:
        return self._cached_search(
            self.enrichment_provider,
            query,
            time_window,
            source_types,
            limit,
        )

    def _cached_search(
        self,
        provider: SearchProvider,
        query: str,
        time_window: InvestigationPlanTimeWindow,
        source_types: list[str],
        limit: int,
    ) -> list[SearchResult]:
        if self._cache is not None:
            cached = self._cache.get_search(provider.name, query)
            if cached is not None:
                try:
                    return [SearchResult(**item) for item in cached]
                except Exception:
                    pass

        results = provider.search(query, time_window, source_types, limit)

        if self._cache is not None and results:
            self._cache.set_search(
                provider.name,
                query,
                [result.model_dump(mode="json") for result in results],
            )
        return results

    @property
    def provider_mix(self) -> dict[str, str]:
        return {
            "discovery": self.discovery_provider.name,
            "enrichment": self.enrichment_provider.name,
        }

    @property
    def health(self) -> dict[str, dict[str, Any]]:
        return {
            "discovery": self.discovery_provider.health(),
            "enrichment": self.enrichment_provider.health(),
        }


class CachedSearchProvider(SearchProvider):
    """SearchProvider-compatible cache wrapper for investigation retrieval."""

    def __init__(self, provider: SearchProvider, cache=None) -> None:
        self.provider = provider
        self._cache = cache
        self.name = provider.name

    def search(
        self,
        query: str,
        time_window: InvestigationPlanTimeWindow,
        source_types: list[str],
        limit: int,
    ) -> list[SearchResult]:
        if self._cache is not None:
            cached = self._cache.get_search(self.name, query)
            if cached is not None:
                try:
                    return [SearchResult(**item) for item in cached]
                except Exception:
                    pass

        results = self.provider.search(query, time_window, source_types, limit)

        if self._cache is not None and results:
            self._cache.set_search(
                self.name,
                query,
                [result.model_dump(mode="json") for result in results],
            )
        return results

    def health(self, *, probe: bool = False) -> dict[str, Any]:
        return self.provider.health(probe=probe)


def build_search_provider() -> SearchProvider:
    settings = get_settings()
    if settings.RESEARCH_RUNTIME in {"auto", "langgraph"} and not settings.DEMO_MODE:
        return SearxngSearchProvider()
    return UnconfiguredSearchProvider()


def source_name_from_url(url: str) -> str:
    return urlparse(url).netloc.lower()


def _canonical_result_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        filtered_query = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in _TRACKING_QUERY_KEYS
        ]
        return urlunparse((
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path or "/",
            parsed.params,
            urlencode(filtered_query, doseq=True),
            "",
        ))
    except ValueError:
        return ""
