import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from events.b5_acceptance import (
    BASE_TEXT,
    LOAD_SEED_COUNT,
    acceptance_plan,
    build_investigation_workspace,
    build_seed_documents,
    persist_acceptance_investigation,
    percentile,
    scheduled_offsets,
    validate_disposable_settings,
)


def test_persisted_probe_workspace_has_terminal_snapshot_and_attached_documents(tmp_path):
    from services.b5_repository import ProjectionRepository

    repository = ProjectionRepository(str(tmp_path / "b5.sqlite3"))
    documents = build_seed_documents("run", 3)
    for document in documents:
        repository.record_document(document, source_event_id=f"seed:{document.id}")
    result = persist_acceptance_investigation(repository, "run", documents)
    snapshot = result["snapshot"]
    assert snapshot["kind"] == "investigation"
    assert snapshot["eligible"] is True
    assert snapshot["data"]["document_snapshot_ids"]
    assert result["workspace"].report is not None


def test_load_plan_preserves_required_seed_and_pacing_windows():
    plan = acceptance_plan("load")
    assert plan.seed_documents == LOAD_SEED_COUNT == 10_000
    assert [(phase.count, phase.rate_per_minute) for phase in plan.phases] == [(3_000, 100), (2_500, 500)]
    assert plan.paced_documents == 5_500
    assert plan.backlog_documents == 4_500
    assert plan.drain_seconds == 600


def test_schedule_and_percentile_are_deterministic():
    assert scheduled_offsets(3, 120) == [0.0, 0.5, 1.0]
    assert percentile([10, 20, 30, 40], 95) == pytest.approx(38.5)
    assert percentile([], 95) is None


def test_seed_documents_are_distinct_citable_and_have_valid_reference_span():
    documents = build_seed_documents("run", 12)
    assert len({document.id for document in documents}) == 12
    assert all(BASE_TEXT in document.text for document in documents)
    assert all(document.metadata["acquisition_receipt_valid"] is True for document in documents)
    assert all(document.text[reference.start:reference.end] == reference.anchor_text for document in documents for reference in document.references)


def test_investigation_workspace_attaches_seeded_documents_and_timeline():
    documents = build_seed_documents("run", 3)
    workspace = build_investigation_workspace("run", documents)
    assert workspace["investigation_id"] == "run:investigation"
    assert [item["id"] for item in workspace["retrieved_documents"]] == [item.id for item in documents]
    assert [item["document_id"] for item in workspace["timeline"]["timeline_events"]] == [item.id for item in documents]


def test_disposable_gate_rejects_non_postgres_or_external_broker():
    settings = SimpleNamespace(DATABASE_URL="postgresql://u:p@localhost:5432/rq", KAFKA_BOOTSTRAP_SERVERS="localhost:9092", EMBEDDING_LOCAL_ONLY=True, ENABLE_B5_RETRIEVAL=True)
    errors = validate_disposable_settings(settings, disposable_stack=True)
    assert "DATABASE_URL must target the disposable postgres service" in errors
    assert "KAFKA_BOOTSTRAP_SERVERS must target the disposable broker service" in errors


def test_delivery_measurements_reject_old_revision_and_exclude_warmup(tmp_path):
    from events.b5_acceptance import _Latency, _delivery_integrity, _sample_deliveries
    from services.b5_repository import ProjectionRepository

    repository = ProjectionRepository(str(tmp_path / "b5.sqlite3"))
    documents = build_seed_documents("run", 2)
    first = repository.record_document(documents[0], source_event_id="first")
    second = repository.record_document(documents[1], source_event_id="second")
    for target in ("elasticsearch", "neo4j", "minilm"):
        repository.mark_delivery(target, first)
        repository.mark_delivery(target, second)
    latency, seen = _Latency(), set()
    rows = repository.delivery_observations("live")
    _sample_deliveries(rows, {second["domain_id"]}, latency, seen)
    _sample_deliveries(rows, {second["domain_id"]}, latency, seen)
    assert latency.report()["target_freshness_ms"]["count"] == 3
    assert _delivery_integrity(repository, {first["domain_id"], second["domain_id"]}, "live")["all_targets_applied"]
    repository.withdraw(second["domain_id"], operation_id="withdraw", reason="qualification check")
    assert not _delivery_integrity(repository, {second["domain_id"]}, "live")["all_targets_applied"]


def test_backlog_qualification_requires_samples_and_detects_sustained_growth():
    from events.b5_acceptance import _sustained_backlog_growth
    window = {"started_elapsed_seconds": 0, "duration_seconds": 300, "rate_per_minute": 100}
    def samples(counts):
        return [{"elapsed_seconds": index * 60 + 1, "pending": dict.fromkeys(("elasticsearch", "neo4j", "minilm"), count)} for index, count in enumerate(counts)]
    assert _sustained_backlog_growth([], [window]) is None
    assert _sustained_backlog_growth(samples([0, 30, 60, 90, 120]), [window]) is True
    assert _sustained_backlog_growth(samples([3, 4, 1, 4, 0]), [window]) is False


def test_host_qualification_rejects_missing_resources_and_shortened_windows():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("b5_qualify", Path(__file__).resolve().parents[2] / "infra/b5/qualify.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    samples = [{"memory_bytes": 13_000_000_000, "unexpected_restarts": 0}] * 2
    assert module.resource_checks(samples, oom_events=0, machine={"NCPU": 8, "MemTotal": 16_000_000_000})
    assert not module.resource_checks(samples, oom_events=None, machine={"NCPU": 8, "MemTotal": 16_000_000_000})
    assert not module.resource_checks(samples, oom_events=0, machine={"NCPU": 16, "MemTotal": 16_000_000_000})
    integrity = dict.fromkeys(("no_lost_canonical_documents", "backlog_drained", "freshness_samples_complete", "raw_to_search_samples_complete", "latency_thresholds_met", "cache_metrics_complete", "targets_match_canonical"), True)
    integrity.update(duplicate_semantic_relations=False, sustained_backlog_growth=False, broker_dlq_total=0)
    report = {"mode": "load", "status": "passed", "integrity": integrity, "seed": {"requested": 10_000}, "health": {"flink": {"status": "healthy", "checkpoint_health": {"status": "healthy"}}},
              "runtime": {"concurrent_query_clients": 10}, "phase_windows": [{"count": 3000, "rate_per_minute": 100, "duration_seconds": 1800}, {"count": 2500, "rate_per_minute": 500, "duration_seconds": 300}]}
    assert module.qualifies(report, True)
    assert not module.qualifies(report, False)
    report["phase_windows"][0]["duration_seconds"] = 10
    assert not module.qualifies(report, True)
