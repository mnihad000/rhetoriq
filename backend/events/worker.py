from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
import random
import re
import time
from typing import Any

from config import get_settings
from models.events import (
    DeadLetterEvent,
    DeadLetterPayload,
    PRIMARY_TOPICS,
    dlq_topic,
    validate_event,
)
from services.event_handlers import EventHandlers
from services.event_store import EventStore
from services.kafka_runtime import KafkaEventPublisher, SchemaRegistry, kafka_client_config, physical_topic
from services.kafka_runtime import SchemaRegistryError


logger = logging.getLogger(__name__)
_SECRET_PATTERN = re.compile(r"(?i)(password|secret|token|authorization|api[_-]?key)\s*[=:]\s*[^\s,;]+")
TOPICS_BY_ROLE = {
    "documents": ("raw.documents.v1", "documents.processed.v1"),
    "signals": ("signals.detected.v1",),
    "investigations": ("investigations.requested.v1",),
    "projections": ("investigations.stage-events.v1", "investigations.completed.v1"),
    "all": PRIMARY_TOPICS,
}


def _redact(message: str) -> str:
    return _SECRET_PATTERN.sub(r"\1=<redacted>", message)[:500]


def _is_permanent(error: Exception) -> bool:
    try:
        from jsonschema import ValidationError as JsonSchemaValidationError
        from pydantic import ValidationError as PydanticValidationError
        permanent_types = (SchemaRegistryError, JsonSchemaValidationError, PydanticValidationError, ValueError)
    except ImportError:  # pragma: no cover
        permanent_types = (SchemaRegistryError, ValueError)
    return isinstance(error, permanent_types)


class EventProcessor:
    def __init__(self, store: EventStore, handlers: EventHandlers) -> None:
        self.store = store
        self.handlers = handlers

    def process(
        self,
        topic: str,
        decoded: dict[str, Any],
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


class KafkaEventWorker:
    def __init__(self, role: str | None = None) -> None:
        settings = get_settings()
        self.settings = settings
        self.registry = SchemaRegistry()
        self.publisher = KafkaEventPublisher(self.registry)
        self.store = EventStore(settings.persistence_target)
        self.processor = EventProcessor(self.store, EventHandlers(settings.persistence_target))
        try:
            from confluent_kafka import Consumer
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Kafka support requires confluent-kafka") from exc
        config = kafka_client_config(settings)
        role = role or settings.KAFKA_WORKER_ROLE
        if role not in TOPICS_BY_ROLE:
            raise ValueError(f"Unknown KAFKA_WORKER_ROLE: {role}")
        config.update({
            "group.id": f"{settings.KAFKA_CONSUMER_GROUP_PREFIX}-{role}-worker-v1",
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
            "max.poll.interval.ms": 900000,
        })
        self.consumer = Consumer(config)
        self.consumer.subscribe([physical_topic(topic) for topic in TOPICS_BY_ROLE[role]])

    def run_forever(self) -> None:
        while True:
            message = self.consumer.poll(1.0)
            if message is None:
                continue
            if message.error():
                logger.error("Kafka consumer error: %s", message.error())
                continue
            topic = message.topic()
            logical = topic[len(self.settings.KAFKA_TOPIC_PREFIX):] if self.settings.KAFKA_TOPIC_PREFIX else topic
            last_error: Exception | None = None
            decoded: dict[str, Any] | None = None
            attempts = 0
            for attempt in range(1, self.settings.KAFKA_RETRY_MAX_ATTEMPTS + 1):
                attempts = attempt
                try:
                    decoded = self.registry.decode(logical, message.value())
                    self.processor.process(
                        logical,
                        decoded,
                        partition=message.partition(),
                        offset=message.offset(),
                    )
                    self.consumer.commit(message=message, asynchronous=False)
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    if _is_permanent(exc):
                        break
                    if attempt < self.settings.KAFKA_RETRY_MAX_ATTEMPTS:
                        delay = self.settings.KAFKA_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
                        time.sleep(delay * random.uniform(0.75, 1.25))
            if last_error is not None:
                self._dead_letter(message, logical, decoded, last_error, attempt_count=attempts)
                self.consumer.commit(message=message, asynchronous=False)

    def _dead_letter(
        self,
        message,
        topic: str,
        decoded: dict[str, Any] | None,
        error: Exception,
        *,
        attempt_count: int,
    ) -> None:
        raw = message.value() or b""
        original_event_id = decoded.get("event_id") if decoded else None
        correlation_id = decoded.get("correlation_id") if decoded else f"dlq-{topic}-{message.partition()}-{message.offset()}"
        safe_payload = None
        if decoded:
            safe_payload = {
                key: value for key, value in decoded.items()
                if key not in {"payload"}
            }
        event = DeadLetterEvent.create(
            DeadLetterPayload(
                original_topic=topic,
                original_partition=message.partition(),
                original_offset=message.offset(),
                original_key=message.key().decode("utf-8", errors="replace") if message.key() else None,
                original_event_id=original_event_id,
                payload_sha256=hashlib.sha256(raw).hexdigest(),
                safe_payload=safe_payload,
                consumer=f"{self.settings.KAFKA_CONSUMER_GROUP_PREFIX}-event-worker-v1",
                failure_class=type(error).__name__,
                failure_code="permanent_failure" if _is_permanent(error) else "retry_exhausted",
                error_message=_redact(str(error)),
                attempt_count=attempt_count,
                failed_at=datetime.now(timezone.utc),
            ),
            producer="event-worker",
            correlation_id=correlation_id,
            causation_id=original_event_id,
            partition_key=message.key().decode("utf-8", errors="replace") if message.key() else correlation_id,
        )
        # If this publish fails, the caller does not commit the source offset.
        self.publisher.publish(dlq_topic(topic), event)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    KafkaEventWorker().run_forever()


if __name__ == "__main__":
    main()
