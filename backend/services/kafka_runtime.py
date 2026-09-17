from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import struct
import threading
import time
from typing import Any, Callable

import httpx
from jsonschema import Draft202012Validator

from config import Settings, get_settings
from models.events import DLQ_TOPICS, EventEnvelope, event_schema
from services.event_store import EventStore, OutboxRecord


logger = logging.getLogger(__name__)

_TOPICS_BY_CONSUMER_ROLE = {
    "b5-elasticsearch": ("corpus.projections.v1",),
    "b5-neo4j": ("corpus.projections.v1", "investigation.projections.v1"),
    "b5-minilm": ("corpus.projections.v1",),
    "documents": ("documents.processed.v1",),
    "enrichment": ("documents.enrichment-requested.v1",),
    "signals": ("signals.detected.v1",),
    "investigations": ("investigations.requested.v1",),
    "projections": ("investigations.stage-events.v1", "investigations.completed.v1",
                    "pipeline.evaluated.v1", "documents.late.v1"),
}


def physical_topic(topic: str, settings: Settings | None = None) -> str:
    prefix = (settings or get_settings()).KAFKA_TOPIC_PREFIX.strip()
    return f"{prefix}{topic}" if prefix else topic


def logical_topic(topic: str, settings: Settings | None = None) -> str:
    prefix = (settings or get_settings()).KAFKA_TOPIC_PREFIX.strip()
    return topic[len(prefix):] if prefix and topic.startswith(prefix) else topic


