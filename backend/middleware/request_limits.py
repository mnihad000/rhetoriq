"""Small, dependency-free request limiter for the single-instance MVP API."""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass
import ipaddress
import json
import threading
import time
from typing import Callable

from starlette.types import ASGIApp, Message, Receive, Scope, Send


HEALTH_PATHS = frozenset({
    "/", "/health", "/health/ready", "/health/b5", "/health/embeddings",
    "/api/research/health",
})


@dataclass(frozen=True)
class RateLimitRule:
    """A bounded number of requests permitted within a rolling time window."""

    limit: int
    window_seconds: int


class SlidingWindowLimiter:
    """Thread-safe rolling-window limiter with bounded client-key retention."""

    def __init__(self, *, max_clients: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._max_clients = max_clients
        self._clock = clock
        self._requests: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def clear(self) -> None:
        """Clear quota history when the owning application is reset."""
        with self._lock:
            self._requests.clear()

    def check(self, key: str, rule: RateLimitRule) -> tuple[bool, int]:
        """Record a request and return whether it is allowed and retry seconds."""
        if rule.limit == 0:
            return True, 0

        now = self._clock()
        cutoff = now - rule.window_seconds
        with self._lock:
            timestamps = self._requests.get(key)
            if timestamps is None:
                if len(self._requests) >= self._max_clients:
                    self._requests.popitem(last=False)
                timestamps = deque()
                self._requests[key] = timestamps
            else:
                self._requests.move_to_end(key)

            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()
            if len(timestamps) >= rule.limit:
                return False, max(1, int(timestamps[0] + rule.window_seconds - now + 0.999))
            timestamps.append(now)
            return True, 0


def client_key(headers: dict[str, str], peer_host: str | None, *, trust_proxy_headers: bool) -> str:
    """Return a safe per-client key without trusting spoofable proxy headers by default."""
    candidate = peer_host or "unknown"
    if trust_proxy_headers:
        forwarded = headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        if forwarded:
            candidate = forwarded
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return "unknown"


def is_investigation_start(method: str, path: str) -> bool:
    return method == "POST" and (
        path == "/api/investigate"
        or (path.startswith("/api/trending/") and path.endswith("/investigate"))
    )


class RequestLimitMiddleware:
    """Apply general and expensive-investigation limits before route execution."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        requests_per_minute: int,
        investigation_starts_per_hour: int,
        max_clients: int,
        trust_proxy_headers: bool,
        limiter: SlidingWindowLimiter | None = None,
    ) -> None:
        self.app = app
        self._general_rule = RateLimitRule(requests_per_minute, 60)
        self._investigation_rule = RateLimitRule(investigation_starts_per_hour, 3600)
        self._limiter = limiter if limiter is not None else SlidingWindowLimiter(max_clients=max_clients)
        self._trust_proxy_headers = trust_proxy_headers

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope["method"].upper()
        path = scope["path"]
        if method == "OPTIONS" or path in HEALTH_PATHS:
            await self.app(scope, receive, send)
            return

        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope["headers"]}
        peer = scope.get("client")
        peer_host = peer[0] if peer else None
        client = client_key(headers, peer_host, trust_proxy_headers=self._trust_proxy_headers)

        allowed, retry_after = self._limiter.check(f"general:{client}", self._general_rule)
        if allowed and is_investigation_start(method, path):
            allowed, retry_after = self._limiter.check(f"investigation:{client}", self._investigation_rule)
        if allowed:
            await self.app(scope, receive, send)
            return

        body = json.dumps(
            {
                "detail": "Rate limit exceeded. Please retry later.",
                "retry_after_seconds": retry_after,
            }
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"retry-after", str(retry_after).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
