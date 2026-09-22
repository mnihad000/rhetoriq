"""Request and verify a bounded Flink savepoint for controlled B6 shutdown."""

from __future__ import annotations

import argparse
import time

import httpx

from config import get_settings


def create_savepoint(base_url: str, target: str, *, timeout_seconds: float = 120.0) -> str:
    base_url = base_url.rstrip("/")
    with httpx.Client(timeout=10.0) as client:
        overview = client.get(f"{base_url}/jobs/overview")
        overview.raise_for_status()
        running = [job for job in overview.json().get("jobs", []) if job.get("state") == "RUNNING"]
        if len(running) != 1:
            raise RuntimeError(f"Expected exactly one running Flink job; found {len(running)}")
        job_id = running[0]["jid"]
        triggered = client.post(
            f"{base_url}/jobs/{job_id}/savepoints",
            json={"target-directory": target, "cancel-job": False},
        )
        triggered.raise_for_status()
        request_id = triggered.json()["request-id"]
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            status = client.get(f"{base_url}/jobs/{job_id}/savepoints/{request_id}")
            status.raise_for_status()
            payload = status.json()
            state = payload.get("status", {}).get("id")
            if state == "COMPLETED":
                location = payload.get("operation", {}).get("location")
                if not location:
                    raise RuntimeError("Flink completed the savepoint without returning a location")
                return str(location)
            if state == "FAILED":
                raise RuntimeError("Flink reported savepoint failure")
            time.sleep(2.0)
    raise TimeoutError("Flink savepoint did not complete before the deadline")


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Create a verified Flink savepoint")
    parser.add_argument("--url", default=settings.FLINK_REST_URL)
    parser.add_argument("--target", default="file:///opt/flink/savepoints")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    args = parser.parse_args()
    print(create_savepoint(args.url, args.target, timeout_seconds=args.timeout_seconds))


if __name__ == "__main__":
    main()