def kafka_client_config(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    config: dict[str, Any] = {
        "bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS,
        "client.id": settings.KAFKA_CLIENT_ID,
        "security.protocol": settings.KAFKA_SECURITY_PROTOCOL,
        "socket.timeout.ms": int(settings.KAFKA_REQUEST_TIMEOUT_SECONDS * 1000),
    }
    if settings.KAFKA_SASL_USERNAME:
        config.update({
            "sasl.mechanism": settings.KAFKA_SASL_MECHANISM,
            "sasl.username": settings.KAFKA_SASL_USERNAME,
            "sasl.password": settings.KAFKA_SASL_PASSWORD,
        })
    return config


def kafka_consumer_health(settings: Settings | None = None) -> dict[str, Any]:
    """Return broker-backed group status, aggregate lag, and DLQ depth."""
    settings = settings or get_settings()
    try:
        from confluent_kafka import Consumer, TopicPartition
        from confluent_kafka.admin import AdminClient

        timeout = settings.KAFKA_REQUEST_TIMEOUT_SECONDS
        base = kafka_client_config(settings)
        listed = AdminClient(base).list_consumer_groups(request_timeout=timeout).result(timeout=timeout)
        active = {item.group_id: str(item.state).split(".")[-1].lower() for item in listed.valid}
        groups: list[dict[str, Any]] = []
        total_lag = 0
        all_ready = True
        for role, topics in _TOPICS_BY_CONSUMER_ROLE.items():
            if role.startswith("b5-") and not settings.ENABLE_B5_RETRIEVAL:
                continue
            group_id = f"{settings.KAFKA_CONSUMER_GROUP_PREFIX}-{role}-worker-v1"
            consumer = Consumer({**base, "group.id": group_id, "enable.auto.commit": False})
            role_lag = 0
            try:
                partitions = []
                for topic in topics:
                    physical = physical_topic(topic, settings)
                    metadata = consumer.list_topics(physical, timeout=timeout)
                    topic_metadata = metadata.topics.get(physical)
                    if topic_metadata is None or topic_metadata.error is not None:
                        raise RuntimeError(f"Topic metadata unavailable for {physical}")
                    partitions.extend(TopicPartition(physical, partition_id) for partition_id in topic_metadata.partitions)
                committed = consumer.committed(partitions, timeout=timeout)
                for partition in committed:
                    low, high = consumer.get_watermark_offsets(partition, timeout=timeout)
                    current = partition.offset if partition.offset >= 0 else low
                    role_lag += max(0, high - current)
            finally:
                consumer.close()
            state = active.get(group_id, "missing")
            ready = state in {"stable", "empty"}
            all_ready = all_ready and ready
            total_lag += role_lag
            groups.append({"group_id": group_id, "role": role, "state": state, "lag": role_lag})

        inspector = Consumer({
            **base,
            "group.id": f"{settings.KAFKA_CONSUMER_GROUP_PREFIX}-health-inspector-v1",
            "enable.auto.commit": False,
        })
        dlq_counts: dict[str, int] = {}
        try:
            for topic in DLQ_TOPICS:
                physical = physical_topic(topic, settings)
                metadata = inspector.list_topics(physical, timeout=timeout)
                topic_metadata = metadata.topics.get(physical)
                if topic_metadata is None or topic_metadata.error is not None:
                    raise RuntimeError(f"Topic metadata unavailable for {physical}")
                count = 0
                for partition_id in topic_metadata.partitions:
                    low, high = inspector.get_watermark_offsets(
                        TopicPartition(physical, partition_id), timeout=timeout
                    )
                    count += max(0, high - low)
                dlq_counts[topic] = count
        finally:
            inspector.close()
        return {
            "status": "ready" if all_ready else "unavailable",
            "groups": groups,
            "lag": total_lag,
            "dlq_counts": dlq_counts,
            "dlq_total": sum(dlq_counts.values()),
            "list_errors": [str(error)[:180] for error in listed.errors],
        }
    except Exception as exc:
        return {
            "status": "unavailable",
            "detail": str(exc)[:180],
            "groups": [],
            "lag": None,
            "dlq_counts": {},
            "dlq_total": None,
        }


class SchemaRegistryError(RuntimeError):
    pass


class SchemaRegistry:
    """Small Confluent-compatible JSON Schema registry client for Apicurio."""

    def __init__(self, base_url: str | None = None, *, transport: httpx.BaseTransport | None = None) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.KAFKA_SCHEMA_REGISTRY_URL).rstrip("/")
        self.timeout = settings.KAFKA_REQUEST_TIMEOUT_SECONDS
        self.transport = transport
        self._schema_ids: dict[str, int] = {}
        self._writer_schemas: dict[tuple[str, int], dict[str, Any]] = {}
        self._lock = threading.Lock()

    def ensure_schema(self, topic: str) -> int:
        subject = f"{physical_topic(topic)}-value"
        with self._lock:
            if subject in self._schema_ids:
                return self._schema_ids[subject]
            schema = event_schema(topic)
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                compatibility = client.put(
                    f"{self.base_url}/config/{subject}",
                    json={"compatibility": "BACKWARD_TRANSITIVE"},
                )
                if compatibility.status_code not in {200, 201, 204, 404}:
                    raise SchemaRegistryError(
                        f"Could not configure compatibility for {subject}: HTTP {compatibility.status_code}"
                    )
                response = client.post(
                    f"{self.base_url}/subjects/{subject}/versions",
                    json={"schemaType": "JSON", "schema": json.dumps(schema, sort_keys=True)},
                )
                if compatibility.status_code == 404 and response.status_code in {200, 201}:
                    compatibility = client.put(
                        f"{self.base_url}/config/{subject}",
                        json={"compatibility": "BACKWARD_TRANSITIVE"},
                    )
                    if compatibility.status_code not in {200, 201, 204}:
                        raise SchemaRegistryError(
                            f"Could not configure compatibility for {subject}: HTTP {compatibility.status_code}"
                        )
            if response.status_code not in {200, 201}:
                detail = response.text[:300]
                raise SchemaRegistryError(f"Could not register {subject}: HTTP {response.status_code}: {detail}")
            schema_id = int(response.json()["id"])
            self._schema_ids[subject] = schema_id
            return schema_id

    def encode(self, topic: str, event: EventEnvelope) -> bytes:
        schema = event_schema(topic)
        value = event.model_dump(mode="json")
        Draft202012Validator(schema).validate(value)
        schema_id = self.ensure_schema(topic)
        return b"\x00" + struct.pack(">I", schema_id) + json.dumps(
            value, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")

    def decode(self, topic: str, value: bytes) -> dict[str, Any]:
        if len(value) < 6 or value[0] != 0:
            raise SchemaRegistryError("Message does not use Confluent JSON Schema framing")
        actual_id = struct.unpack(">I", value[1:5])[0]
        expected_id = self.ensure_schema(topic)
        if actual_id != expected_id:
            # Backward-transitive registration does not change older messages'
            # framing IDs. Require membership in this subject, then validate
            # both the original writer schema and our current reader contract.
            cache_key = (topic, actual_id)
            if cache_key not in self._writer_schemas:
                subject = f"{physical_topic(topic)}-value"
                with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                    versions = client.get(f"{self.base_url}/schemas/ids/{actual_id}/versions")
                    versions.raise_for_status()
                    if not any(item.get("subject") == subject for item in versions.json()):
                        raise SchemaRegistryError("Message schema is not registered for this topic")
                    writer = client.get(f"{self.base_url}/schemas/ids/{actual_id}")
                    writer.raise_for_status()
                    self._writer_schemas[cache_key] = json.loads(writer.json()["schema"])
        decoded = json.loads(value[5:])
        if actual_id != expected_id:
            Draft202012Validator(self._writer_schemas[(topic, actual_id)]).validate(decoded)
        Draft202012Validator(event_schema(topic)).validate(decoded)
        return decoded

    def health(self) -> dict[str, Any]:
        try:
            response = httpx.get(f"{self.base_url}/subjects", timeout=self.timeout)
            response.raise_for_status()
            return {"status": "ready", "subject_count": len(response.json())}
        except Exception as exc:
            return {"status": "unavailable", "detail": str(exc)[:180]}


class KafkaEventPublisher:
    def __init__(self, registry: SchemaRegistry | None = None, *, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.registry = registry or SchemaRegistry()
        try:
            from confluent_kafka import Producer
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError("Kafka support requires confluent-kafka") from exc
        config = kafka_client_config(self.settings)
        config.update({
            "enable.idempotence": True,
            "acks": "all",
            "retries": self.settings.KAFKA_RETRY_MAX_ATTEMPTS,
        })
        self._producer = Producer(config)

    def publish(self, topic: str, event: EventEnvelope) -> None:
        error: list[Exception] = []

        def delivered(err, _message) -> None:
            if err is not None:
                error.append(RuntimeError(str(err)))

        self._producer.produce(
            physical_topic(topic, self.settings),
            key=event.partition_key.encode("utf-8"),
            value=self.registry.encode(topic, event),
            headers={
                "event_id": event.event_id,
                "correlation_id": event.correlation_id,
                "event_type": event.event_type,
            },
            on_delivery=delivered,
        )
        remaining = self._producer.flush(self.settings.KAFKA_REQUEST_TIMEOUT_SECONDS)
        if remaining:
            raise TimeoutError(f"Kafka publish timed out with {remaining} message(s) pending")
        if error:
            raise error[0]

    def health(self) -> dict[str, Any]:
        try:
            metadata = self._producer.list_topics(timeout=self.settings.KAFKA_REQUEST_TIMEOUT_SECONDS)
            return {"status": "ready", "broker_count": len(metadata.brokers)}
        except Exception as exc:
            return {"status": "unavailable", "detail": str(exc)[:180]}


class InMemoryEventPublisher:
    """Unit-test transport only; never selected by application configuration."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, EventEnvelope]] = []

    def publish(self, topic: str, event: EventEnvelope) -> None:
        Draft202012Validator(event_schema(topic)).validate(event.model_dump(mode="json"))
        self.messages.append((topic, event))


class OutboxPublisher:
    def __init__(
        self,
        store: EventStore,
        publisher: KafkaEventPublisher | InMemoryEventPublisher,
        *,
        settings: Settings | None = None,
    ) -> None:
        self.store = store
        self.publisher = publisher
        self.settings = settings or get_settings()

    def publish_batch(self) -> int:
        published = 0
        for record in self.store.pending(self.settings.KAFKA_OUTBOX_BATCH_SIZE):
            try:
                event = EventEnvelope.model_validate_json(record.event_json)
                self.publisher.publish(record.topic, event)
                self.store.mark_published(record.event_id)
                published += 1
            except Exception as exc:
                delay = self.settings.KAFKA_RETRY_BACKOFF_SECONDS * (2 ** min(record.attempts, 6))
                self.store.mark_publish_failed(record.event_id, str(exc), backoff_seconds=delay)
                logger.warning("Outbox publish failed for %s: %s", record.event_id, exc)
        return published

    def run_forever(self, stop: Callable[[], bool] | None = None) -> None:
        stop = stop or (lambda: False)
        while not stop():
            count = self.publish_batch()
            if not count:
                time.sleep(self.settings.KAFKA_OUTBOX_POLL_SECONDS)


def publish_outbox_main() -> None:
    settings = get_settings()
    store = EventStore(settings.persistence_target)
    OutboxPublisher(store, KafkaEventPublisher()).run_forever()


if __name__ == "__main__":
    publish_outbox_main()
