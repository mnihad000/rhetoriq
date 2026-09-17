"""Operator-only Kafka delivery probe for a disposable recorded-provider stack.

This runs the real topology; it does not replace the offline fixture tests.
Use a separate Compose project and database, never a production broker.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import threading
import time
from uuid import uuid4

from config import get_settings
from models.document import Document
from services.event_factory import raw_event_from_document
from services.kafka_runtime import KafkaEventPublisher, SchemaRegistry, kafka_client_config, physical_topic


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("smoke", "load"), default="smoke")
    parser.add_argument("--drain-seconds", type=int, default=240)
    parser.add_argument("--disposable-stack", action="store_true", required=True,
                        help="Acknowledge that configured broker/database are disposable test targets")
    args = parser.parse_args()
    from confluent_kafka import Consumer

    settings = get_settings()
    registry = SchemaRegistry()
    publisher = KafkaEventPublisher(registry, settings=settings)
    run_id = "b4_acceptance_" + uuid4().hex
    consumer = Consumer({**kafka_client_config(settings), "group.id": run_id,
                         "auto.offset.reset": "earliest", "enable.auto.commit": False,
                         "isolation.level": "read_committed"})
    topics = ["documents.processed.v1", "signals.detected.v1"]
    consumer.subscribe([physical_topic(topic, settings) for topic in topics])
    stop = threading.Event()
    received: dict[str, str] = {}
    failures: list[str] = []
    first_signal_seconds: list[float] = []
    signal_support: list[set[str]] = []
    published_documents: set[str] = set()
    started = time.monotonic()

    def collect() -> None:
        try:
            while not stop.is_set():
                message = consumer.poll(0.5)
                if message is None:
                    continue
                if message.error():
                    raise RuntimeError(str(message.error()))
                topic = next(topic for topic in topics if physical_topic(topic, settings) == message.topic())
                event = registry.decode(topic, message.value())
                payload = event["payload"]
                if topic == "signals.detected.v1":
                    supporting = set(payload.get("supporting_document_ids") or [])
                    if supporting & published_documents:
                        first_signal_seconds.append(time.monotonic() - started)
                        signal_support.append(supporting)
                    continue
                if event.get("correlation_id") != run_id:
                    continue
                document_id = payload["document"]["id"]
                digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                if document_id in received and received[document_id] != digest:
                    raise AssertionError("Recovery changed a processed document's semantic payload")
                received[document_id] = digest
        except Exception as exc:
            failures.append(f"{type(exc).__name__}: {exc}")

    collector = threading.Thread(target=collect, daemon=True)
    collector.start()
    published = 0
    try:
        # The full acceptance pace is 100/min for 30 min, then 500/min for 5 min.
        phases = [(20, 20)] if args.mode == "smoke" else [(3000, 100), (2500, 500)]
        for count, rate in phases:
            phase_started = time.monotonic()
            for index in range(count):
                due = phase_started + index * 60 / rate
                time.sleep(max(0, due - time.monotonic()))
                now = datetime.now(timezone.utc)
                domain = f"publisher{published % 4}.example"
                text = f"Public records policy changes today. Acceptance record {run_id} {published}."
                document = Document(
                    id=f"{run_id}_{published}", source_name=domain,
                    source_type="government_record" if published % 2 else "national_news",
                    url=f"https://{domain}/{run_id}/{published}", title="Public records policy",
                    text=text, snippet=text, published_at=now, collected_at=now,
                    entities=[], phrases=[], language="en", content_type="article",
                    metadata={"provider": "federal_register" if published % 2 else "searxng"},
                )
                event = raw_event_from_document(document, producer="b4-acceptance", correlation_id=run_id)
                # Normalization assigns its own stable document ID; calculate
                # that ID through the same pure boundary used by the job.
                from services.document_normalization import normalize_raw_event
                published_documents.add(normalize_raw_event(event).payload.document.id)
                publisher.publish("raw.documents.v1", event)
                published += 1
                if failures:
                    raise RuntimeError(failures[0])
        deadline = time.monotonic() + args.drain_seconds
        while len(received) < published and time.monotonic() < deadline and not failures:
            time.sleep(0.5)
    finally:
        stop.set()
        collector.join(timeout=5)
        consumer.close()
    result = {"run_id": run_id, "mode": args.mode, "published": published,
              "unique_processed": len(received), "elapsed_seconds": round(time.monotonic() - started, 2),
              "first_signal_seconds": round(min(first_signal_seconds), 2) if first_signal_seconds else None,
              "failures": failures}
    delivery_ok = not failures and len(received) == published
    signal_ok = args.mode == "smoke" or bool(first_signal_seconds and min(first_signal_seconds) <= 1200)
    result["passed"] = delivery_ok and signal_ok
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
