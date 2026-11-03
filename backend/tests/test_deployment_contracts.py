from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from api.health import readiness_report
from migrations import runner


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class _Cursor:
    def __init__(self, migrations: list[str]) -> None:
        self.migrations = migrations
        self.statements: list[str] = []
        self._query = ""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, query, _params=None):
        self._query = str(query)
        self.statements.append(self._query)

    def fetchone(self):
        return ("schema_migrations",)

    def fetchall(self):
        return [(name,) for name in self.migrations]


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def cursor(self):
        return self._cursor


def test_verify_migrations_is_read_only(monkeypatch):
    cursor = _Cursor(runner._migration_names())
    fake_psycopg = SimpleNamespace(
        connect=lambda _url, autocommit: _Connection(cursor) if autocommit is True else None
    )
    monkeypatch.setitem(__import__("sys").modules, "psycopg", fake_psycopg)

    runner.verify_migrations("postgresql://deployment.invalid/rhetoriq")

    assert cursor.statements
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in cursor.statements)


def test_verify_mode_dispatches_without_apply(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(runner, "verify_migrations", calls.append)
    runner.run_migrations.cache_clear()

    runner.run_migrations("postgresql://deployment.invalid/rhetoriq", mode="verify")

    assert calls == ["postgresql://deployment.invalid/rhetoriq"]
    runner.run_migrations.cache_clear()


def test_readiness_requires_internal_dependencies_only(monkeypatch):
    settings = SimpleNamespace(
        DATABASE_URL="postgresql://deployment.invalid/rhetoriq",
        persistence_target="postgresql://deployment.invalid/rhetoriq",
        ENABLE_B5_RETRIEVAL=True,
        REDIS_URL="rediss://redis:6379/0",
        REDIS_PASSWORD="unused-test-value",
        REDIS_CA_CERT="/ca.crt",
        B5_CACHE_TTL_SECONDS=30,
        B5_CACHE_MAX_BYTES=262144,
    )

    class _Queue:
        def __init__(self, *_args, **_kwargs):
            pass

        def health(self):
            return {"status": "ready"}

    class _Cache:
        available = True

        def __init__(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr("migrations.runner.verify_migrations", lambda _url: None)
    monkeypatch.setattr("services.job_queue.JobQueue", _Queue)
    monkeypatch.setattr("events.b5_init.check", lambda: None)
    monkeypatch.setattr("services.b5_cache.B5Cache", _Cache)

    report = readiness_report(settings)

    assert report["status"] == "ready"
    assert set(report["dependencies"]) == {"postgresql", "jobs", "b5"}


def test_readiness_failure_is_sanitized(monkeypatch):
    settings = SimpleNamespace(DATABASE_URL="bad", persistence_target="bad", ENABLE_B5_RETRIEVAL=False)
    monkeypatch.setattr(
        "migrations.runner.verify_migrations",
        lambda _url: (_ for _ in ()).throw(RuntimeError("password=never-report-this")),
    )
    monkeypatch.setattr(
        "services.job_queue.JobQueue",
        lambda *_args, **_kwargs: SimpleNamespace(health=lambda: {"status": "ready"}),
    )

    report = readiness_report(settings)

    assert report["status"] == "unavailable"
    assert report["dependencies"]["postgresql"] == {
        "status": "unavailable",
        "reason": "RuntimeError",
    }
    assert "never-report-this" not in str(report)


def test_b5_check_requires_initialized_generation(monkeypatch):
    from events import b5_init

    checked: list[tuple[str, str]] = []

    class _Repository:
        def __init__(self, _target):
            pass

        def manifest(self):
            return {"generation": "live"}

    def adapter(name):
        class _Adapter:
            def __init__(self, _settings):
                pass

            def health(self):
                return {"status": "healthy"}

            def initialized(self, generation):
                checked.append((name, generation))
                return True

            def close(self):
                pass

        return _Adapter

    monkeypatch.setattr(b5_init, "get_settings", lambda: SimpleNamespace(persistence_target="unused"))
    monkeypatch.setattr(b5_init, "ProjectionRepository", _Repository)
    monkeypatch.setattr(b5_init, "ElasticsearchTarget", adapter("elasticsearch"))
    monkeypatch.setattr(b5_init, "Neo4jTarget", adapter("neo4j"))

    b5_init.check()

    assert checked == [("elasticsearch", "live"), ("neo4j", "live")]
