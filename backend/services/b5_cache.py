"""Small, failure-tolerant cache used by the B5 retrieval facade.

The cache is deliberately a best-effort optimisation.  Canonical projection
state is always checked by :mod:`b5_search` before a cached response is used.
This module only knows how to store bounded JSON values under generation and
target scoped keys, which also makes it straightforward to inject a fake
Redis client in tests.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)


class B5Cache:
    """Revision/generation scoped Redis cache with safe fall-through."""

    def __init__(
        self,
        redis_url: str | None = None,
        *,
        redis_client: Any | None = None,
        ttl_seconds: int = 30,
        max_item_bytes: int = 1_000_000,
        namespace: str = "rhetoriq:b5",
        password: str | None = None,
        ca_cert: str | None = None,
    ) -> None:
        self.redis_url = redis_url or ""
        self._redis = redis_client
        self.ttl_seconds = max(1, int(ttl_seconds))
        self.max_item_bytes = max(1, int(max_item_bytes))
        self.namespace = namespace.strip(":") or "rhetoriq:b5"
        self.password = password or None
        self.ca_cert = ca_cert or None
        self.metrics: dict[str, int | float] = {
            "hits": 0,
            "misses": 0,
            "sets": 0,
            "skips": 0,
            "errors": 0,
            "calls": 0,"total_latency_ms":0.0,"last_latency_ms":0.0,"max_latency_ms":0.0,
        }

    @property
    def available(self) -> bool:
        if self._redis is None and not self.redis_url:
            return False
        try:
            self._call("ping")
            return True
        except Exception as exc:  # pragma: no cover - depends on Redis client
            self.metrics["errors"] += 1
            logger.debug("B5 cache unavailable: %s", exc)
            return False

    def key(
        self,
        kind: str,
        domain_id: str | None,
        target: str | None,
        generation: str | int | None,
        *,
        revision: str | int | None = None,
        semantic_hash: str | None = None,
    ) -> str:
        """Return a stable key; values are hashed only to avoid unsafe key text."""
        if domain_id is None and target is None and generation is None and str(kind).startswith(f"{self.namespace}:"):
            return str(kind)
        parts = [
            str(kind or "result"),
            str(domain_id or "global"),
            str(target or "canonical"),
            str(generation if generation is not None else "current"),
            str(revision if revision is not None else "r0"),
            str(semantic_hash or "h0"),
        ]
        encoded = ":".join(hashlib.sha256(part.encode("utf-8")).hexdigest()[:32] for part in parts)
        return f"{self.namespace}:{encoded}"

    def get(
        self,
        kind: str,
        domain_id: str | None = None,
        target: str | None = None,
        generation: str | int | None = None,
        *,
        revision: str | int | None = None,
        semantic_hash: str | None = None,
        validator: Callable[[dict[str, Any]], bool] | None = None,
    ) -> dict[str, Any] | None:
        if not self.available:
            self.metrics["misses"] += 1
            return None
        cache_key = self.key(
            kind, domain_id, target, generation,
            revision=revision, semantic_hash=semantic_hash,
        )
        try:
            raw = self._call("get",cache_key)
            if raw is None:
                self.metrics["misses"] += 1
                return None
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            value = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(value, dict) or (validator and not validator(value)):
                self.metrics["misses"] += 1
                return None
            self.metrics["hits"] += 1
            return value
        except Exception as exc:
            self.metrics["errors"] += 1
            logger.debug("B5 cache get failed: %s", exc)
            return None

    def set(
        self,
        kind: str,
        domain_id: str | None = None,
        target: str | None = None,
        generation: str | int | None = None,
        value: dict[str, Any] | None = None,
        *,
        revision: str | int | None = None,
        semantic_hash: str | None = None,
    ) -> bool:
        # Also accept the conventional ``set(key, value)`` form for callers
        # that already composed a revision-scoped key themselves.
        if value is None and isinstance(domain_id, dict):
            value = domain_id
            domain_id = target = generation = None
        if not isinstance(value, dict):
            self.metrics["skips"] += 1
            return False
        try:
            payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        except Exception:
            self.metrics["errors"] += 1
            return False
        if len(payload.encode("utf-8")) > self.max_item_bytes:
            self.metrics["skips"] += 1
            return False
        if not self.available:
            self.metrics["skips"] += 1
            return False
        try:
            cache_key = self.key(
                kind, domain_id, target, generation,
                revision=revision, semantic_hash=semantic_hash,
            )
            self._call("setex",cache_key, self.ttl_seconds, payload)
            self.metrics["sets"] += 1
            return True
        except Exception as exc:
            self.metrics["errors"] += 1
            logger.debug("B5 cache set failed: %s", exc)
            return False

    def delete(
        self,
        kind: str,
        domain_id: str | None = None,
        target: str | None = None,
        generation: str | int | None = None,
        *,
        revision: str | int | None = None,
        semantic_hash: str | None = None,
    ) -> bool:
        """Best-effort invalidation helper used by operational callers."""
        try:
            if not self.available:
                return False
            self._call("delete",self.key(
                kind, domain_id, target, generation,
                revision=revision, semantic_hash=semantic_hash,
            ))
            return True
        except Exception as exc:
            self.metrics["errors"] += 1
            logger.debug("B5 cache delete failed: %s", exc)
            return False

    def _client(self) -> Any:
        if self._redis is None:
            import redis

            options: dict[str, Any] = {
                "decode_responses": True,
                "socket_timeout": 2,
                "socket_connect_timeout": 2,
            }
            if self.password:
                options["password"] = self.password
            if self.ca_cert:
                options.update({"ssl_ca_certs": self.ca_cert,"ssl_cert_reqs":"required","ssl_check_hostname":True})
                if self.redis_url.startswith("redis://"):
                    self.redis_url = "rediss://" + self.redis_url[len("redis://"):]
            self._redis = redis.from_url(self.redis_url, **options)
        return self._redis

    def _call(self,method: str,*args: Any) -> Any:
        started = time.perf_counter()
        try:
            return getattr(self._client(),method)(*args)
        finally:
            elapsed = (time.perf_counter()-started)*1000
            self.metrics["calls"] += 1
            self.metrics["total_latency_ms"] += elapsed
            self.metrics["last_latency_ms"] = elapsed
            self.metrics["max_latency_ms"] = max(self.metrics["max_latency_ms"],elapsed)

    def status(self) -> dict[str,Any]:
        healthy = self.available
        info = {}
        if healthy:
            try:
                for section,fields in (("stats",("evicted_keys","keyspace_hits","keyspace_misses")),("memory",("used_memory","maxmemory"))):
                    values = self._call("info",section)
                    info.update({field:values.get(field) for field in fields})
            except Exception:
                info["metrics_available"] = False
        requests = self.metrics["hits"]+self.metrics["misses"]
        return {"status":"healthy" if healthy else "unavailable","metrics":dict(self.metrics),
                "hit_rate":self.metrics["hits"]/requests if requests else 0.0,"redis":info}


# A descriptive alias keeps integration code readable while retaining the
# short class name used by the B5 contract.
RevisionScopedCache = B5Cache
