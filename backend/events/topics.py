from __future__ import annotations

import logging

from config import get_settings
from models.events import DLQ_TOPICS, PRIMARY_TOPICS
from services.kafka_runtime import SchemaRegistry, kafka_client_config, physical_topic


RETENTION_MS = {
    "raw.documents.v1": 7 * 24 * 60 * 60 * 1000,
    "documents.processed.v1": 30 * 24 * 60 * 60 * 1000,
    "signals.detected.v1": 90 * 24 * 60 * 60 * 1000,
    "investigations.requested.v1": 90 * 24 * 60 * 60 * 1000,
    "investigations.stage-events.v1": 90 * 24 * 60 * 60 * 1000,
    "investigations.completed.v1": 180 * 24 * 60 * 60 * 1000,
}


def bootstrap_topics() -> None:
    try:
        from confluent_kafka.admin import AdminClient, NewTopic
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Kafka support requires confluent-kafka") from exc
    settings = get_settings()
    admin = AdminClient(kafka_client_config(settings))
    definitions = []
    for topic in PRIMARY_TOPICS:
        partitions = 6 if topic in {"raw.documents.v1", "documents.processed.v1", "investigations.stage-events.v1"} else 3
        definitions.append(NewTopic(
            physical_topic(topic, settings),
            num_partitions=partitions,
            replication_factor=1,
            config={"retention.ms": str(RETENTION_MS[topic]), "cleanup.policy": "delete"},
        ))
    for topic in DLQ_TOPICS:
        definitions.append(NewTopic(
            physical_topic(topic, settings),
            num_partitions=3,
            replication_factor=1,
            config={"retention.ms": str(14 * 24 * 60 * 60 * 1000), "cleanup.policy": "delete"},
        ))
    for topic, future in admin.create_topics(definitions).items():
        try:
            future.result()
            logging.info("Created Kafka topic %s", topic)
        except Exception as exc:
            if "TOPIC_ALREADY_EXISTS" not in str(exc):
                raise
    registry = SchemaRegistry()
    for topic in (*PRIMARY_TOPICS, *DLQ_TOPICS):
        registry.ensure_schema(topic)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    bootstrap_topics()
