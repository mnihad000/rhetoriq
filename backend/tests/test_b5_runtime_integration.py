import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from events.b5_acceptance import run_acceptance


def test_actual_stack_harness_is_skipped_without_explicit_runtime_gate(tmp_path, monkeypatch):
    monkeypatch.delenv("B5_RUNTIME_TESTS", raising=False)
    report = run_acceptance(mode="smoke", disposable_stack=False, output=str(tmp_path / "b5-report.json"))
    assert report["status"] == "skipped"
    assert report["qualified"] is False
    assert report["runtime"]["executed"] is False
    assert (tmp_path / "b5-report.json").exists()


def test_runtime_failure_is_not_claimed_as_qualification_when_gate_is_enabled(monkeypatch):
    # The explicit gate is intentionally not enough to make a host stack safe;
    # settings still need disposable postgres/broker endpoints and local model
    # configuration.  This test exercises the honest blocked result without
    # opening Kafka or target clients.
    monkeypatch.setenv("B5_RUNTIME_TESTS", "true")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    report = run_acceptance(mode="smoke", disposable_stack=True)
    assert report["status"] in {"blocked", "failed"}
    assert report["qualified"] is False


@pytest.mark.skipif(os.getenv("B5_RUNTIME_TESTS", "false").lower() not in {"1", "true", "yes"}, reason="requires explicit disposable B5 runtime gate")
def test_disposable_runtime_smoke_when_explicitly_enabled():
    report = run_acceptance(mode="smoke", disposable_stack=True)
    # A real disposable runtime must complete the smoke probes.  Smoke is a
    # bounded health run and therefore never claims the 100k/day load
    # qualification even when every probe passes.
    assert report["status"] == "passed"
    assert report["qualified"] is False
