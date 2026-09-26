from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from api.health import readiness_report
from events.b6_evidence import _sanitize
from events.flink_savepoint import create_savepoint
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
        ENABLE_B5_RETRIEVAL=True,
        REDIS_URL="rediss://redis:6379/0",
        REDIS_PASSWORD="unused-test-value",
        REDIS_CA_CERT="/ca.crt",
        B5_CACHE_TTL_SECONDS=30,
        B5_CACHE_MAX_BYTES=262144,
    )

    class _Publisher:
        def __init__(self, **_kwargs):
            pass

        def health(self):
            return {"status": "ready"}

    class _Cache:
        available = True

        def __init__(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr("migrations.runner.verify_migrations", lambda _url: None)
    monkeypatch.setattr("services.kafka_runtime.KafkaEventPublisher", _Publisher)
    monkeypatch.setattr("events.topics.check_topics", lambda: None)
    monkeypatch.setattr("events.b5_init.check", lambda: None)
    monkeypatch.setattr("services.b5_cache.B5Cache", _Cache)

    report = readiness_report(settings)

    assert report["status"] == "ready"
    assert set(report["dependencies"]) == {"postgresql", "kafka", "topics_and_schemas", "b5"}


def test_readiness_failure_is_sanitized(monkeypatch):
    settings = SimpleNamespace(DATABASE_URL="bad", ENABLE_B5_RETRIEVAL=False)
    monkeypatch.setattr(
        "migrations.runner.verify_migrations",
        lambda _url: (_ for _ in ()).throw(RuntimeError("password=never-report-this")),
    )
    monkeypatch.setattr(
        "services.kafka_runtime.KafkaEventPublisher",
        lambda **_kwargs: SimpleNamespace(health=lambda: {"status": "ready"}),
    )
    monkeypatch.setattr("events.topics.check_topics", lambda: None)

    report = readiness_report(settings)

    assert report["status"] == "unavailable"
    assert report["dependencies"]["postgresql"] == {
        "status": "unavailable",
        "reason": "RuntimeError",
    }
    assert "never-report-this" not in str(report)


def test_evidence_sanitizer_removes_sensitive_and_error_fields():
    sanitized = _sanitize(
        {"status": "ready", "password": "hidden", "nested": [{"error": "hidden", "count": 2}]}
    )
    assert sanitized == {"status": "ready", "nested": [{"count": 2}]}


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


def test_flink_savepoint_requires_completed_location(monkeypatch):
    responses = iter(
        [
            SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"jobs": [{"jid": "job-1", "state": "RUNNING"}]}),
            SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"request-id": "request-1"}),
            SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"status": {"id": "IN_PROGRESS"}}),
            SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"status": {"id": "COMPLETED"}, "operation": {"location": "file:///savepoints/sp-1"}},
            ),
        ]
    )

    class _Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, _url):
            return next(responses)

        def post(self, _url, **_kwargs):
            return next(responses)

    monkeypatch.setattr("events.flink_savepoint.httpx.Client", _Client)
    monkeypatch.setattr("events.flink_savepoint.time.sleep", lambda _seconds: None)

    assert create_savepoint("http://flink", "file:///savepoints") == "file:///savepoints/sp-1"


def test_flink_connector_source_pin_matches_runtime_evidence():
    runtime_lock = json.loads(
        (REPOSITORY_ROOT / "infra/b5/runtime-lock.json").read_text(encoding="utf-8")
    )
    dockerfile = (REPOSITORY_ROOT / "infra/flink/Dockerfile").read_text(encoding="utf-8")
    compatibility = (
        REPOSITORY_ROOT / "infra/flink/connector-compatibility.txt"
    ).read_text(encoding="utf-8")

    commit = runtime_lock["flink_kafka_connector_source_commit"]
    checksum = runtime_lock["flink_kafka_connector_source_sha256"]
    assert f"ARG KAFKA_CONNECTOR_SOURCE_COMMIT={commit}" in dockerfile
    assert f"ARG KAFKA_CONNECTOR_SOURCE_SHA256={checksum}" in dockerfile
    assert commit in compatibility
    assert checksum in compatibility


def test_flink_job_uses_the_pinned_flink_2_3_checkpoint_api():
    source = (REPOSITORY_ROOT / "backend/flink/job.py").read_text(encoding="utf-8")

    assert "ExternalizedCheckpointRetention.RETAIN_ON_CANCELLATION" in source
    assert ".set_externalized_checkpoint_retention(" in source
    assert "runtime_configuration.set_string(" in source
    assert '"execution.checkpointing.dir"' in source
    assert ".set_checkpoint_storage(" not in source
    assert ".enable_externalized_checkpoints(" not in source
    assert "ExternalizedCheckpointCleanup" not in source
    assert "StateTtlConfig.new_builder(Time.days(14))" in source
    assert "StateTtlConfig.new_builder(Duration.of_days(14))" not in source

    compose = (REPOSITORY_ROOT / "compose.yml").read_text(encoding="utf-8")
    assert "execution.checkpointing.dir: file:///opt/flink/checkpoints" in compose
    assert "state.checkpoints.dir:" not in compose
