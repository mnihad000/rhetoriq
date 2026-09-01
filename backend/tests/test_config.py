from datetime import datetime, timezone
from pathlib import Path

import pytest

from config import BACKEND_DIR, Settings
from models.document import Document
from services.document_store import live_store
from services.ingestion import get_merged_documents


def test_env_file_can_be_loaded_independent_of_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("DEMO_MODE", raising=False)
    monkeypatch.delenv("INVESTIGATION_DB_PATH", raising=False)

    env_file = tmp_path / "backend.env"
    env_file.write_text("DEMO_MODE=false\nINVESTIGATION_DB_PATH=from-env.sqlite3\n", encoding="utf-8")

    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    settings = Settings(_env_file=env_file)

    assert settings.DEMO_MODE is False
    assert settings.INVESTIGATION_DB_PATH == str(BACKEND_DIR / "from-env.sqlite3")


def test_relative_db_path_resolves_under_backend_dir(monkeypatch):
    monkeypatch.delenv("INVESTIGATION_DB_PATH", raising=False)

    settings = Settings(_env_file=None, INVESTIGATION_DB_PATH="data/investigations.sqlite3")

    assert settings.INVESTIGATION_DB_PATH == str(BACKEND_DIR / "data" / "investigations.sqlite3")


def test_absolute_db_path_is_preserved(tmp_path, monkeypatch):
    monkeypatch.delenv("INVESTIGATION_DB_PATH", raising=False)

    db_path = tmp_path / "investigations.sqlite3"

    settings = Settings(_env_file=None, INVESTIGATION_DB_PATH=str(db_path))

    assert Path(settings.INVESTIGATION_DB_PATH) == db_path


def test_memory_db_path_is_preserved(monkeypatch):
    monkeypatch.delenv("INVESTIGATION_DB_PATH", raising=False)

    settings = Settings(_env_file=None, INVESTIGATION_DB_PATH=":memory:")

    assert settings.INVESTIGATION_DB_PATH == ":memory:"


def test_production_settings_require_a_database_url():
    with pytest.raises(ValueError, match="DATABASE_URL"):
        Settings(_env_file=None, DEPLOYMENT_ENV="production")


def test_get_merged_documents_excludes_demo_docs_in_live_mode(monkeypatch):
    monkeypatch.delenv("DEMO_MODE", raising=False)
    live_store.clear()

    live_doc = Document(
        id="doc_live",
        source_id="src_live",
        source_name="example.com",
        source_type="national_news",
        url="https://example.com/story",
        title="Live story",
        published_at=datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc),
        collected_at=datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc),
        text="Live story body.",
        snippet="Live story body.",
        language="en",
        content_type="article",
        geographic_scope="national",
        entities=["live"],
        phrases=["live story"],
        metadata={},
    )
    demo_doc = live_doc.model_copy(
        update={
            "id": "doc_demo",
            "source_name": "demo.example",
            "url": "https://demo.example/story",
            "title": "Demo story",
            "text": "Demo story body.",
            "snippet": "Demo story body.",
        }
    )
    live_store.save(live_doc)

    try:
        monkeypatch.setattr("services.ingestion.get_settings", lambda: Settings(_env_file=None, DEMO_MODE=False))
        merged = get_merged_documents([demo_doc])
    finally:
        live_store.clear()

    assert [doc.id for doc in merged] == ["doc_live"]
