"""Independently scalable enrichment worker boundary.

The worker delegates inference to :class:`EnrichmentService`; the class is
also usable in fixture tests without Kafka or provider credentials.
"""

from __future__ import annotations

from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
import re
import signal
import threading
import time
from typing import Callable, Any
from uuid import uuid4

import httpx

from models.events import (
    DeadLetterEvent,
    DeadLetterPayload,
    ENRICHMENT_REQUESTED_TOPIC,
    EnrichmentRequestedEvent,
    dlq_topic,
)
from services.enrichment import EnrichmentService
from services.enrichment_artifacts import BudgetExceeded, EnrichmentBudgetLedger, EnrichmentArtifactStore
from services.database import connect
from services.kafka_runtime import SchemaRegistryError

_SECRET_PATTERN = re.compile(r"(?i)(password|secret|token|authorization|api[_-]?key)\s*[=:]\s*[^\s,;]+")
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[^\s,;\"']+")


def _redact(message: str) -> str:
    return _SECRET_PATTERN.sub(r"\1=<redacted>", _BEARER_PATTERN.sub("Bearer <redacted>", message))[:500]


def _dlq_event_id(topic: str, partition: int, offset: Any) -> str:
    identity = f"{topic}:{partition}:{offset}"
    return "evt_dlq_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]


def _registry_retryable(error: Exception, raw: bytes | None) -> bool:
    if isinstance(error, httpx.HTTPError):
        return True
    if not isinstance(error, SchemaRegistryError):
        return False
    message = str(error).lower()
    if "framing" in message or "not registered" in message or "schema is not" in message:
        return False
    return any(marker in message for marker in ("http", "timeout", "timed out", "connection", "register", "unavailable"))


class EnrichmentRetryExhausted(RuntimeError):
    pass


