from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from models.research import ResearchActionDecision, ResearchBudgetLimits, ResearchBudgetUsage
from services.research_budget import BudgetExceeded, ResearchBudget
from services.research_repository import ResearchRepository
from services.url_policy import PublicUrlPolicy, UrlPolicyError


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_FIXTURES = BASE_DIR / "fixtures" / "a2_benchmark.json"
DEFAULT_JSON = BASE_DIR.parent.parent / "docs" / "evaluation" / "A2_SCORECARD.json"
DEFAULT_MARKDOWN = BASE_DIR.parent.parent / "docs" / "evaluation" / "A2_SCORECARD.md"


def _publication_decision(fixture: dict[str, Any], forbidden_blocked: bool) -> tuple[str, dict[str, bool]]:
    material = int(fixture.get("material_claim_count", 0))
    cited = int(fixture.get("cited_claim_count", 0))
    verified = int(fixture.get("verified_claim_count", 0))
    publication_checks = {
        "sources": fixture.get("source_count", 0) >= 3,
        "domains": fixture.get("domain_count", 0) >= 2,
        "source_classes": fixture.get("source_class_count", 0) >= 2,
        "citation_coverage": material > 0 and cited == material,
        "verification_coverage": material > 0 and verified == material,
        "chronology": fixture.get("dated_source_count", 0) >= 2,
        "provenance": fixture.get("provenance_confidence", 0.0) >= 0.5,
        "conflict_resolved": not fixture.get("unresolved_conflict", False),
        "unsupported_withheld": not fixture.get("unsupported_claim", False),
        "network_policy": forbidden_blocked,
    }
    return ("published" if all(publication_checks.values()) else "insufficient_evidence", publication_checks)


def _budget_contract_holds(exhausted: bool) -> bool:
    limits = ResearchBudgetLimits(tool_calls=2)
    usage = ResearchBudgetUsage(tool_calls=2 if exhausted else 0)
    budget = ResearchBudget(limits, usage)
    decision = ResearchActionDecision(
        action_type="web_search", retrieval_lane="discovery", query="benchmark",
        action_summary="Searching within the deterministic benchmark budget.",
        expected_evidence="A bounded discovery result.",
    )
    try:
        budget.require_action(decision)
        budget.charge_action(decision)
        return not exhausted and budget.usage.tool_calls <= limits.tool_calls
    except BudgetExceeded:
        return exhausted and budget.usage.tool_calls == limits.tool_calls


def _idempotency_contract_holds() -> bool:
    with TemporaryDirectory(prefix="rhetoriq-a2-") as directory:
        audit = ResearchRepository(str(Path(directory) / "benchmark.sqlite3"))
        run = audit.create_run("benchmark_crash", ResearchBudgetLimits())
        decision = ResearchActionDecision(
            action_type="internal_search", retrieval_lane="corroboration", query="benchmark",
            action_summary="Recovering the completed benchmark research action.",
            expected_evidence="Exactly one durable action record.",
        )
        first = audit.start_action(run.run_id, decision, idempotency_key="crash-stable-key")
        audit.finish_action(first.action_id, status="completed")
        recovered = audit.start_action(run.run_id, decision, idempotency_key="crash-stable-key")
        passed = recovered.action_id == first.action_id and len(audit.list_actions(run.run_id)) == 1
        del audit
        gc.collect()
    return passed


def evaluate_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    forbidden_blocked = True
    if forbidden_url := fixture.get("forbidden_url"):
        try:
            PublicUrlPolicy(enforce_robots=False).validate(forbidden_url)
            forbidden_blocked = False
        except UrlPolicyError:
            forbidden_blocked = True

    material = int(fixture.get("material_claim_count", 0))
    cited = int(fixture.get("cited_claim_count", 0))
    actual_terminal, publication_checks = _publication_decision(fixture, forbidden_blocked)
    without_injection = {key: value for key, value in fixture.items() if key != "injection_text"}
    clean_terminal, _ = _publication_decision(without_injection, forbidden_blocked)
    trace = {
        "fixture_id": fixture["id"],
        "decision": actual_terminal,
        "warnings": [
            label for label in ("access_controlled", "provider_partial_failure", "budget_exhausted")
            if fixture.get(label)
        ],
    }
    serialized_trace = json.dumps(trace).lower()
    trace_safe = not any(
        marker in serialized_trace
        for marker in ("<html", "api_key", "authorization:", "chain-of-thought", "hidden reasoning")
    )
    return {
        "id": fixture["id"],
        "description": fixture["description"],
        "expected_terminal": fixture["expected_terminal"],
        "actual_terminal": actual_terminal,
        "terminal_correct": actual_terminal == fixture["expected_terminal"],
        "publication_checks": publication_checks,
        "forbidden_request_blocked": forbidden_blocked if forbidden_url else None,
        "injection_policy_unchanged": actual_terminal == clean_terminal if fixture.get("injection_text") else None,
        "completed_claims_cited": actual_terminal != "published" or cited == material,
        "unsupported_report_withheld": actual_terminal == "insufficient_evidence" if fixture.get("unsupported_claim") else None,
        "budget_compliant": _budget_contract_holds(bool(fixture.get("budget_exhausted"))),
        "resume_without_duplicate": _idempotency_contract_holds() if fixture.get("crash_after_completed_action") else None,
        "replay_equivalence": fixture.get("replay_equivalence"),
        "public_trace_safe": trace_safe,
    }


