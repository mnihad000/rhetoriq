from __future__ import annotations

import json
import logging
import signal
import socket
import threading
from typing import Any

from config import get_settings
from models.events import validate_event
from services.event_handlers import EventHandlers
from services.event_store import EventStore
from services.job_queue import JobQueue, LeaseLostError, _safe_error

logger = logging.getLogger(__name__)

def _redact(message: str) -> str:
    return _safe_error(message)


class EventProcessor:
    """Transport-independent event application and idempotency ledger."""

    def __init__(self, store: EventStore, handlers: EventHandlers) -> None:
        self.store = store
        self.handlers = handlers

    def process(
        self,
        topic: str,
        decoded: dict[str, Any] | str,
        *,
        partition: int | None = None,
        offset: int | None = None,
        replay_namespace: str | None = None,
    ) -> bool:
        event = validate_event(topic, decoded)
        consumer_name, handler = self.handlers.handler_for(topic)
        should_process = self.store.begin_consumption(
            consumer_name,
            event,
            topic=topic,
            partition=partition,
            offset=offset,
            replay_namespace=replay_namespace,
        )
        if not should_process:
            return False
        try:
            handler(event)
        except Exception as exc:
            self.store.fail_consumption(
                consumer_name, event.event_id, _redact(str(exc)), replay_namespace=replay_namespace
            )
            raise
        self.store.complete_consumption(consumer_name, event.event_id, replay_namespace=replay_namespace)
        return True


class DatabaseEventWorker:
    """Lease and execute durable event jobs from ``event_outbox``."""

    def __init__(self, role: str | None = None, *, queue=None, store=None, handlers=None) -> None:
        self.settings = get_settings()
        target = self.settings.persistence_target
        self.queue = queue or JobQueue(target)
        self.store = store or EventStore(target)
        self.processor = EventProcessor(store=self.store, handlers=handlers or EventHandlers(target))
        self.role = role or "database"
        self.worker_id = f"{self.role}-{socket.gethostname()}"
        self._stop = threading.Event()

    def run_once(self) -> bool:
        lease_seconds = float(getattr(self.settings, "JOB_LEASE_SECONDS", 60.0))
        max_attempts = max(1, int(getattr(self.settings, "JOB_MAX_ATTEMPTS", 3)))
        job = self.queue.claim(self.worker_id, lease_seconds, max_attempts=max_attempts)
        if job is None:
            return False
        lost = threading.Event()
        heartbeat_stop = threading.Event()
        renew_seconds = max(0.1, float(getattr(self.settings, "JOB_LEASE_RENEW_SECONDS", 15.0)))

        def renew_loop() -> None:
            while not heartbeat_stop.wait(renew_seconds):
                if not self.queue.renew(job, lease_seconds):
                    lost.set()
                    return
                try:
                    self.store.heartbeat(self.worker_id, "database")
                except Exception:
                    logger.exception("database worker heartbeat failed")

        heartbeat = threading.Thread(target=renew_loop, name=f"lease-{job.event_id}", daemon=True)
        heartbeat.start()
        try:
            if job.finalize_only:
                event = validate_event(job.topic, job.event_json)
                consumer_name, _handler = self.processor.handlers.handler_for(job.topic)
                with self.queue.fenced(job):
                    completed = self.store.consumption_completed(consumer_name, event)
                if completed:
                    return self.queue.complete(job)
                # An expired/crashed claim at the retry limit is leased for
                # this small reconciliation step.  Never dispatch its event
                # handler again; terminal run state is fenced before the job
                # row is marked failed.
                if not self._finish_investigation_failure(job, RuntimeError("job retry limit exhausted")):
                    logger.error("could not fence terminal failure for exhausted job %s", job.event_id)
                    return False
                self.queue.fail(job, "job retry limit exhausted", max_attempts, 0)
                return False
            with self.queue.fenced(job):
                self.processor.process(job.topic, job.event_json)
        except LeaseLostError:
            logger.warning("lease lost while processing job %s", job.event_id)
            return False
        except Exception as exc:
            if job.attempts >= int(getattr(self.settings, "JOB_MAX_ATTEMPTS", 3)):
                if not self._finish_investigation_failure(job, exc):
                    logger.error("could not fence terminal failure for exhausted job %s", job.event_id)
                    return False
            self.queue.fail(
                job, _redact(str(exc)),
                max_attempts,
                float(getattr(self.settings, "JOB_RETRY_BACKOFF_SECONDS", 1.0)),
            )
            return False
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=min(renew_seconds, 1.0))
        if lost.is_set():
            return False
        return self.queue.complete(job)

    def _finish_investigation_failure(self, job, error: Exception) -> bool:
        if job.topic != "investigations.requested.v1":
            return True
        try:
            payload = json.loads(job.event_json).get("payload", {})
            run_id = payload.get("run_id")
            repository = getattr(self.processor.handlers, "research", None)
            if not run_id or repository is None:
                # A malformed investigation job has no run to fence.  It can
                # still be failed safely as a queue record.
                return True
            with self.queue.fenced(job):
                run = repository.get_run(run_id)
                if run is not None and run.status not in {"queued", "running"}:
                    return True
                repository.finish_run(
                    run_id, status="failed", terminal_decision="failed",
                    warnings=[_redact(str(error))], event_type="run.failed",
                    payload={"status": "failed", "error": _redact(str(error))},
                )
            return True
        except LeaseLostError:
            logger.warning("lease lost before terminal failure for job %s", job.event_id)
            return False
        except Exception:
            logger.exception("could not record terminal investigation failure for %s", job.event_id)
            return False

    def run_forever(self) -> None:
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                signal.signal(signum, lambda *_: self._stop.set())
        poll_seconds = max(0.05, float(getattr(self.settings, "JOB_POLL_SECONDS", 0.5)))
        try:
            while not self._stop.is_set():
                self.store.heartbeat(self.worker_id, "database")
                self.run_once()
                self._stop.wait(poll_seconds)
        finally:
            self.store.heartbeat(self.worker_id, "database", "stopped")
            close = getattr(self.processor.handlers, "close", None)
            if close:
                close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    DatabaseEventWorker().run_forever()


if __name__ == "__main__":
    main()
