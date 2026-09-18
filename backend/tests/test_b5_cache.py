from __future__ import annotations

import json

from services.b5_cache import B5Cache


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.expirations = []

    def ping(self):
        return True

    def get(self, key):
        return self.values.get(key)

    def setex(self, key, ttl, value):
        self.expirations.append((key, ttl))
        self.values[key] = value

    def delete(self, key):
        self.values.pop(key, None)


def test_cache_is_revision_scoped_and_uses_ttl():
    redis = FakeRedis()
    cache = B5Cache(redis_client=redis, ttl_seconds=30, max_item_bytes=1000)
    payload = {"complete": True, "generation": "g1"}

    assert cache.set("search", "inv_1", "elastic", "g1", payload, revision=1, semantic_hash="h1")
    assert cache.get("search", "inv_1", "elastic", "g1", revision=1, semantic_hash="h1") == payload
    assert cache.get("search", "inv_1", "elastic", "g1", revision=2, semantic_hash="h2") is None
    assert redis.expirations[0][1] == 30
    assert cache.metrics["hits"] == 1


def test_cache_skips_oversized_and_invalid_values():
    redis = FakeRedis()
    cache = B5Cache(redis_client=redis, max_item_bytes=10)
    assert cache.set("search", "i", "t", "g", {"large": "x" * 100}) is False
    assert cache.set("search", "i", "t", "g", ["invalid"]) is False
    assert cache.get("search", "i", "t", "g") is None


def test_cache_errors_fall_through():
    class Broken(FakeRedis):
        def ping(self):
            raise RuntimeError("offline")

    cache = B5Cache(redis_client=Broken())
    assert cache.get("search", "i", "t", "g") is None
    assert cache.metrics["errors"] >= 1


def test_verified_redis_tls_uses_ssl_connection_without_invalid_constructor_flag():
    cache = B5Cache("rediss://redis:6379/0",password="fixture",ca_cert="/fixture/ca.crt")
    pool = cache._client().connection_pool
    assert pool.connection_class.__name__=="SSLConnection"
    assert pool.connection_kwargs["ssl_cert_reqs"]=="required"
    assert pool.connection_kwargs["ssl_check_hostname"] is True
    assert "ssl" not in pool.connection_kwargs
