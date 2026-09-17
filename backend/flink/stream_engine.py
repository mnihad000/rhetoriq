"""Deterministic event-time windows and narrative signal lifecycle."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any
from urllib.parse import urlsplit

from services.narrative_scoring import narrative_confidence
from services.enrichment import validate_artifact

from .contracts import (
    ENRICHED_DOCUMENTS_TOPIC,
    EVALUATION_HEARTBEATS_TOPIC,
    LATE_DOCUMENTS_TOPIC,
    PROCESSED_DOCUMENTS_TOPIC,
    SIGNALS_DETECTED_TOPIC,
    envelope,
    semantic_hash,
    stable_id,
    utc,
)
from .normalization import normalize_raw_event


@dataclass(frozen=True)
class EngineConfig:
    watermark_minutes: int = 15
    idle_partition_minutes: int = 1
    allowed_lateness_hours: int = 24
    dedup_days: int = 14
    baseline_days: int = 7
    emerging_hours: int = 6
    emerging_interval_minutes: int = 15
    sustained_hours: int = 24
    sustained_interval_minutes: int = 60
    min_documents: int = 4
    min_publishers: int = 3
    min_source_types: int = 2
    min_spike: float = 2.0
    min_confidence: float = 0.65


@dataclass
class EngineOutput:
    enrichment_requests: list[dict[str, Any]] = field(default_factory=list)
    processed: list[dict[str, Any]] = field(default_factory=list)
    signals: list[dict[str, Any]] = field(default_factory=list)
    late: list[dict[str, Any]] = field(default_factory=list)
    heartbeats: list[dict[str, Any]] = field(default_factory=list)

    def all_events(self) -> list[tuple[str, dict[str, Any]]]:
        return (
            [("documents.enrichment-requested.v1", item) for item in self.enrichment_requests]
            + [("documents.processed.v1", item) for item in self.processed]
            + [("signals.detected.v1", item) for item in self.signals]
            + [("documents.late.v1", item) for item in self.late]
            + [("pipeline.evaluated.v1", item) for item in self.heartbeats]
        )


class StreamIntelligenceEngine:
    """A bounded keyed-state engine shared by Flink operators and replay tests."""

    def __init__(self, config: EngineConfig | None = None, *, phrase_key: str | None = None) -> None:
        self.config = config or EngineConfig()
        self.phrase_key = phrase_key.strip().lower() if phrase_key is not None else None
        self._documents: dict[str, dict[str, Any]] = {}
        self._content_keys: dict[str, tuple[str, datetime]] = {}
        self._seen_events: dict[str, datetime] = {}
        self._max_event_time: datetime | None = None
        self._fingerprints: dict[str, str] = {}
        self._signal_revisions: dict[str, int] = defaultdict(int)
        self._horizon_metrics: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        self._window_metrics: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._current_buckets: dict[tuple[str, str], str] = {}
        self._window_active: dict[tuple[str, str, str], bool] = {}
        self._signal_active: dict[str, bool] = defaultdict(bool)
        self._heartbeat_keys: dict[str, datetime] = {}
        self._active_horizons: dict[str, dict[str, bool]] = defaultdict(dict)

    @property
    def watermark(self) -> datetime | None:
        if self._max_event_time is None:
            return None
        return self._max_event_time - timedelta(minutes=self.config.watermark_minutes)

    def process(self, event: Any, *, now: datetime | None = None, watermark: datetime | None = None) -> EngineOutput:
        raw = event.model_dump(mode="json") if hasattr(event, "model_dump") else dict(event)
        event_id = str(raw.get("event_id") or "")
        if event_id and event_id in self._seen_events:
            return EngineOutput()
        event_type = str(raw.get("event_type") or "")
        payload = raw.get("payload") or {}
        is_raw = event_type in {"raw.document.collected", "document.raw"} or (
            not event_type and bool(payload.get("provider")) and not payload.get("document")
        )
        if is_raw:
            if event_id:
                self._seen_events[event_id] = utc(raw.get("occurred_at")) or datetime.now(timezone.utc)
            request = normalize_raw_event(raw)
            return EngineOutput(enrichment_requests=[request])
        if event_type not in {"document.enriched", "document.enrichment_completed", "documents.enriched"} and ENRICHED_DOCUMENTS_TOPIC not in event_type:
            # Tests and replay tools may pass an enrichment payload without an
            # event_type; requiring the enriched document fields is sufficient.
            if not (payload.get("document") or payload.get("enriched_document")):
                raise ValueError(f"Unsupported stream event type: {event_type or '<missing>'}")
        return self.process_enriched(raw, now=now, watermark=watermark)

    def process_enriched(self, event: dict[str, Any], *, now: datetime | None = None, watermark: datetime | None = None) -> EngineOutput:
        if event.get("event_id") in self._seen_events:
            return EngineOutput()
        now_utc = utc(now) or datetime.now(timezone.utc)
        # Validate before touching keyed state: a partial provider response is
        # a permanent contract failure and must be sent to the enrichment DLQ.
        from models.events import EnrichedDocumentEvent

        validated = EnrichedDocumentEvent.model_validate(event)
        payload = validated.payload.model_dump(mode="json")
        normalized = payload["document"]
        artifact = payload["enrichment"]
        if payload.get("semantic_output_hash") != artifact.get("output_hash"):
            raise ValueError("Enriched event semantic hash does not match its receipt")
        if payload.get("input_hash") != artifact.get("input_hash"):
            raise ValueError("Enriched event input hash does not match its receipt")
        if artifact.get("failure_state"):
            raise ValueError("Failed enrichment artifacts cannot enter stream intelligence")
        text = str(normalized.get("text") or "")
        expected_exact = (normalized.get("metadata") or {}).get("exact_content_hash")
        actual_exact = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if not expected_exact or str(expected_exact) != actual_exact:
            raise ValueError("Enriched document exact content hash does not match normalized text")
        from models.events import EnrichmentReceipt
        validate_artifact(EnrichmentReceipt.model_validate(artifact), text)
        from services.document_normalization import _input_hash
        if _input_hash(validated.payload.document) != payload["input_hash"]:
            raise ValueError("Enriched document normalization input integrity failed")
        if normalized.get("phrases") != [item["phrase"] for item in artifact["canonical_phrases"]] or normalized.get("entities") != [item["value"] for item in artifact["entities"]]:
            raise ValueError("Enriched document mentions differ from its validated artifact")
        event_id = str(event.get("event_id") or "")
        if event_id:
            self._seen_events[event_id] = utc(event.get("occurred_at")) or datetime.now(timezone.utc)
        document = dict(normalized)
        document.setdefault("text", document.get("snippet") or "")
        document.setdefault("phrases", [item["phrase"] for item in artifact.get("canonical_phrases", [])])
        document.setdefault("entities", [item["value"] for item in artifact.get("entities", [])])
        document.setdefault("metadata", {})
        document["metadata"] = dict(document["metadata"] or {})
        document["metadata"].update({"enrichment_artifact_hash": artifact["output_hash"]})
        normalized_result = {
            "document": document,
            "quality_flags": list(payload.get("quality_flags") or []),
            "event_time_quality": document["metadata"].get("event_time_quality") or "published_at",
            "content_hash": document["metadata"].get("exact_content_hash") or semantic_hash({"text": document.get("text")}),
        }
        doc_id = str(document["id"])
        event_time = utc(document.get("published_at")) or utc(document.get("collected_at"))
        if event_time is None:
            raise ValueError("Enriched document requires a deterministic timestamp")
        quality = str(payload.get("event_time_quality") or (document.get("metadata") or {}).get("event_time_quality") or normalized_result["event_time_quality"])
        if self._max_event_time is None or event_time > self._max_event_time:
            self._max_event_time = event_time
        watermark = utc(watermark) or self.watermark or event_time
        too_late = event_time < watermark - timedelta(hours=self.config.allowed_lateness_hours)
        duplicate_of: str | None = None
        digest = actual_exact
        self._prune(self._max_event_time or event_time)
        previous = self._content_keys.get(digest)
        if previous is not None and event_time - previous[1] <= timedelta(days=self.config.dedup_days):
            duplicate_of = previous[0]
        elif not too_late:
            self._content_keys[digest] = (doc_id, event_time)
            self._documents[doc_id] = {
                "document": document,
                "event_time": event_time,
                "quality": quality,
                "quality_flags": list(dict.fromkeys(payload.get("quality_flags") or normalized_result["quality_flags"])),
                "content_hash": digest,
                "provider": str((document.get("metadata") or {}).get("provider") or "unknown"),
                "publisher": self._publisher(document),
                "source_type": str(document.get("source_type") or "unknown"),
            }
        document["duplicate_of_doc_id"] = duplicate_of
        processing = {
            "normalizer_version": "b4-normalizer-v1",
            "duplicate_of_document_id": duplicate_of,
            "quality_flags": list(normalized_result["quality_flags"]),
            "raw_event_id": str(payload.get("raw_event_id") or event.get("causation_id") or event.get("event_id") or doc_id),
        }
        artifact_hash = str(artifact["output_hash"])
        document["metadata"]["enrichment_artifact_hash"] = artifact_hash
        processed_payload = {
            "document": document,
            "processing": processing,
            "semantic_output_hash": None,
            "enrichment": artifact,
            "investigation_id": payload.get("investigation_id"),
            "run_id": payload.get("run_id"),
            "action_id": payload.get("action_id"),
        }
        from models.events import ProcessedDocumentPayload

        hashable_payload = ProcessedDocumentPayload.model_validate(processed_payload).model_dump(mode="json")
        hashable_payload.pop("semantic_output_hash", None)
        processed_payload["semantic_output_hash"] = semantic_hash(hashable_payload)
        # The event id includes lineage and artifact identity so a duplicate
        # marker or a re-enrichment cannot collide with an older output.
        processed_event_id = stable_id("processed", processing["raw_event_id"], artifact["artifact_id"], artifact_hash, duplicate_of or "canonical")
        processed = envelope(
            event_type="document.processed",
            event_id=processed_event_id,
            payload=processed_payload,
            partition_key=doc_id,
            occurred_at=event_time,
            correlation_id=str(event.get("correlation_id") or doc_id),
            causation_id=event.get("event_id"),
        )
        output = EngineOutput(processed=[processed])
        if too_late:
            # A beyond-lateness record belongs exclusively to the audit stream;
            # current signal state and processed projections are untouched.
            output.processed.clear()
            output.late.append(self._late_event(event, document, event_time, watermark, str(payload.get("input_hash") or digest)))
            return output
        phrases = {str(p).strip().lower() for item in self._documents.values() for p in item["document"].get("phrases", []) if str(p).strip()}
        if duplicate_of is None:
            phrases.update(str(item).strip().lower() for item in document.get("phrases", []) if str(item).strip())
        if self.phrase_key is not None:
            phrases = {self.phrase_key} if self.phrase_key in phrases else set()
        evaluations = self._evaluation_times(event_time)
        for phrase in sorted(phrases):
            for horizon in ("emerging", "sustained"):
                for evaluation in sorted(evaluations[horizon]):
                    metric = self._metric(phrase, horizon, evaluation)
                    output.signals.extend(self._signal_for(phrase, horizon, metric, event, evaluation))
        output.heartbeats.append(self._heartbeat(self._max_event_time or event_time, signals=len(output.signals), late=0))
        return output

    def evaluate(self, evaluation_time: datetime, *, watermark: datetime | None = None) -> EngineOutput:
        """Evaluate aligned event-time windows from a timer callback.

        This method is also used by bounded replay after the fixture's final
        record. It lets a quiet period close a 15-minute/one-hour bucket and
        gives timers a deterministic path to resolve expired lifecycles.
        """
        evaluation = utc(evaluation_time) or datetime.now(timezone.utc)
        if self._max_event_time is None or evaluation > self._max_event_time:
            self._max_event_time = evaluation
        self._prune(self._max_event_time or evaluation)
        if watermark is not None and watermark < evaluation - timedelta(hours=self.config.allowed_lateness_hours):
            return EngineOutput()
        source = {"event_id": stable_id("timer", evaluation.isoformat()), "correlation_id": "pipeline"}
        output = EngineOutput()
        phrases = sorted({str(p).strip().lower() for item in self._documents.values() for p in item["document"].get("phrases", []) if str(p).strip()})
        if self.phrase_key is not None:
            phrases = [self.phrase_key] if self.phrase_key in phrases else []
        for phrase in phrases:
            for horizon in ("emerging", "sustained"):
                metric = self._metric(phrase, horizon, evaluation)
                output.signals.extend(self._signal_for(phrase, horizon, metric, source, evaluation))
        output.heartbeats.append(self._heartbeat(evaluation, signals=len(output.signals), late=0))
        return output

    def _prune(self, reference: datetime) -> None:
        cutoff = reference - timedelta(days=max(self.config.dedup_days, self.config.baseline_days + 2))
        for doc_id, item in list(self._documents.items()):
            if item["event_time"] < cutoff:
                self._documents.pop(doc_id, None)
        for digest, (_doc_id, timestamp) in list(self._content_keys.items()):
            if timestamp < reference - timedelta(days=self.config.dedup_days):
                self._content_keys.pop(digest, None)
        seen_cutoff = reference - timedelta(days=self.config.dedup_days)
        for event_id, timestamp in list(self._seen_events.items()):
            if timestamp < seen_cutoff:
                self._seen_events.pop(event_id, None)
        heartbeat_cutoff = reference - timedelta(days=2)
        for key, timestamp in list(self._heartbeat_keys.items()):
            if timestamp < heartbeat_cutoff:
                self._heartbeat_keys.pop(key, None)
        window_cutoff = reference - timedelta(days=self.config.baseline_days + 2)
        for window_key, metric in list(self._window_metrics.items()):
            if (utc(metric.get("evaluation_bucket")) or reference) < window_cutoff:
                self._window_metrics.pop(window_key, None)
                self._window_active.pop(window_key, None)
                self._fingerprints.pop(":".join(window_key), None)

    def _evaluation_times(self, event_time: datetime) -> dict[str, set[datetime]]:
        current = self._max_event_time or event_time
        values: dict[str, set[datetime]] = {}
        for horizon, minutes in (("emerging", self.config.emerging_interval_minutes), ("sustained", self.config.sustained_interval_minutes)):
            def aligned(value: datetime) -> datetime:
                return value.replace(minute=(value.minute // minutes) * minutes, second=0, microsecond=0)
            start = aligned(event_time)
            end = aligned(current)
            duration = timedelta(hours=self.config.emerging_hours if horizon == "emerging" else self.config.sustained_hours)
            # A late record can affect every closed window whose end lies
            # within one horizon after its event time. Keep the range bounded
            # by the watermark/current event time and retention configuration.
            stop = min(end, aligned(event_time + duration))
            windows: set[datetime] = set()
            cursor = start
            while cursor <= stop:
                windows.add(cursor)
                cursor += timedelta(minutes=minutes)
            # A late document also changes aligned prior-day baseline counts.
            for day in range(1, self.config.baseline_days + 1):
                shifted = event_time + timedelta(days=day)
                cursor = aligned(shifted)
                stop = min(end, aligned(shifted + duration))
                while cursor <= stop:
                    windows.add(cursor)
                    cursor += timedelta(minutes=minutes)
            windows.add(end)
            values[horizon] = windows
        return values

    def _publisher(self, document: dict[str, Any]) -> str:
        domain = urlsplit(str(document.get("url") or "")).netloc.lower()
        return domain or str(document.get("source_name") or "unknown").lower()

    def _metric(self, phrase: str, horizon: str, evaluation: datetime) -> dict[str, Any]:
        if self.phrase_key and phrase != self.phrase_key:
            return {"horizon": horizon, "observed_count": 0, "baseline_mean": 0.0, "baseline_counts": [], "spike": 0.0, "publisher_count": 0, "source_type_count": 0, "confidence": 0.0, "confidence_label": "Low", "persistence_runs": 0, "baseline_persistence": 0, "provider_coverage": [], "evaluation_bucket": evaluation.isoformat().replace("+00:00", "Z"), "event_time_quality": "published_at", "document_ids": []}
        if horizon == "emerging":
            duration = timedelta(hours=self.config.emerging_hours)
            interval = timedelta(minutes=self.config.emerging_interval_minutes)
        else:
            duration = timedelta(hours=self.config.sustained_hours)
            interval = timedelta(minutes=self.config.sustained_interval_minutes)
        minutes = int(interval.total_seconds() // 60)
        evaluation = evaluation.replace(minute=(evaluation.minute // minutes) * minutes, second=0, microsecond=0)
        active = [
            item for item in self._documents.values()
            if item["event_time"] > evaluation - duration
            and item["event_time"] <= evaluation
            and phrase in {str(p).lower() for p in item["document"].get("phrases", [])}
        ]
        observed = len(active)
        publishers = {item["publisher"] for item in active}
        source_types = {item["source_type"] for item in active}
        provider_mix = Counter(item["provider"] for item in active)
        source_type_counts = Counter(item["source_type"] for item in active)
        quality_flags = sorted({flag for item in active for flag in item.get("quality_flags", [])})
        baseline_counts: list[int] = []
        for day in range(1, self.config.baseline_days + 1):
            start = evaluation - timedelta(days=day) - duration
            end = evaluation - timedelta(days=day)
            baseline_counts.append(sum(1 for item in self._documents.values() if start < item["event_time"] <= end and phrase in {str(p).lower() for p in item["document"].get("phrases", [])}))
        baseline_mean = sum(baseline_counts) / max(1, len(baseline_counts))
        spike = observed / max(1.0, baseline_mean)
        persistence = len({item["event_time"].date() for item in active})
        baseline_persistence = sum(1 for count in baseline_counts if count > 0)
        confidence, label = narrative_confidence(
            source_count=observed,
            publisher_count=len(publishers),
            source_type_count=len(source_types),
            persistence_runs=baseline_persistence,
            velocity_score=spike,
            provider_mix=provider_mix,
        )
        bucket = evaluation.replace(minute=(evaluation.minute // int(interval.total_seconds() // 60)) * int(interval.total_seconds() // 60), second=0, microsecond=0)
        return {
            "horizon": horizon,
            "observed_count": observed,
            "baseline_mean": baseline_mean,
            "baseline_counts": baseline_counts,
            "spike": spike,
            "publisher_count": len(publishers),
            "source_type_count": len(source_types),
            "source_type_counts": dict(sorted(source_type_counts.items())),
            "confidence": confidence,
            "confidence_label": label,
            "persistence_runs": persistence,
            "baseline_persistence": baseline_persistence,
            "provider_coverage": sorted(provider_mix),
            "provider_mix": dict(sorted(provider_mix.items())),
            "evaluation_bucket": bucket.isoformat().replace("+00:00", "Z"),
            "event_time_quality": "published_at" if all(item["quality"] == "published_at" for item in active) else "collected_at_fallback",
            "document_ids": sorted(item["document"]["id"] for item in active),
            "quality_flags": quality_flags,
            "first_observed_at": min((item["event_time"] for item in self._documents.values() if phrase in {str(p).lower() for p in item["document"].get("phrases", [])}), default=evaluation),
            "latest_observed_at": max((item["event_time"] for item in self._documents.values() if phrase in {str(p).lower() for p in item["document"].get("phrases", [])}), default=evaluation),
        }

    def _signal_for(self, phrase: str, horizon: str, metric: dict[str, Any], source_event: dict[str, Any], event_time: datetime) -> list[dict[str, Any]]:
        if self.phrase_key and phrase != self.phrase_key:
            return []
        qualifies = metric["observed_count"] >= self.config.min_documents and metric["publisher_count"] >= self.config.min_publishers and metric["source_type_count"] >= self.config.min_source_types and metric["spike"] >= self.config.min_spike and metric["confidence"] >= self.config.min_confidence
        bucket = str(metric["evaluation_bucket"])
        window_key = (phrase, horizon, bucket)
        metric_fingerprint = semantic_hash(metric)
        if metric_fingerprint == self._fingerprints.get(":".join(window_key)):
            return []
        self._fingerprints[":".join(window_key)] = metric_fingerprint
        self._window_metrics[window_key] = metric
        self._window_active[window_key] = qualifies
        current_key = (phrase, horizon)
        current_bucket = self._current_buckets.get(current_key)
        is_current = current_bucket is None or bucket >= current_bucket
        if is_current:
            self._current_buckets[current_key] = bucket
            self._horizon_metrics[phrase][horizon] = metric
            self._active_horizons[phrase][horizon] = qualifies
        was_signal_active = self._signal_active[phrase]
        current_active = any(self._active_horizons[phrase].get(name, False) for name in ("emerging", "sustained"))
        if current_active:
            self._signal_active[phrase] = True
            lifecycle = "active" if not was_signal_active else "revised"
        else:
            if not was_signal_active:
                return []
            self._signal_active[phrase] = False
            lifecycle = "resolved"
        self._signal_revisions[phrase] += 1
        revision = self._signal_revisions[phrase]
        horizons = dict(self._horizon_metrics[phrase])
        card_metric = self._horizon_metrics[phrase].get(horizon, metric)
        card_time = utc(card_metric["evaluation_bucket"]) or event_time
        signal_id = stable_id("signal", phrase, length=16)
        payload = {
            "signal_id": signal_id,
            "canonical_phrase": phrase,
            "canonical_phrase_id": stable_id("phrase", phrase, length=16),
            "horizon": horizon,
            "lifecycle_status": lifecycle,
            "revision": revision,
            "scoring_version": "b4-confidence-v1",
            "score": {"spike": card_metric["spike"], "confidence": card_metric["confidence"], "observed_count": card_metric["observed_count"], "baseline_count": round(card_metric["baseline_mean"])},
            "observed_window": {"start": (card_time - (timedelta(hours=self.config.emerging_hours) if horizon == "emerging" else timedelta(hours=self.config.sustained_hours))).isoformat().replace("+00:00", "Z"), "end": card_time.isoformat().replace("+00:00", "Z"), "label": "custom"},
            "baseline_window": {"start": (event_time - timedelta(days=self.config.baseline_days) - (timedelta(hours=self.config.emerging_hours) if horizon == "emerging" else timedelta(hours=self.config.sustained_hours))).isoformat().replace("+00:00", "Z"), "end": (event_time - timedelta(days=1)).isoformat().replace("+00:00", "Z"), "label": "custom"},
            "horizon_metrics": horizons,
            "baseline_statistics": {"mean": card_metric["baseline_mean"], "counts": card_metric["baseline_counts"], "persistence_days": card_metric["baseline_persistence"]},
            "source_diversity": card_metric["source_type_count"],
            "publisher_diversity": card_metric["publisher_count"],
            "supporting_document_ids": sorted({document_id for item in horizons.values() for document_id in item["document_ids"]}),
            "coverage_limitations": sorted(set(card_metric.get("quality_flags", [])) | ({"event_time_fallback_used"} if card_metric["event_time_quality"] != "published_at" else set())),
            "event_time_quality": card_metric["event_time_quality"],
            "origin_disclaimer": "This signal identifies the first observation in the available dataset, not proven origin.",
            "auto_investigate": False,
            "artifact_lineage": sorted({str(item["document"].get("metadata", {}).get("enrichment_artifact_id") or item["document"].get("metadata", {}).get("enrichment_artifact_hash")) for item in self._documents.values() if item["document"]["id"] in card_metric["document_ids"]}),
            "first_observed_at": card_metric["first_observed_at"].isoformat().replace("+00:00", "Z"),
            "latest_observed_at": card_metric["latest_observed_at"].isoformat().replace("+00:00", "Z"),
        }
        from models.events import DetectedSignalPayload
        typed = DetectedSignalPayload.model_validate({**payload, "semantic_output_hash": None}).model_dump(mode="json")
        typed.pop("semantic_output_hash", None)
        payload["semantic_output_hash"] = semantic_hash(typed)
        event = envelope(event_type="signal.detected", event_id=stable_id("signal_event", signal_id, revision, payload["semantic_output_hash"]), payload=payload, partition_key=phrase, occurred_at=event_time, correlation_id=str(source_event.get("correlation_id") or signal_id), causation_id=source_event.get("event_id"))
        from models.events import DetectedSignalEvent
        DetectedSignalEvent.model_validate(event)
        return [event]

    def _late_event(self, source: dict[str, Any], document: dict[str, Any], event_time: datetime, watermark: datetime, digest: str) -> dict[str, Any]:
        lateness = max(0.0, (watermark - event_time).total_seconds())
        payload = {
            "document": document,
            "input_hash": digest,
            "raw_event_id": str((source.get("payload") or {}).get("raw_event_id") or source.get("event_id") or document["id"]),
            "event_time_quality": str((document.get("metadata") or {}).get("event_time_quality") or "published_at"),
            "lateness_seconds": lateness,
            "quality_flags": ["beyond_allowed_lateness"],
            "investigation_id": (source.get("payload") or {}).get("investigation_id"),
            "run_id": (source.get("payload") or {}).get("run_id"),
            "action_id": (source.get("payload") or {}).get("action_id"),
        }
        return envelope(event_type="document.late", event_id=stable_id("late", source.get("event_id") or document["id"], digest), payload=payload, partition_key=document["id"], occurred_at=event_time, correlation_id=str(source.get("correlation_id") or document["id"]), causation_id=source.get("event_id"))

    def _heartbeat(self, evaluation: datetime, *, signals: int, late: int) -> dict[str, Any]:
        bucket = evaluation.replace(second=0, microsecond=0).isoformat()
        if bucket not in self._heartbeat_keys:
            self._heartbeat_keys[bucket] = evaluation
        payload = {"evaluated_at": evaluation.isoformat().replace("+00:00", "Z"), "signal_count": signals, "late_event_count": late, "watermark": self.watermark.isoformat() if self.watermark else None, "documents_in_state": len(self._documents), "status": "healthy"}
        return envelope(event_type="pipeline.evaluated", event_id=stable_id("heartbeat", bucket, self.phrase_key or "global", semantic_hash(payload)), payload=payload, partition_key=self.phrase_key or "pipeline", occurred_at=evaluation, correlation_id="pipeline")
