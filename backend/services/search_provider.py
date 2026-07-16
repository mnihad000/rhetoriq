from __future__ import annotations

from abc import ABC, abstractmethod
from urllib.parse import urlparse

from models.investigation import InvestigationPlanTimeWindow, SearchResult


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
    return UnconfiguredSearchProvider()


def source_name_from_url(url: str) -> str:
    return urlparse(url).netloc.lower()
