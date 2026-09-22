"""Readiness probe for ordinary, outbox, and enrichment workers."""

from __future__ import annotations

from datetime import datetime, timezone
import os
import socket

from config import get_settings
from services.database import connect


def main() -> None:
    settings = get_settings()
    role = os.getenv("WORKER_PROBE_ROLE", settings.KAFKA_WORKER_ROLE)
    worker_id = os.getenv("WORKER_PROBE_ID") or f"{role}-{socket.gethostname()}"
    table = "enrichment_worker_health" if role == "enrichment" else "runtime_worker_health"
    with connect(settings.persistence_target) as db:
        row = db.execute(
            f"SELECT state, heartbeat_at FROM {table} WHERE worker_id=?", (worker_id,)
        ).fetchone()
    if not row or row["state"] not in {"ready", "healthy"}:
        raise SystemExit(1)
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["heartbeat_at"])).total_seconds()
    raise SystemExit(0 if age < 45 else 1)


if __name__ == "__main__":
    main()