class EnrichmentWorker:
    def __init__(self, service: EnrichmentService, *, budget: EnrichmentBudgetLedger | None = None,
                 max_attempts: int = 3, retry_window_seconds: float = 60.0,
                 sleep: Callable[[float], None] | None = None, worker_id: str | None = None) -> None:
        if not 1 <= max_attempts <= 3 or not 0 < retry_window_seconds <= 60:
            raise ValueError("Enrichment allows at most three attempts within 60 seconds")
        self.service = service
        self.budget = budget
        self.max_attempts = max_attempts
        self.retry_window_seconds = retry_window_seconds
        self.sleep = sleep or time.sleep
        self.dead_letters: list[DeadLetterEvent] = []
        self.worker_id = worker_id or os.getenv("ENRICHMENT_WORKER_ID") or f"enrichment-worker-{os.getpid()}-{uuid4().hex[:8]}"
        artifact_store = getattr(service, "artifact_store", None)
        self.health_target = budget.target if budget else getattr(artifact_store, "target", None)
        self.health_state = "healthy"
        self.budget_paused = False
        self._record_health()

    def process_request(self, request: EnrichmentRequestedEvent):
        # Replays and immutable cache hits perform no hosted inference and do
        # not consume request or spend budget.
        if getattr(self.service, "has_cached_artifact", lambda _request: False)(request):
            return self.service.enrich(request)
        started = time.monotonic()
        deadline = started + self.retry_window_seconds
        set_deadline = getattr(self.service, "set_deadline", None)
        if callable(set_deadline):
            set_deadline(deadline)
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            if time.monotonic() - started > self.retry_window_seconds:
                break
            reservation = None
            try:
                if self.budget:
                    request_limit = int(os.getenv("ENRICHMENT_REQUEST_BUDGET", "0"))
                    spend_limit = int(os.getenv("ENRICHMENT_SPEND_BUDGET_MICROS", "0"))
                    if ("ENRICHMENT_REQUEST_BUDGET" in os.environ and request_limit <= 0) or (
                        "ENRICHMENT_SPEND_BUDGET_MICROS" in os.environ and spend_limit <= 0
                    ):
                        raise BudgetExceeded("Hosted enrichment budget is not enabled")
                    estimated_cost = self._estimated_cost_micros(request)
                    reservation = self.budget.reserve(
                        budget_name=os.getenv("ENRICHMENT_BUDGET_NAME", "hosted-enrichment"),
                        request_cost=3,
                        spend_micros=estimated_cost,
                        request_limit=request_limit,
                        spend_limit_micros=spend_limit,
                    )
                result = self.service.enrich(request)
                if reservation:
                    reservation.settle()
                self.budget_paused = False
                self.health_state = "healthy"
                self._record_health()
                return result
            except BudgetExceeded as exc:
                self.budget_paused = True
                self.health_state = "degraded_budget_exhausted"
                self._record_health(detail=type(exc).__name__)
                raise
            except Exception as exc:
                recorder = getattr(getattr(self.service, "artifact_store", None), "record_failure", None)
                if callable(recorder):
                    try:
                        recorder(request, exc, attempt_count=attempt, pipeline_version=getattr(self.service, "pipeline_version", "b4-v1"))
                    except Exception as audit_exc:
                        raise RuntimeError("Enrichment failure audit could not be persisted") from audit_exc
                last_error = exc
                if reservation:
                    reservation.settle()
                if attempt < self.max_attempts:
                    remaining = self.retry_window_seconds - (time.monotonic() - started)
                    if remaining <= 0:
                        break
                    self.sleep(min(1.0 * (2 ** (attempt - 1)), remaining))
        error = EnrichmentRetryExhausted(str(last_error or "retry window exhausted"))
        self.dead_letters.append(self._dead_letter(request, error, self.max_attempts))
        self.health_state = "degraded_retry_exhausted"
        self._record_health(detail=type(error).__name__)
        raise error from last_error

    def _estimated_cost_micros(self, request: EnrichmentRequestedEvent) -> int:
        estimator = getattr(self.service, "estimated_cost_micros", None)
        if not callable(estimator):
            # A worker with a budget must never silently invent a price. The
            # service owns conservative pricing; this fallback is only for
            # fixture services that do not expose the production interface.
            value = int(os.getenv("ENRICHMENT_ESTIMATED_COST_MICROS", "0"))
        else:
            value = int(estimator(request))
        if value <= 0:
            raise BudgetExceeded("Hosted enrichment estimated cost is not configured")
        return value

    def _record_health(self, *, detail: str | None = None) -> None:
        if not self.health_target:
            return
        try:
            with connect(self.health_target) as db:
                db.execute(
                    """INSERT INTO enrichment_worker_health(worker_id, state, budget_paused, heartbeat_at, detail)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(worker_id) DO UPDATE SET state=excluded.state,
                    budget_paused=excluded.budget_paused, heartbeat_at=excluded.heartbeat_at, detail=excluded.detail""",
                    (self.worker_id, self.health_state, int(self.budget_paused), datetime.now(timezone.utc).isoformat(), str(detail)[:120] if detail else None),
                )
        except Exception:
            # Health reporting cannot turn a successful enrichment into a
            # duplicate or failed event.
            pass

    def process(self, request: EnrichmentRequestedEvent):
        return self.process_request(request)

    def run_forever(self, consumer, publisher, registry, *, topic: str = ENRICHMENT_REQUESTED_TOPIC,
                    output_topic: str = "documents.enriched.v1", subscription_topic: str | None = None,
                    dlq_publisher=None, stop: Callable[[], bool] | None = None,
                    max_messages: int | None = None) -> None:
        """Consume framed requests and publish enriched events with manual commits.

        Dependencies are injected so CI can use fake Kafka clients and recorded
        providers; constructing this worker never contacts Kafka or a model.
        """
        subscription_topic = subscription_topic or topic
        stop = stop or (lambda: False)
        consumer.subscribe([subscription_topic])
        committed = 0
        last_health = time.monotonic()
        pending_message = None
        pending_event = None
        pending_topic = None
        pending_request = None
        try:
            while not stop() and (max_messages is None or committed < max_messages):
                if time.monotonic() - last_health >= 30:
                    self._record_health()
                    last_health = time.monotonic()
                if pending_message is None:
                    message = consumer.poll(1.0)
                    if message is None or message.error():
                        continue
                    decoded = None
                    request = None
                    while True:
                        try:
                            decoded = registry.decode(topic, message.value())
                            request = EnrichmentRequestedEvent.model_validate(decoded)
                            break
                        except Exception as exc:
                            if _registry_retryable(exc, message.value()):
                                self._pause_and_heartbeat(consumer, 1.0, stop)
                                if stop():
                                    return
                                continue
                            pending_message, pending_event, pending_topic = message, self._raw_dead_letter(message, exc, topic), dlq_topic(topic)
                            break
                    if pending_message is None:
                        pending_request = request
                        try:
                            pending_event = self.process_request(request)
                            pending_message, pending_topic = message, output_topic
                        except BudgetExceeded as exc:
                            self._record_health(detail=type(exc).__name__)
                            pending_message, pending_event, pending_topic = message, None, None
                            self._pause_and_heartbeat(consumer, 1.0, stop)
                            continue
                        except Exception as exc:
                            pending_message, pending_event, pending_topic = message, self._dead_letter(request, exc, self.max_attempts, message=message), dlq_topic(topic)

                if pending_message is None:
                    continue
                if pending_event is None:
                    try:
                        pending_event = self.process_request(pending_request)
                        pending_topic = output_topic
                    except BudgetExceeded as exc:
                        self._record_health(detail=type(exc).__name__)
                        self._pause_and_heartbeat(consumer, 1.0, stop)
                        continue
                    except Exception as exc:
                        pending_event = self._dead_letter(pending_request, exc, self.max_attempts, message=pending_message)
                        pending_topic = dlq_topic(topic)
                try:
                    (dlq_publisher or publisher).publish(pending_topic, pending_event)
                    consumer.commit(message=pending_message, asynchronous=False)
                except Exception:
                    self._pause_and_heartbeat(consumer, 1.0, stop)
                    continue
                pending_message = pending_event = pending_topic = pending_request = None
                committed += 1
                self.budget_paused = False
                self.health_state = "healthy"
                self._record_health()
                self._resume_all(consumer)
        finally:
            close = getattr(consumer, "close", None)
            if callable(close):
                close()

    @staticmethod
    def _assigned(consumer):
        try:
            return list(consumer.assignment() or [])
        except Exception:
            return []

    def _pause_and_heartbeat(self, consumer, seconds: float, stop: Callable[[], bool]) -> None:
        assigned = self._assigned(consumer)
        if assigned:
            try:
                consumer.pause(assigned)
            except Exception:
                pass
        deadline = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < deadline and not stop():
            message = consumer.poll(min(0.25, max(0.0, deadline - time.monotonic())))
            if message is not None and not message.error():
                # Rebalances/prefetch may expose a record during pause. Put it
                # back at its exact offset rather than discarding it.
                from confluent_kafka import TopicPartition
                consumer.seek(TopicPartition(message.topic(), message.partition(), message.offset()))

    def _resume_all(self, consumer) -> None:
        assigned = self._assigned(consumer)
        if assigned:
            try:
                consumer.resume(assigned)
            except Exception:
                pass

    def _raw_dead_letter(self, message, error: Exception, topic: str) -> DeadLetterEvent:
        raw = message.value() or b""
        key = message.key().decode("utf-8", errors="replace") if message.key() else None
        return DeadLetterEvent.create(
            DeadLetterPayload(
                original_topic=topic, original_partition=message.partition(),
                original_offset=message.offset(), original_key=key,
                payload_sha256=hashlib.sha256(raw).hexdigest(), safe_payload=None,
                consumer="enrichment-worker", failure_class=type(error).__name__,
                failure_code="invalid_framing_or_schema", error_message=f"{type(error).__name__}: invalid enrichment request",
                attempt_count=1, failed_at=datetime.now(timezone.utc),
            ),
            producer="enrichment-worker",
            correlation_id=f"dlq-{topic}-{message.partition()}-{message.offset()}",
            partition_key=key or "unknown",
            event_id=_dlq_event_id(topic, message.partition(), message.offset()),
        )

    def _dead_letter(self, request: EnrichmentRequestedEvent, error: Exception, attempts: int, *, message=None) -> DeadLetterEvent:
        payload_json = request.model_dump_json().encode("utf-8")
        return DeadLetterEvent.create(
            DeadLetterPayload(
                original_topic=ENRICHMENT_REQUESTED_TOPIC,
                original_key=request.partition_key,
                original_event_id=request.event_id,
                payload_sha256=hashlib.sha256(payload_json).hexdigest(),
                safe_payload={"event_id": request.event_id, "input_hash": request.payload.input_hash},
                consumer="enrichment-worker",
                failure_class=type(error).__name__,
                failure_code="retry_exhausted",
                error_message=f"{type(error).__name__}: hosted enrichment failed",
                attempt_count=attempts,
                failed_at=datetime.now(timezone.utc),
            ),
            producer="enrichment-worker",
            correlation_id=request.correlation_id,
            causation_id=request.event_id,
            partition_key=request.partition_key,
            event_id=_dlq_event_id(
                ENRICHMENT_REQUESTED_TOPIC,
                message.partition() if message is not None else 0,
                message.offset() if message is not None else request.event_id,
            ),
        )


