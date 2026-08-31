from __future__ import annotations

from abc import ABC, abstractmethod
from urllib.parse import urlparse

import httpx

from config import get_settings

from models.investigation import InvestigationPlanTimeWindow, SearchResult
from services.url_policy import has_embedded_credentials


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


class UnconfiguredSearchProvider(SearchProvider):
    """Placeholder used until model-native web search is implemented."""

    name = "not_configured"

    def search(
        self,
        query: str,
        time_window: InvestigationPlanTimeWindow,
        source_types: list[str],
        limit: int,
    ) -> list[SearchResult]:
        raise RuntimeError(
            "Live web search is not configured. "
            "The agents still plan search queries, but a model-native search provider "
            "must be implemented before live retrieval can execute them."
        )


class SearxngSearchProvider(SearchProvider):
    name = "searxng"

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = (base_url or get_settings().SEARXNG_BASE_URL).rstrip("/")
        self.last_diagnostics: dict = {}

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
        response = httpx.get(
            f"{self._base_url}/search",
            params=params,
            headers={"Accept": "application/json", "User-Agent": "RhetoriQ/0.2"},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        self.last_diagnostics = {
            "query": query,
            "time_filter": time_range,
            "unresponsive_engines": payload.get("unresponsive_engines") or [],
            "suggestion_count": len(payload.get("suggestions") or []),
        }
        output: list[SearchResult] = []
        for rank, item in enumerate((payload.get("results") or [])[:limit], start=1):
            url = str(item.get("url") or "").strip()
            title = str(item.get("title") or "").strip()
            if not url or not title:
                continue
            if has_embedded_credentials(url):
                continue
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
                        "time_filter": time_range,
                        "unresponsive_engines": self.last_diagnostics["unresponsive_engines"],
                        "source_types_requested": source_types,
                    },
                )
            )
        return output


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


def build_search_provider() -> SearchProvider:
    settings = get_settings()
    if settings.RESEARCH_RUNTIME in {"auto", "langgraph"} and not settings.DEMO_MODE:
        return SearxngSearchProvider()
    return UnconfiguredSearchProvider()


def source_name_from_url(url: str) -> str:
    return urlparse(url).netloc.lower()
