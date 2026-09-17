"""Shared, fail-closed Flink readiness checks for B4 shadow and cutover paths."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any

import httpx


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _timestamp_ms(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    parsed = _as_datetime(value)
    return parsed.timestamp() * 1000 if parsed else None


def get_flink_health(settings: Any, *, evaluation: dict[str, Any] | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Inspect the named B4 job, checkpoints, task managers, and heartbeat.

    The monitor intentionally runs with the feature flag disabled so operators
    can observe shadow health. The feed itself decides whether to use this
    result as authoritative.
    """
    now = now or datetime.now(timezone.utc)
    base = str(getattr(settings, "FLINK_REST_URL", "http://localhost:8082")).rstrip("/")
    max_age_seconds = min(1800, max(1, int(getattr(settings, "FLINK_HEARTBEAT_MAX_AGE_SECONDS", 1800))))
    result: dict[str, Any] = {
        "status": "unavailable",
        "state": "UNKNOWN",
        "cutover_enabled": bool(getattr(settings, "ENABLE_FLINK_TRENDING", False)),
        "job_name_required": "rhetoriq-b4-flink",
        "checkpoint_age_ms": None,
        "checkpoint_failures": [],
        "taskmanagers": [],
        "signal_freshness": evaluation.get("evaluated_at") if evaluation else None,
    }
    try:
        overview_response = httpx.get(f"{base}/jobs/overview", timeout=2)
        if not overview_response.is_success:
            return {**result, "detail": f"Flink overview HTTP {overview_response.status_code}"}
        overview = overview_response.json()
        jobs = overview.get("jobs", []) if isinstance(overview, dict) else []
        job = next((item for item in jobs if item.get("name") == "rhetoriq-b4-flink"), None)
        if job is None:
            return {**result, "status": "unhealthy", "state": "MISSING", "jobs": jobs[:10]}
        result.update({"job_id": job.get("jid") or job.get("job_id"), "job_name": job.get("name"), "state": job.get("state")})
        jid = result["job_id"]

        checkpoints: dict[str, Any] = {}
        if jid:
            response = httpx.get(f"{base}/jobs/{jid}/checkpoints", timeout=2)
            if response.is_success and isinstance(response.json(), dict):
                checkpoints = response.json()
        latest = checkpoints.get("latest", {}) if isinstance(checkpoints, dict) else {}
        completed = latest.get("completed") if isinstance(latest, dict) else None
        failures = latest.get("failed") if isinstance(latest, dict) else None
        result["checkpoint_failures"] = failures if isinstance(failures, list) else ([failures] if failures else [])
        result["checkpoints"] = {"completed": completed or {}, "failed": result["checkpoint_failures"]}
        timestamp = None
        if isinstance(completed, dict):
            timestamp = completed.get("latest_ack_timestamp") or completed.get("trigger_timestamp") or completed.get("timestamp")
        timestamp_ms = _timestamp_ms(timestamp)
        if timestamp_ms is not None and timestamp_ms < 10_000_000_000:
            timestamp_ms *= 1000
        if timestamp_ms is not None:
            result["checkpoint_age_ms"] = int(now.timestamp() * 1000 - timestamp_ms)

        taskmanagers: list[dict[str, Any]] = []
        response = httpx.get(f"{base}/taskmanagers", timeout=2)
        if response.is_success and isinstance(response.json(), dict):
            taskmanagers = response.json().get("taskmanagers", [])
        healthy_taskmanagers = []
        for taskmanager in taskmanagers:
            total = taskmanager.get("slotsNumber", taskmanager.get("slots_total", taskmanager.get("total_slots", 0))) or 0
            available = taskmanager.get("freeSlots", taskmanager.get("slots_available", total))
            if int(total) > 0 and int(available) >= 0:
                healthy_taskmanagers.append(taskmanager)
        result["taskmanagers"] = healthy_taskmanagers

        heartbeat = evaluation or {}
        evaluated_at = _as_datetime(heartbeat.get("evaluated_at")) if heartbeat else None
        heartbeat_age = (now - evaluated_at).total_seconds() if evaluated_at else None
        result["heartbeat_age_seconds"] = heartbeat_age
        heartbeat_status = heartbeat.get("status") or (heartbeat.get("details") or {}).get("status")
        heartbeat_ok = (
            heartbeat_age is not None and 0 <= heartbeat_age <= max_age_seconds
            and heartbeat_status != "degraded"
        )
        checkpoint_ok = (
            job.get("state") == "RUNNING"
            and bool(completed)
            and result["checkpoint_age_ms"] is not None
            and 0 <= result["checkpoint_age_ms"] <= max_age_seconds * 1000
        )
        result["status"] = "ready" if checkpoint_ok and healthy_taskmanagers and heartbeat_ok else "unhealthy"
        if not heartbeat_ok:
            result["heartbeat_status"] = "missing_or_stale"
        return result
    except Exception as exc:
        # Do not echo the configured REST URL; it may contain deployment auth.
        return {**result, "detail": f"{type(exc).__name__}: Flink health request failed"}


class FlinkHealthMonitor:
    def __init__(self, settings: Any) -> None:
        self.settings = settings

    def check(self, *, evaluation: dict[str, Any] | None = None, now: datetime | None = None) -> dict[str, Any]:
        return get_flink_health(self.settings, evaluation=evaluation, now=now)


# Small aliases keep the monitor convenient for API callers and tests.
check_flink_health = get_flink_health
flink_health = get_flink_health
