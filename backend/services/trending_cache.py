"""Redis-backed cache for the trending discovery pipeline.

Two cache tiers:
  - Search results  → 1-hour TTL
  - Page fetches    → 6-hour TTL  (raw HTML from URLs)

Both fall back gracefully if Redis is unavailable.
"""
from __future__ import annotations

import hashlib
import json
import logging

logger = logging.getLogger(__name__)

_SEARCH_TTL = 3_600    # 1 hour
_PAGE_TTL   = 21_600   # 6 hours


class TrendingRedisCache:
    def __init__(self, redis_url: str) -> None:
        self._client = None
        self.available = False
        try:
            import redis
            client = redis.Redis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=10,
                socket_timeout=10,
            )
            client.ping()
            self._client = client
            self.available = True
            logger.info("TrendingRedisCache: connected")
        except Exception as exc:
            logger.warning("TrendingRedisCache: Redis not available — %s", exc)

    # ------------------------------------------------------------------
    # Search result cache
    # ------------------------------------------------------------------

    def get_search(self, provider: str, query: str) -> list | None:
        if not self.available:
            return None
        try:
            raw = self._client.get(self._search_key(provider, query))
            return json.loads(raw) if raw else None
        except Exception:
            return None

    def set_search(self, provider: str, query: str, results: list) -> None:
        if not self.available:
            return
        try:
            self._client.set(
                self._search_key(provider, query),
                json.dumps(results),
                ex=_SEARCH_TTL,
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Page fetch cache
    # ------------------------------------------------------------------

    def get_page(self, url: str) -> dict | None:
        if not self.available:
            return None
        try:
            raw = self._client.get(self._page_key(url))
            return json.loads(raw) if raw else None
        except Exception:
            return None

    def set_page(self, url: str, page_data: dict) -> None:
        if not self.available:
            return
        try:
            self._client.set(self._page_key(url), json.dumps(page_data), ex=_PAGE_TTL)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Key helpers
    # ------------------------------------------------------------------

    def _search_key(self, provider: str, query: str) -> str:
        h = hashlib.sha256(query.lower().encode()).hexdigest()[:20]
        return f"rq:ts:search:{provider}:{h}"

    def _page_key(self, url: str) -> str:
        h = hashlib.sha256(url.strip().encode()).hexdigest()[:20]
        return f"rq:ts:page:{h}"
