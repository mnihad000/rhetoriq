"""Adapter around the shared B4 pure normalizer.

Keeping this boundary thin is intentional: normalization rules, hashes, and
quality flags must be identical for the hosted worker and the Flink replay.
"""

from __future__ import annotations

from typing import Any

from models.events import RawDocumentEvent
from services.document_normalization import normalize_raw_event as _normalize_raw_event


def normalize_raw_event(event: RawDocumentEvent | dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize one raw event, returning its request envelope."""
    if isinstance(event, dict):
        event = RawDocumentEvent.model_validate(event)
    return _normalize_raw_event(event).model_dump(mode="json")


__all__ = ["normalize_raw_event"]
