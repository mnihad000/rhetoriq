from __future__ import annotations

import argparse
import logging
from uuid import uuid4

from config import get_settings
from models.events import PRIMARY_TOPICS
from services.event_handlers import EventHandlers
from services.event_store import EventStore
from services.kafka_runtime import SchemaRegistry, kafka_client_config, physical_topic
from events.worker import EventProcessor


def replay(
    topic: str,
    *,
    partition: int,
    start_offset: int,
    end_offset: int | None,
    correlation_id: str | None,
    dry_run: bool,
) -> dict[str, int | str]:
    if topic not in PRIMARY_TOPICS:
        raise ValueError(f"Replay supports versioned primary topics only: {topic}")
    settings = get_settings()
    replay_job_id = f"replay_{uuid4().hex}"
    store = EventStore(settings.persistence_target)
    store.create_replay_job(
        replay_job_id,
        topic,
        correlation_id=correlation_id,
        partition=partition,
        start_offset=start_offset,
        end_offset=end_offset,
    )
    processed = skipped = 0
    error: str | None = None
    try:
        from confluent_kafka import Consumer, TopicPartition
        config = kafka_client_config(settings)
        config.update({
            "group.id": f"{settings.KAFKA_CONSUMER_GROUP_PREFIX}-{replay_job_id}",
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
        })
        consumer = Consumer(config)
        consumer.assign([TopicPartition(physical_topic(topic, settings), partition, start_offset)])
        registry = SchemaRegistry()
        processor = EventProcessor(store, EventHandlers(settings.persistence_target))
        idle_polls = 0
        while idle_polls < 3:
            message = consumer.poll(1.0)
            if message is None:
                idle_polls += 1
                continue
            idle_polls = 0
            if message.error():
                raise RuntimeError(str(message.error()))
            if end_offset is not None and message.offset() > end_offset:
                break
            decoded = registry.decode(topic, message.value())
            if correlation_id and decoded.get("correlation_id") != correlation_id:
                skipped += 1
                continue
            if dry_run:
                skipped += 1
                continue
            if processor.process(
                topic,
                decoded,
                partition=message.partition(),
                offset=message.offset(),
                replay_namespace=replay_job_id,
            ):
                processed += 1
            else:
                skipped += 1
        consumer.close()
    except Exception as exc:
        error = str(exc)
        raise
    finally:
        store.finish_replay_job(
            replay_job_id,
            processed_count=processed,
            skipped_count=skipped,
            error=error,
        )
    return {"replay_job_id": replay_job_id, "processed": processed, "skipped": skipped}


def main() -> None:
    parser = argparse.ArgumentParser(description="Controlled, audited Kafka event replay")
    parser.add_argument("--topic", required=True, choices=PRIMARY_TOPICS)
    parser.add_argument("--partition", required=True, type=int)
    parser.add_argument("--start-offset", required=True, type=int)
    parser.add_argument("--end-offset", type=int)
    parser.add_argument("--correlation-id")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = replay(
        args.topic,
        partition=args.partition,
        start_offset=args.start_offset,
        end_offset=args.end_offset,
        correlation_id=args.correlation_id,
        dry_run=args.dry_run,
    )
    print(result)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
