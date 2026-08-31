"""A3 captured-corpus release gate.

This module deliberately rejects hand-wavy metadata fixtures.  Each case must
contain public-source captures, content hashes, capture dates, and an expected
claim disposition before it can be used as a release-quality benchmark.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from models.document import Document
from models.investigation import (
    ClaimVerificationResult,
    FinalReportClaim,
    FinalReportResult,
    FinalReportSections,
    InvestigationPlan,
    ReportCitation,
)
from services.claim_evidence_verifier import ClaimEvidenceVerifier


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_FIXTURES = BASE_DIR / "fixtures" / "a3_captured_corpus.json"


def run_scorecard(fixtures_path: Path = DEFAULT_FIXTURES) -> dict[str, Any]:
    suite = json.loads(fixtures_path.read_text(encoding="utf-8"))
    fixtures = suite.get("fixtures", [])
    _validate_suite(suite, fixtures)
    results = [_evaluate_case(case) for case in fixtures]
    expected_affirmed = [result for result in results if result["expected_disposition"] == "supported"]
    affirmed_correct = [result for result in expected_affirmed if result["actual_disposition"] == "supported"]
    leakage = [result for result in results if result["expected_disposition"] in {"withheld", "contradicted"} and result["actual_disposition"] == "supported"]
    span_valid = [result for result in results if result["span_valid"]]
    duplicate_errors = [result for result in results if result["duplicate_as_independent"]]
    outcome_correct = [result for result in results if result["outcome_correct"]]
    metrics = {
        "affirmed_claim_precision": len(affirmed_correct) / max(1, len(expected_affirmed)),
        "unsupported_or_contradicted_leakage": len(leakage) / max(1, len(results)),
        "citation_span_validity": len(span_valid) / max(1, len(results)),
        "duplicate_as_independent_error_rate": len(duplicate_errors) / max(1, len(results)),
        "case_outcome_accuracy": len(outcome_correct) / max(1, len(results)),
    }
    thresholds = {
        "affirmed_claim_precision": 0.95,
        "unsupported_or_contradicted_leakage": 0.0,
        "citation_span_validity": 1.0,
        "duplicate_as_independent_error_rate": 0.0,
        "case_outcome_accuracy": 1.0,
    }
    passed = (
        metrics["affirmed_claim_precision"] >= thresholds["affirmed_claim_precision"]
        and metrics["unsupported_or_contradicted_leakage"] <= thresholds["unsupported_or_contradicted_leakage"]
        and metrics["citation_span_validity"] >= thresholds["citation_span_validity"]
        and metrics["duplicate_as_independent_error_rate"] <= thresholds["duplicate_as_independent_error_rate"]
        and metrics["case_outcome_accuracy"] >= thresholds["case_outcome_accuracy"]
    )
    return {"suite_version": suite["suite_version"], "fixture_count": len(results), "passed": passed, "metrics": metrics, "thresholds": thresholds, "results": results}


def _validate_suite(suite: dict[str, Any], fixtures: list[dict[str, Any]]) -> None:
    if suite.get("suite_version") != "a3-captured-v1":
        raise ValueError("A3 corpus must declare suite_version a3-captured-v1.")
    if len(fixtures) != 30:
        raise ValueError("A3 requires exactly 30 captured public-source cases (five per required category).")
    categories = {"origin_uncertainty", "direct_conflict", "syndicated_reporting", "sparse_evidence", "unavailable_source", "misleading_chronology"}
    counts = {category: 0 for category in categories}
    for case in fixtures:
        category = case.get("category")
        if category not in categories:
            raise ValueError(f"Unknown A3 category: {category!r}")
        counts[category] += 1
        if not case.get("public_access_confirmed") or not case.get("expert_reviewer"):
            raise ValueError(f"{case.get('id', 'unknown')} lacks public-access or reviewer provenance.")
        captures = case.get("captures") or []
        if not captures:
            raise ValueError(f"{case.get('id', 'unknown')} has no captured source material.")
        for capture in captures:
            required = {"url", "captured_at", "content_sha256", "document"}
            if not required.issubset(capture):
                raise ValueError(f"{case.get('id', 'unknown')} has an incomplete capture record.")
            text = capture["document"].get("text", "")
            if hashlib.sha256(text.encode("utf-8")).hexdigest() != capture["content_sha256"]:
                raise ValueError(f"{case.get('id', 'unknown')} has a capture hash mismatch.")
    if any(count != 5 for count in counts.values()):
        raise ValueError("A3 requires five real captures in each category.")


def _evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    documents = [Document.model_validate(item["document"]) for item in case["captures"]]
    claim = case["claim"]
    plan = InvestigationPlan.model_validate(case["plan"])
    citations = [
        ReportCitation(
            document_id=document.id, source_name=document.source_name, source_type=document.source_type,
            title=document.title, url=document.url, published_at=document.published_at,
            snippet=document.snippet, relevance_note="Captured A3 evaluation evidence.",
        ) for document in documents
    ]
    report = FinalReportResult(
        investigation_id=case["id"], plan_snapshot=plan, report_title="A3 capture evaluation", report_summary="Candidate.",
        sections=FinalReportSections(headline="A3 capture evaluation", executive_summary="Candidate.", observed_facts="Candidate.", reasonable_inferences="Candidate.", timeline_summary="Candidate.", counter_narrative_summary="Candidate.", limitations="", recommended_human_checks=""),
        key_claims=[FinalReportClaim(claim_id=claim["id"], claim_text=claim["text"], claim_type=claim["claim_type"], citations=citations)],
    )
    result: ClaimVerificationResult = ClaimEvidenceVerifier().verify(case["id"], plan, documents, report)
    record = result.records[0]
    spans = [*record.supporting_evidence, *record.contradicting_evidence]
    span_valid = all(
        document := next((item for item in documents if item.id == span.document_id), None)
        and document.text[span.span_start:span.span_end] == span.evidence_span
        for span in spans
    )
    groups = [item.source_intelligence.independence_group for item in record.supporting_evidence]
    duplicate_as_independent = len(groups) != len(set(groups)) and record.disposition == "supported" and case.get("expect_single_independence_group", False)
    expected = claim["expected_disposition"]
    return {
        "id": case["id"], "category": case["category"], "expected_disposition": expected,
        "actual_disposition": record.disposition, "outcome_correct": record.disposition == expected,
        "span_valid": bool(span_valid), "duplicate_as_independent": duplicate_as_independent,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the A3 captured-corpus verification scorecard.")
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    args = parser.parse_args()
    print(json.dumps(run_scorecard(args.fixtures), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
