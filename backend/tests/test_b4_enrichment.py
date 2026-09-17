from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest

from models.events import (
    EnrichmentRequestedEvent,
    RawDocumentEvent,
    RawDocumentPayload,
    RetrievalReceipt,
)
from services.document_normalization import normalize_raw_event
from services.enrichment import (
    EnrichmentService,
    RecordedProvider,
    ReplayArtifactMissing,
    normalize_embedding,
    validate_extraction,
)
from services.enrichment_artifacts import EnrichmentArtifactStore, EnrichmentBudgetLedger, BudgetExceeded
from events.enrichment_worker import EnrichmentRetryExhausted, EnrichmentWorker


def _raw() -> RawDocumentEvent:
    collected = datetime(2026, 9, 16, tzinfo=timezone.utc)
    return RawDocumentEvent.create(
        RawDocumentPayload(
            provider="fixture", transport="recorded", provider_record_id="r1",
            source_url="HTTPS://Example.com/story?b=2&a=1#tracking",
            canonical_url="https://example.com/story?b=2&a=1#tracking", title="Acme",
            body="Acme reports an energy surcharge", snippet="Acme reports an energy surcharge",
            published_at=collected + timedelta(days=1), collected_at=collected,
            content_type="article", retrieval=RetrievalReceipt(status="canonical_evidence"),
        ), producer="fixture", correlation_id="c1", partition_key="r1",
        occurred_at=collected,
    )


def _request() -> EnrichmentRequestedEvent:
    return normalize_raw_event(_raw())


def test_normalization_is_deterministic_and_falls_back_from_future_publication() -> None:
    raw = _raw()
    first, second = normalize_raw_event(raw), normalize_raw_event(raw)
    assert first.event_id == second.event_id
    assert first.payload.input_hash == second.payload.input_hash
    assert first.payload.document.metadata["exact_content_hash"] == second.payload.document.metadata["exact_content_hash"]
    assert first.payload.document.published_at == first.payload.document.collected_at
    assert "future_published_at" in first.payload.quality_flags
    assert first.payload.document.url == "https://example.com/story?a=1&b=2"


def test_evidence_offsets_fail_closed() -> None:
    with pytest.raises(ValueError):
        validate_extraction("Acme reports energy", {"entities": [], "canonical_phrases": [
            {"phrase": "missing", "surface_form": "missing", "start": 0, "end": 7}
        ]})
    with pytest.raises(ValueError):
        validate_extraction("Acme", {"entities": [{}], "canonical_phrases": []})


def test_recorded_enrichment_is_immutable_and_replayable(tmp_path) -> None:
    request = _request()
    fixture = json.loads((__import__("pathlib").Path(__file__).parent / "fixtures/b4/gemini_extraction.json").read_text())
    provider = RecordedProvider({"extraction": fixture, "embedding": [1.0] + [0.0] * 383})
    store = EnrichmentArtifactStore(str(tmp_path / "artifacts.sqlite"))
    service = EnrichmentService(gemini=provider, artifact_store=store)
    first = service.enrich(request)
    second = EnrichmentService(gemini=RecordedProvider({"extraction": fixture, "embedding": [0.0, 1.0] + [0.0] * 382}), artifact_store=store).enrich(request)
    assert first.payload.semantic_output_hash == second.payload.semantic_output_hash
    assert first.event_id == second.event_id
    replay = EnrichmentService(gemini=provider, artifact_store=store, replay=True).enrich(request)
    assert replay.payload.document.entities == ["Acme"]


def test_groq_fallback_does_not_replace_gemini_embedding() -> None:
    class Broken:
        provider, model = "gemini", "flash"
        def extract(self, text): raise ValueError("malformed")
        def embed(self, text): return [1.0] + [0.0] * 383
    request = _request()
    groq = RecordedProvider({"extraction": {"entities": [], "canonical_phrases": []}}, provider="groq", model="llama")
    service = EnrichmentService(gemini=Broken(), groq=groq)
    result = service.enrich(request)
    assert result.payload.enrichment.provider == "groq"
    assert len(result.payload.enrichment.embedding) == 384


def test_budget_is_durable_and_counts_used_reservations(tmp_path) -> None:
    ledger = EnrichmentBudgetLedger(str(tmp_path / "budget.sqlite"))
    reservation = ledger.reserve(request_limit=1)
    reservation.settle()
    with pytest.raises(BudgetExceeded):
        ledger.reserve(request_limit=1)


def test_worker_dlqs_after_three_attempts() -> None:
    class BrokenService:
        def enrich(self, request): raise TimeoutError("provider timeout")
    worker = EnrichmentWorker(BrokenService(), max_attempts=3, sleep=lambda _: None)
    with pytest.raises(EnrichmentRetryExhausted):
        worker.process_request(_request())
    assert len(worker.dead_letters) == 1
    assert worker.dead_letters[0].payload.attempt_count == 3