# A small alias keeps integration code independent of the worker's name.
HostedEnrichmentWorker = EnrichmentWorker


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="B4 hosted enrichment worker")
    parser.add_argument("--replay", action="store_true", help="Read immutable artifacts only")
    parser.add_argument("--recorded-fixture", help="JSON provider fixture for offline runs")
    parser.add_argument("--reenrich", action="store_true", help="Explicitly request a fresh artifact write")
    args = parser.parse_args(argv)
    if args.replay and (args.recorded_fixture or args.reenrich):
        parser.error("--replay cannot be combined with --recorded-fixture or --reenrich")
    from config import get_settings
    from services.enrichment import GeminiProvider, GroqProvider, RecordedProvider, EnrichmentService
    settings = get_settings()
    if args.recorded_fixture:
        fixture = json.loads(open(args.recorded_fixture, encoding="utf-8").read())
        gemini = RecordedProvider(fixture, model=settings.GEMINI_MODEL)
        groq = RecordedProvider(fixture, provider="groq", model=settings.GROQ_MODEL)
    elif args.replay:
        class ReplayProvider:
            def __init__(self, provider: str, model: str) -> None:
                self.provider, self.model = provider, model
            def extract(self, text): raise RuntimeError("Replay mode forbids provider calls")
            def embed(self, text): raise RuntimeError("Replay mode forbids provider calls")
        gemini = ReplayProvider("gemini", settings.GEMINI_MODEL)
        # Replay never calls providers, but both configured identities must be
        # present so recorded Groq fallback artifacts are discoverable.
        groq = ReplayProvider("groq", settings.GROQ_MODEL)
    else:
        required = ("ENRICHMENT_REQUEST_BUDGET", "ENRICHMENT_SPEND_BUDGET_MICROS",
                    "ENRICHMENT_INPUT_PRICE_MICROS_PER_MILLION", "ENRICHMENT_OUTPUT_PRICE_MICROS_PER_MILLION",
                    "ENRICHMENT_EMBEDDING_PRICE_MICROS_PER_MILLION")
        if any(int(os.getenv(name, "0")) <= 0 for name in required):
            raise RuntimeError("Hosted worker requires explicit positive request/spend budgets and provider price ceilings")
        gemini = GeminiProvider(model=settings.GEMINI_MODEL)
        groq = GroqProvider(model=settings.GROQ_MODEL)
    target = settings.persistence_target
    ledger = EnrichmentBudgetLedger(target) if not args.recorded_fixture and not args.replay else None
    store = EnrichmentArtifactStore(target)
    service = EnrichmentService(gemini=gemini, groq=groq, artifact_store=store,
                                replay=args.replay, reenrich=args.reenrich,
                                pipeline_version=(os.getenv("ENRICHMENT_REENRICH_PIPELINE_VERSION", "b4-v1-reenrich")
                                                  if args.reenrich else "b4-v1"))
    from services.kafka_runtime import KafkaEventPublisher, SchemaRegistry, kafka_client_config, physical_topic
    from confluent_kafka import Consumer
    config = kafka_client_config(settings)
    config.update({"group.id": f"{settings.KAFKA_CONSUMER_GROUP_PREFIX}-enrichment-worker-v1",
                   "enable.auto.commit": False, "isolation.level": "read_committed", "auto.offset.reset": "earliest"})
    consumer = Consumer(config)
    worker = EnrichmentWorker(service, budget=ledger)
    stop = threading.Event()
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda *_: stop.set())
    worker.run_forever(consumer, KafkaEventPublisher(SchemaRegistry(), settings=settings), SchemaRegistry(),
                       topic=ENRICHMENT_REQUESTED_TOPIC,
                       subscription_topic=physical_topic(ENRICHMENT_REQUESTED_TOPIC, settings),
                       output_topic="documents.enriched.v1", stop=stop.is_set)


if __name__ == "__main__":
    main()
