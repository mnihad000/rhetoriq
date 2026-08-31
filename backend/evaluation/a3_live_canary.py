"""Non-blocking live canaries for A3 provider and web drift detection."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from models.document import Document
from models.investigation import FinalReportClaim, FinalReportResult, FinalReportSections, InvestigationPlan, ReportCitation
from services.claim_evidence_verifier import ClaimEvidenceVerifier
from services.url_policy import PublicUrlPolicy, UrlPolicyError


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CANARIES = BASE_DIR / "fixtures" / "a3_live_canaries.json"


def run_canaries(config_path: Path = DEFAULT_CANARIES) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    results = [_run_canary(item) for item in config.get("canaries", [])]
    return {
        "suite_version": config.get("suite_version", "a3-live-canary-v1"),
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "passed": all(item["reachable"] for item in results),
        "results": results,
    }


def _run_canary(canary: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    url = canary["url"]
    try:
        PublicUrlPolicy(enforce_robots=False).validate(url)
        with httpx.Client(follow_redirects=True, timeout=20, headers={"User-Agent": "RhetoriQ-A3-Canary/1.0"}) as client:
            response = client.get(url)
        response.raise_for_status()
        text = response.text[:100_000]
        document = Document(
            id=f"canary-{canary['id']}", source_name=canary.get("source_name", url),
            source_type=canary.get("source_type", "national_news"), url=str(response.url),
            title=canary.get("title", canary["id"]), text=text, snippet=text[:300], entities=[], phrases=[],
            collected_at=datetime.now(timezone.utc), content_type=response.headers.get("content-type"),
        )
        plan = InvestigationPlan.model_validate(canary["plan"])
        claim = FinalReportClaim(
            claim_id="canary_claim", claim_text=canary["claim_text"], claim_type="observed_fact",
            citations=[ReportCitation(document_id=document.id, source_name=document.source_name, source_type=document.source_type, title=document.title, url=document.url, snippet=document.snippet, relevance_note="Live canary source.")],
        )
        report = FinalReportResult(
            investigation_id=document.id, plan_snapshot=plan, report_title="A3 live canary", report_summary="Canary.",
            sections=FinalReportSections(headline="A3 live canary", executive_summary="Canary.", observed_facts="Canary.", reasonable_inferences="", timeline_summary="", counter_narrative_summary="", limitations="", recommended_human_checks=""),
            key_claims=[claim],
        )
        verification = ClaimEvidenceVerifier().verify(document.id, plan, [document], report)
        decision = verification.records[0].disposition if verification.records else "withheld"
        return {
            "id": canary["id"], "reachable": True, "status_code": response.status_code,
            "final_url": str(response.url), "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "latency_ms": round((time.perf_counter() - started) * 1000), "verification_disposition": decision,
            "model_provenance": verification.model_provenance, "limitations": verification.limitations,
        }
    except (httpx.HTTPError, UrlPolicyError, ValueError) as exc:
        return {"id": canary["id"], "reachable": False, "latency_ms": round((time.perf_counter() - started) * 1000), "error": str(exc)[:300]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run non-blocking A3 live canaries.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CANARIES)
    args = parser.parse_args()
    print(json.dumps(run_canaries(args.config), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
