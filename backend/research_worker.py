from __future__ import annotations

import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor
from uuid import uuid4

from config import get_settings
from services.autonomous_research import AutonomousResearchEngine, LeaseHeartbeat, get_research_manager, get_research_repository


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    manager = get_research_manager()
    audit = get_research_repository()
    worker_id = f"worker-{uuid4().hex[:8]}"
    executor = ThreadPoolExecutor(max_workers=settings.RESEARCH_WORKER_CONCURRENCY, thread_name_prefix="rq-worker")
    futures: set[Future] = set()

    def execute(run_id: str) -> None:
        try:
            with LeaseHeartbeat(audit, run_id, worker_id):
                AutonomousResearchEngine(manager.repository, audit).execute(run_id)
        except Exception as exc:
            logging.exception("Research run %s failed", run_id)
            audit.update_run(run_id, status="failed", terminal_decision="failed", warnings=[str(exc)[:500]])
            audit.append_event(run_id, "run.failed", {"error": str(exc)[:300]})

    while True:
        audit.heartbeat_worker(worker_id, "worker")
        futures = {future for future in futures if not future.done()}
        capacity = settings.RESEARCH_WORKER_CONCURRENCY - len(futures)
        for run in audit.list_resumable_runs():
            if capacity <= 0:
                break
            if audit.claim_run(run.run_id, worker_id, settings.RESEARCH_LEASE_SECONDS):
                futures.add(executor.submit(execute, run.run_id))
                capacity -= 1
        time.sleep(1)


if __name__ == "__main__":
    main()
