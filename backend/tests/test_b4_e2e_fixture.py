"""Recorded-provider fixture across B4 normalization, stream and projections.

This is the offline product fixture, not proof of the Docker/Flink runtime gate.
"""
from datetime import datetime, timedelta, timezone

from config import get_settings
from models.document import Document
from models.events import validate_event
from services.document_normalization import normalize_raw_event
from services.enrichment import EnrichmentService, RecordedProvider
from services.enrichment_artifacts import EnrichmentArtifactStore
from services.event_factory import raw_event_from_document
from services.signal_repository import SignalRepository


NOW = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)


def raw_fixture(index, published_at, *, duplicate_text=None):
    domain = f"publisher{index % 4}.example"
    text = duplicate_text or f"Public records policy changes today. Record {index}."
    document = Document(
        id=f"doc_b4_fixture_{index}", source_name=domain,
        source_type="government_record" if index % 2 else "national_news",
        url=f"https://{domain}/records/{index}", title=f"Public records policy {index}",
        text=text, snippet=text, collected_at=NOW, published_at=published_at,
        language="en", content_type="article", entities=[], phrases=[],
        metadata={"provider": "federal_register" if index % 2 else "searxng",
                  "source_policy": {"decision": "allow", "citable": True},
                  "evidence_status": "canonical_evidence"},
    )
    event = raw_event_from_document(document, producer="fixture", correlation_id="b4-fixture")
    return event.model_copy(update={"event_id": f"raw_b4_fixture_{index}"})


def recorded_service(target, *, replay=False):
    fixture = {
        "extraction": {
            "entities": [],
            "canonical_phrases": [{"phrase": "public records policy", "surface_form": "Public records policy",
                                   "start": 0, "end": len("Public records policy")}],
        },
        "embedding": [1.0] + [0.0] * 383,
    }
    provider = RecordedProvider(fixture)
    if replay:
        def forbid_inference(_text):
            raise AssertionError("Replay must never invoke a hosted provider")
        provider.extract = forbid_inference
        provider.embed = forbid_inference
    return EnrichmentService(gemini=provider, replay=replay,
                             artifact_store=EnrichmentArtifactStore(str(target)))


def semantic_event(event):
    return {key: value for key, value in event.items() if key != "produced_at"}


def test_clean_replays_produce_identical_projectable_outputs(tmp_path):
    from flink.stream_engine import StreamIntelligenceEngine
    from flink.contracts import PROCESSED_DOCUMENTS_TOPIC, SIGNALS_DETECTED_TOPIC
    source = [raw_fixture(day, NOW - timedelta(days=8 - day)) for day in range(1, 8)]
    source += [raw_fixture(20 + index, NOW - timedelta(minutes=60 - index)) for index in range(4)]
    results = []
    for run in range(2):
        service = recorded_service(tmp_path / "recorded-artifacts.sqlite3", replay=run > 0)
        engine = StreamIntelligenceEngine()
        repository = SignalRepository(str(tmp_path / f"projection-{run}.sqlite3"))
        events = []
        for raw in source:
            request = normalize_raw_event(raw)
            enriched = service.enrich(request)
            output = engine.process(enriched.model_dump(mode="json"), now=NOW)
            for topic, emitted in output.all_events():
                typed = validate_event(topic, emitted)
                if topic == PROCESSED_DOCUMENTS_TOPIC:
                    repository.project_processed(typed)
                elif topic == SIGNALS_DETECTED_TOPIC:
                    repository.project_signal(typed)
                if topic in {PROCESSED_DOCUMENTS_TOPIC, SIGNALS_DETECTED_TOPIC}:
                    events.append((topic, semantic_event(typed.model_dump(mode="json"))))
        # Evaluate the next closed interval using the same event-time mechanism
        # exposed by the pure engine, if signals are timer-driven.
        if hasattr(engine, "evaluate"):
            for topic, emitted in engine.evaluate(NOW).all_events():
                typed = validate_event(topic, emitted)
                if topic == SIGNALS_DETECTED_TOPIC:
                    repository.project_signal(typed)
                    events.append((topic, semantic_event(typed.model_dump(mode="json"))))
        assert repository.latest_signals(), "The qualifying recorded fixture must publish a signal"
        results.append(events)
    assert results[0] == results[1]


def test_automatic_investigation_remains_disabled(tmp_path, monkeypatch):
    from models.events import DetectedSignalEvent, DetectedSignalPayload, SignalScore
    from models.investigation import InvestigationPlanTimeWindow
    from services.event_handlers import EventHandlers
    from services.event_store import EventStore
    monkeypatch.setattr(get_settings(), "FLINK_AUTO_INVESTIGATE", False)
    event = DetectedSignalEvent.create(
        DetectedSignalPayload(
            signal_id="off-by-default", canonical_phrase_id="public records policy",
            observed_window=InvestigationPlanTimeWindow(label="custom"),
            baseline_window=InvestigationPlanTimeWindow(label="all_time"),
            score=SignalScore(spike=4, confidence=.9, observed_count=4, baseline_count=1),
            publisher_diversity=3, source_diversity=2, auto_investigate=True,
        ), producer="fixture", correlation_id="guard", partition_key="guard",
    )
    target = str(tmp_path / "guard.sqlite3")
    EventHandlers(target).schedule_signal(event)
    assert not EventStore(target).pending()
    assert SignalRepository(target).latest_signals()
