from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from config import get_settings
from models.investigation import FetchFailure, RawPage

logger = logging.getLogger(__name__)


class HttpPageFetcher:
    def __init__(self, cache=None) -> None:
        self._settings = get_settings()
        self._cache = cache  # optional TrendingRedisCache

    def fetch(self, url: str) -> RawPage | FetchFailure:
        # Cache hit — skip the network entirely
        if self._cache is not None:
            cached = self._cache.get_page(url)
            if cached:
                try:
                    return RawPage(**cached)
                except Exception:
                    pass  # malformed cache entry → fall through to live fetch

        headers = {
            "User-Agent": "RhetoriQ/0.1 (+investigation retriever)",
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        }
        try:
            with httpx.Client(
                timeout=self._settings.FETCH_TIMEOUT_SECONDS,
                headers=headers,
                follow_redirects=True,
            ) as client:
                response = client.get(url)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            return FetchFailure(url=url, error_type="timeout", message=str(exc), retryable=True)
        except httpx.HTTPStatusError as exc:
            return FetchFailure(
                url=url,
                error_type="http_status",
                message=str(exc),
                status_code=exc.response.status_code,
                retryable=500 <= exc.response.status_code < 600,
            )
        except httpx.HTTPError as exc:
            return FetchFailure(url=url, error_type="http_error", message=str(exc), retryable=True)

        content_type = response.headers.get("content-type")
        if content_type and "html" not in content_type and "xml" not in content_type:
            return FetchFailure(
                url=url,
                error_type="unsupported_content_type",
                message=f"Unsupported content type: {content_type}",
                status_code=response.status_code,
                retryable=False,
            )

        page = RawPage(
            url=url,
            final_url=str(response.url),
            status_code=response.status_code,
            content_type=content_type,
            html=response.text,
            fetched_at=datetime.now(timezone.utc),
        )

        # Store in Redis for future runs (TTL managed by cache layer)
        if self._cache is not None:
            self._cache.set_page(url, page.model_dump(mode="json"))

        return page


def get_page_fetcher(cache=None) -> HttpPageFetcher:
    """Return the standard HTTP page fetcher."""
    return HttpPageFetcher(cache=cache)
