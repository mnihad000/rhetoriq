from __future__ import annotations

import argparse
import logging
import httpx

from config import get_settings
from models.events import (
    DLQ_TOPICS,
    ENRICHED_DOCUMENTS_TOPIC,
    ENRICHMENT_REQUESTED_TOPIC,
    LATE_DOCUMENTS_TOPIC,
    PIPELINE_EVALUATED_TOPIC,
    PRIMARY_TOPICS,
)
from services.kafka_runtime import SchemaRegistry, kafka_client_config, physical_topic


RETENTION_MS = {
    "corpus.projections.v1": 90 * 24 * 60 * 60 * 1000,
    "investigation.projections.v1": 180 * 24 * 60 * 60 * 1000,
    "raw.documents.v1": 14 * 24 * 60 * 60 * 1000,
    ENRICHMENT_REQUESTED_TOPIC: 30 * 24 * 60 * 60 * 1000,
    ENRICHED_DOCUMENTS_TOPIC: 30 * 24 * 60 * 60 * 1000,
    LATE_DOCUMENTS_TOPIC: 30 * 24 * 60 * 60 * 1000,
    PIPELINE_EVALUATED_TOPIC: 90 * 24 * 60 * 60 * 1000,
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
        partitions = 6 if topic in {
            "raw.documents.v1", ENRICHMENT_REQUESTED_TOPIC, ENRICHED_DOCUMENTS_TOPIC,
            "documents.processed.v1", "investigations.stage-events.v1",
        } else 3
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
    # NewTopic configs are ignored by Kafka for topics that already exist;
    # explicitly converge retention during B3 -> B4 upgrades.
    try:
        from confluent_kafka.admin import AlterConfigOpType, ConfigEntry, ConfigResource
        resources = [
            ConfigResource(
                ConfigResource.Type.TOPIC,
                physical_topic(topic, settings),
                incremental_configs=[
                    ConfigEntry(name, value, incremental_operation=AlterConfigOpType.SET)
                    for name, value in (("retention.ms", str(RETENTION_MS[topic])), ("cleanup.policy", "delete"))
                ],
            )
            for topic in PRIMARY_TOPICS
        ]
        if hasattr(admin, "incremental_alter_configs"):
            for future in admin.incremental_alter_configs(resources).values():
                future.result()
    except (ImportError, AttributeError):  # pragma: no cover - old clients
        pass
    registry = SchemaRegistry()
    for topic in (*PRIMARY_TOPICS, *DLQ_TOPICS):
        registry.ensure_schema(topic)


def check_topics() -> None:
    """Fail without mutation unless every required topic and schema exists."""
    try:
        from confluent_kafka.admin import AdminClient
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise RuntimeError("Kafka support requires confluent-kafka") from exc
    settings = get_settings()
    metadata = AdminClient(kafka_client_config(settings)).list_topics(
        timeout=settings.KAFKA_REQUEST_TIMEOUT_SECONDS
    )
    required_topics = {physical_topic(topic, settings) for topic in (*PRIMARY_TOPICS, *DLQ_TOPICS)}
    missing_topics = sorted(required_topics - set(metadata.topics))
    if missing_topics:
        raise RuntimeError("Kafka topics are missing: " + ", ".join(missing_topics))
    with httpx.Client(timeout=settings.KAFKA_REQUEST_TIMEOUT_SECONDS) as client:
        response = client.get(f"{settings.KAFKA_SCHEMA_REGISTRY_URL.rstrip('/')}/subjects")
        response.raise_for_status()
        subjects = set(response.json())
    missing_subjects = sorted(f"{topic}-value" for topic in required_topics if f"{topic}-value" not in subjects)
    if missing_subjects:
        raise RuntimeError("Schema subjects are missing: " + ", ".join(missing_subjects))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Initialize or verify Kafka topics and schemas")
    parser.add_argument("--check", action="store_true", help="Verify without creating resources")
    args = parser.parse_args()
    check_topics() if args.check else bootstrap_topics()
