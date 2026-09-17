from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx

from models.trending import PublishedTrendingSnapshot, TrendingTopic
from services.signal_repository import SignalRepository
from services.trending_runtime import TrendingRuntimeStore
from services.trending_service import TrendingService, _topics_from_signals
from services.flink_health import get_flink_health


def test_signal_projection_is_idempotent_and_keeps_both_horizons(tmp_path):
    repository = SignalRepository(str(tmp_path / "signals.sqlite3"))
    event = {
        "event_id": "signal-event-1",
        "event_type": "signal.detected",
        "occurred_at": "2026-09-16T12:00:00Z",
        "produced_at": "2026-09-16T12:01:00Z",
        "payload": {
            "signal_id": "signal-1",
            "signal_revision": 2,
            "canonical_phrase_id": "phrase-1",
            "canonical_phrase": "climate tax",
            "horizon_metrics": {
                "emerging": {"observed_count": 5, "baseline_count": 2, "spike": 2.5, "confidence": 0.8},
                "sustained": {"observed_count": 10, "baseline_count": 4, "spike": 2.5, "confidence": 0.7},
            },
            "supporting_document_ids": ["doc-1"],
            "coverage_limitations": ["dataset coverage is incomplete"],
        },
    }
    assert repository.project_signal(event) is True
    assert repository.project_signal(event) is False
    signal = repository.latest_signals()[0]
    assert {metric["horizon"] for metric in signal["metrics"]} == {"emerging", "sustained"}
    assert signal["signal_revision"] == 2
    assert repository.health()["signal_revision_count"] == 1


def test_processed_lineage_and_evaluation_heartbeat_are_replay_safe(tmp_path):
    repository = SignalRepository(str(tmp_path / "signals.sqlite3"))
    event = {
        "event_id": "processed-event-1",
        "event_type": "document.processed",
        "occurred_at": "2026-09-16T12:00:00Z",
        "payload": {
            "document": {"id": "doc-1", "title": "Example"},
            "processing": {"raw_event_id": "raw-1", "semantic_output_hash": "hash-1"},
        },
    }
    assert repository.project_processed(event) is True
    assert repository.project_processed(event) is False
    evaluated_at = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)
    details = {"signals": {"evaluated": 1}, "enrichment": {"dlq_depth": 0}}
    assert repository.record_evaluation(evaluated_at, details) is True
    assert repository.record_evaluation(evaluated_at, details) is False
    assert repository.latest_evaluation()["details"] == details


def test_flink_monitor_requires_named_job_checkpoint_taskmanager_and_fresh_heartbeat(monkeypatch):
    now = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)
    timestamp = int(now.timestamp() * 1000) - 60_000
    responses = {
        "/jobs/overview": {"jobs": [{"name": "rhetoriq-b4-flink", "jid": "job-1", "state": "RUNNING"}]},
        "/jobs/job-1/checkpoints": {"latest": {"completed": {"checkpoint_id": 3, "latest_ack_timestamp": timestamp}}},
        "/taskmanagers": {"taskmanagers": [{"id": "tm-1", "slotsNumber": 4, "freeSlots": 2}]},
    }

    def fake_get(url, **_kwargs):
        path = "/" + url.split("/", 3)[-1].split("?", 1)[0]
        return httpx.Response(200, json=responses[path])

    monkeypatch.setattr("services.flink_health.httpx.get", fake_get)
    settings = SimpleNamespace(
        FLINK_REST_URL="http://flink.test",
        FLINK_HEARTBEAT_MAX_AGE_SECONDS=1800,
        ENABLE_FLINK_TRENDING=False,
    )
    evaluation = {"evaluated_at": (now - timedelta(minutes=2)).isoformat(), "details": {}}
    health = get_flink_health(settings, evaluation=evaluation, now=now)
    assert health["status"] == "ready"
    assert health["cutover_enabled"] is False

    future = dict(evaluation, evaluated_at=(now + timedelta(seconds=1)).isoformat())
    assert get_flink_health(settings, evaluation=future, now=now)["status"] == "unhealthy"


