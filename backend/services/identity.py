"""Transport-independent canonical identities for evidence projections."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


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
