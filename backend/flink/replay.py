"""Offline deterministic replay helpers for B4 fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .stream_engine import EngineOutput, StreamIntelligenceEngine


def replay_events(events: Iterable[dict[str, Any]], *, engine: StreamIntelligenceEngine | None = None) -> list[tuple[str, dict[str, Any]]]:
    processor = engine or StreamIntelligenceEngine()
    output: list[tuple[str, dict[str, Any]]] = []
    for event in events:
        output.extend(processor.process(event).all_events())
    return output


def replay_jsonl(path: str | Path, *, engine: StreamIntelligenceEngine | None = None) -> list[tuple[str, dict[str, Any]]]:
    source = Path(path)
    events = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    return replay_events(events, engine=engine)


def dump_jsonl(events: Iterable[tuple[str, dict[str, Any]]], path: str | Path) -> None:
    target = Path(path)
    target.write_text("\n".join(json.dumps({"topic": topic, "event": event}, sort_keys=True, separators=(",", ":")) for topic, event in events) + "\n", encoding="utf-8")