def test_enabled_feed_uses_flink_signal_and_disabled_gate_preserves_legacy(monkeypatch, tmp_path):
    target = str(tmp_path / "signals.sqlite3")
    repository = SignalRepository(target)
    now = datetime.now(timezone.utc)
    repository.project_signal({
        "event_id": "signal-cutover",
        "occurred_at": now.isoformat(),
        "payload": {
            "signal_id": "signal-cutover",
            "signal_revision": 1,
            "canonical_phrase_id": "phrase",
            "canonical_phrase": "climate tax",
            "horizon_metrics": {"emerging": {"observed_count": 5, "baseline_count": 2, "spike": 2.5, "confidence": .8}},
            "supporting_document_ids": ["doc-1"],
        },
    })
    repository.record_evaluation(now, {})
    service = TrendingService.__new__(TrendingService)
    service._settings = SimpleNamespace(ENABLE_FLINK_TRENDING=True, FLINK_HEARTBEAT_MAX_AGE_SECONDS=1800)
    service._signals = repository
    monkeypatch.setattr("services.trending_service.get_flink_health", lambda *args, **kwargs: {"status": "ready", "state": "RUNNING"})
    stream = service._get_flink_feed(limit=3, now=now)
    assert stream.source == "flink"
    assert stream.topics[0].emerging_observed_count == 5

    legacy_topic = TrendingTopic(
        id="legacy", title="Legacy", canonical_phrase="legacy", summary="legacy", status="emerging",
        confidence_label="Medium", first_observed_at=now, latest_observed_at=now,
    )
    service._settings = SimpleNamespace(ENABLE_FLINK_TRENDING=True, FLINK_HEARTBEAT_MAX_AGE_SECONDS=1800)
    service._runtime = TrendingRuntimeStore("")
    service._runtime.get_latest_snapshot = lambda: PublishedTrendingSnapshot(
        snapshot_id="snap", generated_at=now, fresh_until=now - timedelta(hours=1), topics=[legacy_topic]
    )
    service._runtime.get_last_error = lambda: None
    monkeypatch.setattr("services.trending_service.get_flink_health", lambda *args, **kwargs: {"status": "unhealthy", "state": "STOPPED"})
    fallback = service.get_feed(limit=3)
    assert fallback.source == "legacy"
    assert fallback.fallback_active is True
    assert [topic.id for topic in fallback.topics] == ["legacy"]


def test_manual_investigation_accepts_stream_signal_topic(tmp_path):
    target = str(tmp_path / "signals.sqlite3")
    repository = SignalRepository(target)
    repository.project_signal({
        "event_id": "signal-manual",
        "occurred_at": "2026-09-16T12:00:00Z",
        "payload": {
            "signal_id": "signal-manual",
            "signal_revision": 1,
            "canonical_phrase_id": "phrase-manual",
            "canonical_phrase": "public records",
            "score": {"observed_count": 4, "baseline_count": 1, "spike": 4, "confidence": .8},
        },
    })
    service = TrendingService.__new__(TrendingService)
    service._settings = SimpleNamespace(
        ENABLE_FLINK_TRENDING=True,
        TRENDING_REFRESH_HOURS=6,
    )
    service._signals = repository
    service._runtime = TrendingRuntimeStore("")
    service._repository = SimpleNamespace(get_latest_snapshot=lambda: None)

    class FakeInvestigationRepository:
        def __init__(self):
            self.saved = None

        def investigation_exists(self, _investigation_id):
            return False

        def save_plan(self, investigation_id, query_text, plan):
            self.saved = (investigation_id, query_text, plan)

    investigations = FakeInvestigationRepository()
    response = service.start_investigation_for_topic("signal-manual", investigations)
    assert response.topic_id == "signal-manual"
    assert response.canonical_phrase == "public records"
    assert investigations.saved[1] == "Trace the narrative around public records"
