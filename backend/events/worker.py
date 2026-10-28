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
from services.job_queue import JobQueue, LeaseLostError

logger = logging.getLogger(__name__)

# Logical role/topic metadata retained for operational tooling and compatibility
# tests. Dispatch itself is database-backed and imports no transport client.
TOPICS_BY_ROLE = {
    "b5-elasticsearch": ("corpus.projections.v1",),
    "b5-neo4j": ("corpus.projections.v1", "investigation.projections.v1"),
    "b5-minilm": ("corpus.projections.v1",),
    "documents": ("documents.processed.v1",),
    "signals": ("signals.detected.v1",),
    "investigations": ("investigations.requested.v1",),
    "projections": ("investigations.stage-events.v1", "investigations.completed.v1",
                    "pipeline.evaluated.v1", "documents.late.v1"),
    "all": ("documents.processed.v1", "signals.detected.v1", "investigations.requested.v1",
            "investigations.stage-events.v1", "investigations.completed.v1",
            "pipeline.evaluated.v1", "documents.late.v1"),
}


def _redact(message: str) -> str:
    import re
    value = re.sub(r"(?i)\bBearer\s+[^\s,;\"']+", "Bearer <redacted>", message)
    return re.sub(r"(?i)(password|secret|token|authorization|api[_-]?key)\s*[=:]\s*[^\s,;]+",
                  r"\1=<redacted>", value)[:500]


def _is_permanent(error: Exception) -> bool:
    # Avoid importing the removed Kafka runtime merely to identify its legacy
    # schema exception. The stable class name remains compatible for callers.
    if type(error).__name__ == "SchemaRegistryError":
        return True
    try:
        from jsonschema import ValidationError as JsonSchemaValidationError
        from pydantic import ValidationError as PydanticValidationError
        return isinstance(error, (JsonSchemaValidationError, PydanticValidationError, ValueError))
    except ImportError:  # pragma: no cover
        return isinstance(error, ValueError)


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
        job = self.queue.claim(self.worker_id, lease_seconds)
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
            with self.queue.fenced(job):
                self.processor.process(job.topic, job.event_json)
        except LeaseLostError:
            logger.warning("lease lost while processing job %s", job.event_id)
            return False
        except Exception as exc:
            if job.attempts >= int(getattr(self.settings, "JOB_MAX_ATTEMPTS", 3)):
                self._finish_investigation_failure(job, exc)
            self.queue.fail(
                job, _redact(str(exc)),
                int(getattr(self.settings, "JOB_MAX_ATTEMPTS", 3)),
                float(getattr(self.settings, "JOB_RETRY_BACKOFF_SECONDS", 1.0)),
            )
            return False
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=min(renew_seconds, 1.0))
        if lost.is_set():
            return False
        return self.queue.complete(job)

    def _finish_investigation_failure(self, job, error: Exception) -> None:
        if job.topic != "investigations.requested.v1":
            return
        try:
            payload = json.loads(job.event_json).get("payload", {})
            run_id = payload.get("run_id")
            repository = getattr(self.processor.handlers, "research", None)
            if not run_id or repository is None:
                return
            with self.queue.fenced(job):
                repository.finish_run(
                    run_id, status="failed", terminal_decision="failed",
                    warnings=[_redact(str(error))], event_type="run.failed",
                    payload={"status": "failed", "error": _redact(str(error))},
                )
        except LeaseLostError:
            logger.warning("lease lost before terminal failure for job %s", job.event_id)
        except Exception:
            logger.exception("could not record terminal investigation failure for %s", job.event_id)

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
