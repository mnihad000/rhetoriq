from types import SimpleNamespace

from api.research import research_health
from config import get_settings
from services.event_store import EventStore


def test_research_health_reports_database_queue_and_real_worker_heartbeat(tmp_path, monkeypatch):
    settings = get_settings()
    target = str(tmp_path / "health.sqlite3")
    monkeypatch.setattr(settings, "DATABASE_URL", "")
    monkeypatch.setattr(settings, "INVESTIGATION_DB_PATH", target)
    monkeypatch.setattr(settings, "RESEARCH_CHECKPOINT_DB_PATH", str(tmp_path / "checkpoints.sqlite3"))
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    monkeypatch.setattr("api.research.httpx.get", lambda *_args, **_kwargs: SimpleNamespace(
        is_success=True, status_code=200, json=lambda: {"results": [], "models": []},
    ))
    unavailable = research_health()
    assert unavailable["execution_mode"] == "database"
    assert unavailable["status"] == "degraded"
    assert unavailable["components"]["worker"]["status"] == "unavailable"
    EventStore(target).heartbeat("worker-test", "database")
    healthy = research_health()
    assert healthy["status"] == "ready"
    assert healthy["components"]["worker"]["workers"][0]["worker_id"] == "worker-test"
    assert healthy["components"]["jobs"]["pending"] == 0
    assert not {"kafka", "schema_registry", "flink", "event_consumers"}.intersection(healthy["components"])
