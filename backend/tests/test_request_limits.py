from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.request_limits import (
    RateLimitRule,
    RequestLimitMiddleware,
    SlidingWindowLimiter,
    client_key,
    is_investigation_start,
)


def test_sliding_window_rejects_then_allows_after_window() -> None:
    now = [100.0]
    limiter = SlidingWindowLimiter(max_clients=2, clock=lambda: now[0])
    rule = RateLimitRule(limit=2, window_seconds=60)

    assert limiter.check("client", rule) == (True, 0)
    assert limiter.check("client", rule) == (True, 0)
    assert limiter.check("client", rule) == (False, 60)

    now[0] = 160.0
    assert limiter.check("client", rule) == (True, 0)


def test_clearing_limiter_starts_a_new_quota_window() -> None:
    limiter = SlidingWindowLimiter(max_clients=2, clock=lambda: 100.0)
    rule = RateLimitRule(limit=1, window_seconds=60)

    assert limiter.check("client", rule) == (True, 0)
    assert limiter.check("client", rule) == (False, 60)

    limiter.clear()

    assert limiter.check("client", rule) == (True, 0)


def test_client_key_uses_proxy_only_when_explicitly_trusted() -> None:
    headers = {"x-forwarded-for": "203.0.113.8, 10.0.0.1"}

    assert client_key(headers, "198.51.100.4", trust_proxy_headers=False) == "198.51.100.4"
    assert client_key(headers, "198.51.100.4", trust_proxy_headers=True) == "203.0.113.8"


def test_investigation_start_route_detection() -> None:
    assert is_investigation_start("POST", "/api/investigate")
    assert is_investigation_start("POST", "/api/trending/topic_1/investigate")
    assert not is_investigation_start("GET", "/api/investigate")
    assert not is_investigation_start("POST", "/api/investigations/id/retrieve")


def test_middleware_returns_retry_after_for_limited_client() -> None:
    app = FastAPI()
    app.add_middleware(
        RequestLimitMiddleware,
        requests_per_minute=1,
        investigation_starts_per_hour=1,
        max_clients=10,
        trust_proxy_headers=False,
    )

    @app.get("/api/items")
    def items() -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/api/items").status_code == 200
    limited = client.get("/api/items")

    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "60"
    assert limited.json()["retry_after_seconds"] == 60
