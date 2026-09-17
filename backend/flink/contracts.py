"""Versioned stream contracts and stable identity helpers.

These contracts are plain dictionaries on purpose.  The hosted enrichment
worker can evolve its Pydantic models independently, while the stream keeps
the fields required for lineage, replay, and idempotent projections stable.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

RAW_DOCUMENTS_TOPIC = "raw.documents.v1"
ENRICHMENT_REQUESTED_TOPIC = "documents.enrichment-requested.v1"
ENRICHED_DOCUMENTS_TOPIC = "documents.enriched.v1"
PROCESSED_DOCUMENTS_TOPIC = "documents.processed.v1"
SIGNALS_DETECTED_TOPIC = "signals.detected.v1"
LATE_DOCUMENTS_TOPIC = "documents.late.v1"
EVALUATION_HEARTBEATS_TOPIC = "pipeline.evaluated.v1"

ENRICHMENT_REQUESTED_DLQ_TOPIC = "documents.enrichment-requested.dlq.v1"
ENRICHED_DOCUMENTS_DLQ_TOPIC = "documents.enriched.dlq.v1"

B4_TOPICS = (
    RAW_DOCUMENTS_TOPIC,
    ENRICHMENT_REQUESTED_TOPIC,
    ENRICHED_DOCUMENTS_TOPIC,
    PROCESSED_DOCUMENTS_TOPIC,
    SIGNALS_DETECTED_TOPIC,
    LATE_DOCUMENTS_TOPIC,
    EVALUATION_HEARTBEATS_TOPIC,
)


def utc(value: datetime | str | None) -> datetime | None:
    """Parse a timestamp into UTC without changing absent values."""
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)


def semantic_hash(value: Any) -> str:
    """Hash canonical payload data (never generated timestamps)."""
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def stable_id(prefix: str, *parts: Any, length: int = 24) -> str:
    material = "|".join(canonical_json(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:length]}"


def envelope(
    *,
    event_type: str,
    event_id: str,
    payload: dict[str, Any],
    partition_key: str,
    occurred_at: datetime,
    correlation_id: str,
    causation_id: str | None = None,
    producer: str = "rhetoriq-flink-b4",
) -> dict[str, Any]:
    """Create a deterministic JSON event envelope.

    ``produced_at`` follows event time, making fixture replay byte-stable. A
    production adapter may replace this one field with wall-clock time.
    """
    occurred = utc(occurred_at) or datetime(1970, 1, 1, tzinfo=timezone.utc)
    produced = occurred.isoformat().replace("+00:00", "Z")
    return {
        "event_id": event_id,
        "event_type": event_type,
        "schema_version": 1,
        "occurred_at": occurred.isoformat().replace("+00:00", "Z"),
        "produced_at": produced,
        "producer": producer,
        "correlation_id": correlation_id,
        "causation_id": causation_id,
        "partition_key": partition_key,
        "payload": payload,
    }
