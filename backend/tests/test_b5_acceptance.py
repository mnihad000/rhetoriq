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
