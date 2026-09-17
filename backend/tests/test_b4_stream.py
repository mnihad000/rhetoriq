from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from flink.normalization import normalize_raw_event
from flink.serialization import ConfluentJsonAdapter
from flink.stream_engine import StreamIntelligenceEngine
from models.document import Document
from models.events import (
    EnrichedDocumentEvent,
    EnrichmentRequestedEvent,
    RawDocumentEvent,
    RawDocumentPayload,
    RetrievalReceipt,
    validate_event,
)
from services.enrichment import EnrichmentService, RecordedProvider
from services.event_factory import raw_event_from_document


NOW = datetime(2026, 1, 10, 12, tzinfo=timezone.utc)


def raw_event() -> RawDocumentEvent:
    return RawDocumentEvent.create(
        RawDocumentPayload(
            provider="fixture",
            transport="fixture",
            provider_record_id="r-1",
            source_url="HTTPS://Example.com/story?utm_source=fixture",
            canonical_url="https://example.com/story?utm_source=fixture",
            title="  New\n  Energy   Policy ",
            body="New\x00 energy policy details",
            collected_at=NOW,
            content_type="article",
            retrieval=RetrievalReceipt(status="provider_metadata_only"),
        ),
        producer="fixture",
        correlation_id="corr-1",
        partition_key="r-1",
        occurred_at=NOW,
        event_id="raw-1",
    )


def enriched(index: int, *, at: datetime = NOW, phrases: list[str] | None = None) -> EnrichedDocumentEvent:
    domain = f"publisher-{index}.example"
    document = Document(
        id=f"doc-{index}", source_id=f"domain:{domain}", source_name=domain,
        source_type="national_news" if index % 2 else "commentary",
        url=f"https://{domain}/energy", title="Energy policy", published_at=at,
        collected_at=at, text=f"Energy policy details {index}", snippet=f"Energy policy details {index}",
        entities=[], phrases=[], metadata={"provider": "recorded-a" if index % 2 else "recorded-b"},
    )
    raw = raw_event_from_document(document, producer="fixture", correlation_id="corr-1")
    request = normalize_raw_event(raw)
    service = EnrichmentService(gemini=RecordedProvider({
        "extraction": {"entities": [], "canonical_phrases": [{"phrase": "energy policy", "surface_form": "Energy policy", "start": 0, "end": 13}]},
        "embedding": [1.0] + [0.0] * 383,
    }))
    result = service.enrich(EnrichmentRequestedEvent.model_validate(request))
    return result.model_copy(update={"event_id": f"enriched-{index}"})


def test_normalization_uses_shared_contract_and_is_replay_stable():
    first = normalize_raw_event(raw_event().model_dump(mode="json"))
    second = normalize_raw_event(raw_event().model_dump(mode="json"))
    assert first["payload"] == second["payload"]
    assert first["event_id"] == second["event_id"]
    assert first["payload"]["document"]["text"] == "New energy policy details"
    assert first["payload"]["document"]["phrases"] == []
    assert first["payload"]["quality_flags"] == ["published_at_fallback", "language_unknown"]


def test_apicurio_confluent_frame_requires_explicit_offline_schema_id():
    adapter = ConfluentJsonAdapter(schema_ids={"documents.enriched.v1": 17})
    event = {"event_id": "e", "payload": {"ok": True}}
    assert adapter.decode("documents.enriched.v1", adapter.encode("documents.enriched.v1", event)) == event
    with pytest.raises(ValueError):
        ConfluentJsonAdapter().encode("documents.enriched.v1", event)
    with pytest.raises(ValueError):
        adapter.decode("documents.enriched.v1", b"\x00\x00\x00\x00\x12{}")


def test_dual_horizon_signal_deduplicates_documents_and_revises():
    engine = StreamIntelligenceEngine()
    # Seed aligned prior-day buckets so confidence includes baseline
    # persistence, as it does in a seven-day production replay.
    for i in range(101, 105):
        engine.process(enriched(i, at=NOW - timedelta(days=1, minutes=i - 101)))
    outputs = [engine.process(enriched(i, at=NOW - timedelta(minutes=i))) for i in range(1, 5)]
    signals = [event for output in outputs for event in output.signals]
    assert signals
    assert {event["payload"]["canonical_phrase"] for event in signals} == {"energy policy"}
    assert all(event["payload"]["revision"] >= 1 for event in signals)
    duplicate_event = enriched(4, at=NOW - timedelta(minutes=3)).model_copy(update={"event_id": "enriched-4-duplicate"})
    duplicate = engine.process(duplicate_event.model_dump(mode="json"))
    assert not duplicate.signals
    assert duplicate.processed
    assert duplicate.processed[0]["payload"]["processing"]["duplicate_of_document_id"] == "doc-4"


def test_beyond_allowed_lateness_is_audit_only():
    engine = StreamIntelligenceEngine()
    engine.process(enriched(1, at=NOW))
    late = engine.process(enriched(2, at=NOW - timedelta(hours=25)))
    assert not late.processed
    assert len(late.late) == 1
    assert "beyond_allowed_lateness" in late.late[0]["payload"]["quality_flags"]


def test_partial_enrichment_is_rejected_before_state_mutation():
    engine = StreamIntelligenceEngine()
    event = enriched(1).model_dump(mode="json")
    event["payload"].pop("enrichment")
    with pytest.raises(ValueError):
        engine.process(event)
    # A retry with the same id is still eligible for DLQ handling because the
    # failed contract was never marked as consumed.
    with pytest.raises(ValueError):
        engine.process(event)


def test_explicit_global_watermark_controls_lateness_and_signal_hashes():
    engine = StreamIntelligenceEngine()
    late = engine.process(enriched(1, at=NOW - timedelta(hours=25)).model_dump(mode="json"), watermark=NOW)
    assert late.late and not late.processed
    engine = StreamIntelligenceEngine()
    # Seed one aligned baseline document per day and a qualifying current
    # window, then verify final signal payloads are typed.
    for day in range(7, 0, -1):
        engine.process(enriched(100 + day, at=NOW - timedelta(days=day)).model_dump(mode="json"))
    for i in range(1, 5):
        engine.process(enriched(i, at=NOW - timedelta(minutes=i)).model_dump(mode="json"), watermark=NOW)
    output = engine.evaluate(NOW)
    assert output.signals
    for event in output.signals:
        validate_event("signals.detected.v1", event)
        assert event["payload"]["semantic_output_hash"].startswith("sha256:")
        assert event["payload"]["first_observed_at"]
        assert event["payload"]["latest_observed_at"]


def test_phrase_key_scopes_window_evaluations():
    engine = StreamIntelligenceEngine(phrase_key="energy policy")
    output = engine.process(enriched(1).model_dump(mode="json"))
    assert all(event["payload"].get("canonical_phrase") == "energy policy" for event in output.signals)


def test_timer_evaluation_resolves_only_after_both_horizons_expire():
    engine = StreamIntelligenceEngine()
    for day in range(7, 0, -1):
        engine.process(enriched(100 + day, at=NOW - timedelta(days=day)).model_dump(mode="json"))
    for i in range(1, 5):
        engine.process(enriched(i, at=NOW - timedelta(minutes=i)).model_dump(mode="json"))
    assert engine.evaluate(NOW).signals
    resolved = engine.evaluate(NOW + timedelta(days=2)).signals
    assert resolved
    assert resolved[-1]["payload"]["lifecycle_status"] == "resolved"
