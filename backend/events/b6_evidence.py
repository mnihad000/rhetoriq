"""Write a sanitized application-level evidence snapshot for a B6 demo run."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from config import get_settings
from migrations.runner import verify_migrations
from services.b5_repository import ProjectionRepository
from services.event_store import EventStore


_REDACT_KEYS = {
    "authorization",
    "cookie",
    "detail",
    "error",
    "last_error",
    "password",
    "secret",
    "token",
}


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _sanitize(item)
            for key, item in value.items()
            if str(key).lower() not in _REDACT_KEYS
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def _fetch_json(url: str) -> dict[str, Any]:
    with urlopen(url, timeout=10) as response:  # noqa: S310 - fixed internal URL supplied by the operator
        return json.load(response)


def collect(api_url: str) -> dict[str, Any]:
    settings = get_settings()
    checks: dict[str, Any] = {}
    try:
        verify_migrations(settings.DATABASE_URL)
        checks["migrations"] = {"status": "current"}
    except Exception as exc:
        checks["migrations"] = {"status": "failed", "reason": type(exc).__name__}

    for name, path in (
        ("readiness", "/health/ready"),
        ("b5", "/health/b5"),
        ("research", "/api/research/health"),
    ):
        try:
            checks[name] = _fetch_json(f"{api_url.rstrip('/')}{path}")
        except Exception as exc:
            checks[name] = {"status": "unavailable", "reason": type(exc).__name__}

    try:
        checks["event_store"] = EventStore(settings.persistence_target).health()
    except Exception as exc:
        checks["event_store"] = {"status": "unavailable", "reason": type(exc).__name__}
    try:
        checks["b5_projection"] = ProjectionRepository(settings.persistence_target).status()
    except Exception as exc:
        checks["b5_projection"] = {"status": "unavailable", "reason": type(exc).__name__}

    return _sanitize(
        {
            "schema": "rhetoriq-b6-evidence-v1",
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "deployment": settings.DEPLOYMENT_ENV,
            "model_revision": settings.B5_MODEL_REVISION,
            "checks": checks,
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture sanitized B6 application evidence")
    parser.add_argument("--api-url", default="http://api:8000")
    parser.add_argument("--output", default="/evidence/b6-evidence.json")
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(collect(args.api_url), indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
