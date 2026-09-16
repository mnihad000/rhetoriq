#!/usr/bin/env python
"""Manual Redis connectivity check.

This module performs no work on import and reads credentials only from the
REDIS_URL environment variable.
"""

from __future__ import annotations

import os

import redis


def main() -> int:
    redis_url = os.getenv("REDIS_URL", "").strip()
    if not redis_url:
        print("REDIS_URL is not configured.")
        return 2
    try:
        client = redis.from_url(
            redis_url,
            decode_responses=True,
            socket_timeout=10,
            socket_connect_timeout=10,
        )
        client.ping()
        client.set("rhetoriq_connection_check", "ok", ex=30)
        assert client.get("rhetoriq_connection_check") == "ok"
        client.delete("rhetoriq_connection_check")
        print("Redis connection check passed.")
        return 0
    except (redis.ConnectionError, redis.TimeoutError) as exc:
        print(f"Redis connection check failed: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
