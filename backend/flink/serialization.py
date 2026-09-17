"""Confluent JSON framing adapters usable from PyFlink serialization APIs."""

from __future__ import annotations

import json
import struct
from typing import Any


class ConfluentJsonAdapter:
    """Encode/decode Apicurio Confluent-compatible JSON frames.

    For B3 topics, pass the existing ``services.kafka_runtime.SchemaRegistry``
    and its schema validation is reused. New B4 topics can use a schema id
    obtained during topic bootstrap; the adapter remains dependency-free for
    unit tests and PyFlink image startup.
    """

    def __init__(self, registry: Any | None = None, *, schema_ids: dict[str, int] | None = None) -> None:
        self.registry = registry
        self.schema_ids = dict(schema_ids or {})
        if any(int(value) <= 0 for value in self.schema_ids.values()):
            raise ValueError("B4 schema ids must be positive")

    def encode(self, topic: str, event: dict[str, Any]) -> bytes:
        if self.registry is not None:
            from models.events import validate_event

            # Registry.encode is authoritative for every registered B3/B4
            # contract. Validation failures are permanent and must reach DLQ.
            validated = validate_event(topic, event)
            return self.registry.encode(topic, validated)
        schema_id = self.schema_ids.get(topic)
        if schema_id is None:
            raise ValueError(f"No offline schema id configured for {topic}")
        body = json.dumps(event, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return b"\x00" + struct.pack(">I", schema_id) + body

    def decode(self, topic: str, frame: bytes | bytearray | memoryview) -> dict[str, Any]:
        data = bytes(frame)
        if len(data) < 5 or data[0] != 0:
            raise ValueError("Message does not use Confluent JSON Schema framing")
        schema_id = struct.unpack(">I", data[1:5])[0]
        expected = self.schema_ids.get(topic)
        if expected is not None and expected != schema_id:
            raise ValueError(f"Message schema id {schema_id} is incompatible with expected id {expected}")
        decoded = json.loads(data[5:].decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("Event frame must contain a JSON object")
        if self.registry is not None:
            return self.registry.decode(topic, data)
        if expected is None:
            raise ValueError(f"No offline schema id configured for {topic}")
        return decoded


class SerializationSchema:
    """Small PyFlink ``SerializationSchema`` compatible object."""

    def __init__(self, topic: str, adapter: ConfluentJsonAdapter | None = None) -> None:
        self.topic = topic
        self.adapter = adapter or ConfluentJsonAdapter()

    def serialize(self, value: dict[str, Any]) -> bytes:
        return self.adapter.encode(self.topic, value)


class DeserializationSchema:
    """Small PyFlink ``DeserializationSchema`` compatible object."""

    def __init__(self, topic: str, adapter: ConfluentJsonAdapter | None = None) -> None:
        self.topic = topic
        self.adapter = adapter or ConfluentJsonAdapter()

    def deserialize(self, message: bytes) -> dict[str, Any]:
        return self.adapter.decode(self.topic, message)