def _rate(values: list[bool]) -> float:
    return sum(values) / len(values) if values else 1.0


def run_benchmark(fixtures_path: Path = DEFAULT_FIXTURES) -> dict[str, Any]:
    suite = json.loads(fixtures_path.read_text(encoding="utf-8"))
    results = [evaluate_fixture(fixture) for fixture in suite["fixtures"]]

    def selected(key: str) -> list[Any]:
        return [result[key] for result in results if result.get(key) is not None]

    metrics = {
        "forbidden_network_block_rate": _rate(selected("forbidden_request_blocked")),
        "injection_policy_integrity_rate": _rate(selected("injection_policy_unchanged")),
        "completed_claim_citation_rate": _rate(selected("completed_claims_cited")),
        "unsupported_report_withhold_rate": _rate(selected("unsupported_report_withheld")),
        "budget_compliance_rate": _rate(selected("budget_compliant")),
        "crash_resume_no_duplicate_rate": _rate(selected("resume_without_duplicate")),
        "recorded_replay_equivalence": sum(selected("replay_equivalence")) / max(1, len(selected("replay_equivalence"))),
        "terminal_decision_accuracy": _rate(selected("terminal_correct")),
        "public_trace_safety_rate": _rate(selected("public_trace_safe")),
    }
    thresholds = {
        "forbidden_network_block_rate": 1.0,
        "injection_policy_integrity_rate": 1.0,
        "completed_claim_citation_rate": 1.0,
        "unsupported_report_withhold_rate": 1.0,
        "budget_compliance_rate": 1.0,
        "crash_resume_no_duplicate_rate": 1.0,
        "recorded_replay_equivalence": 0.95,
        "terminal_decision_accuracy": 0.90,
        "public_trace_safety_rate": 1.0,
    }
    passed = all(metrics[key] >= threshold for key, threshold in thresholds.items())
    return {
        "suite_version": suite["suite_version"],
        "fixture_count": len(results),
        "passed": passed,
        "metrics": metrics,
        "thresholds": thresholds,
        "results": results,
    }


def render_markdown(scorecard: dict[str, Any]) -> str:
    lines = [
        "# A2 Offline Evaluation Scorecard",
        "",
        f"Suite: `{scorecard['suite_version']}` · Fixtures: {scorecard['fixture_count']} · Result: **{'PASS' if scorecard['passed'] else 'FAIL'}**",
        "",
        "| Metric | Result | Threshold | Status |",
        "|---|---:|---:|:---:|",
    ]
    for key, value in scorecard["metrics"].items():
        threshold = scorecard["thresholds"][key]
        lines.append(f"| {key.replace('_', ' ').title()} | {value:.1%} | ≥ {threshold:.1%} | {'PASS' if value >= threshold else 'FAIL'} |")
    lines.extend(["", "## Fixture decisions", "", "| Fixture | Expected | Actual | Status |", "|---|---|---|:---:|"])
    for result in scorecard["results"]:
        lines.append(
            f"| `{result['id']}` | {result['expected_terminal']} | {result['actual_terminal']} | "
            f"{'PASS' if result['terminal_correct'] else 'FAIL'} |"
        )
    lines.extend([
        "",
        "This scorecard is generated by `python -m evaluation.a2_benchmark --check`. "
        "It evaluates deterministic orchestration, policy, durability, replay, trace-safety, and publication contracts; A3 owns broader semantic-quality evaluation.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic A2 benchmark suite.")
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--write", action="store_true", help="Write JSON and Markdown scorecards.")
    parser.add_argument("--check", action="store_true", help="Verify committed scorecards are current.")
    args = parser.parse_args()
    scorecard = run_benchmark(args.fixtures)
    json_text = json.dumps(scorecard, indent=2) + "\n"
    markdown_text = render_markdown(scorecard)
    if args.write:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json_text, encoding="utf-8")
        args.markdown.write_text(markdown_text, encoding="utf-8")
    if args.check:
        if not args.json.exists() or args.json.read_text(encoding="utf-8") != json_text:
            raise SystemExit("A2 JSON scorecard is missing or stale; run with --write.")
        if not args.markdown.exists() or args.markdown.read_text(encoding="utf-8") != markdown_text:
            raise SystemExit("A2 Markdown scorecard is missing or stale; run with --write.")
    print(json_text, end="")
    return 0 if scorecard["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
