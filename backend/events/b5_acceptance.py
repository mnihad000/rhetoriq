"""B5 disposable-stack acceptance harness.

The offline tests exercise the deterministic plan and report helpers.  The
actual Kafka/Flink/Elasticsearch/Neo4j/PostgreSQL run is opt-in because it
publishes thousands of records and must never point at a developer's normal
database or broker.  Enable it with ``B5_RUNTIME_TESTS=true`` and the
required ``--disposable-stack`` acknowledgement.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import statistics
import shutil
import subprocess
import threading
import time
from typing import Any, Iterable
from urllib.parse import urlparse
from uuid import uuid4

from models.document import Document, SourceReference


BASE_TEXT = "Public records policy changes today."
RECORDED_PROVIDER_FIXTURE = Path(os.getenv("B5_RECORDED_PROVIDER_FIXTURE",str(Path(__file__).resolve().parents[2] / "infra" / "flink" / "recorded-provider.json")))
LOAD_SEED_COUNT = 10_000
LOAD_PHASES = ((3_000, 100, "initial_100_per_minute"), (2_500, 500, "burst_500_per_minute"))
SMOKE_PHASES = ((18, 120, "smoke"),)
MAX_QUERY_CLIENTS = 10
DEFAULT_DRAIN_SECONDS = 600


@dataclass(frozen=True)
class Phase:
    count: int
    rate_per_minute: int
    name: str

    @property
    def duration_seconds(self) -> float:
        return (self.count / self.rate_per_minute) * 60.0


@dataclass(frozen=True)
class AcceptancePlan:
    mode: str
    seed_documents: int
    phases: tuple[Phase, ...]
    drain_seconds: int
    query_clients: int = MAX_QUERY_CLIENTS

    @property
    def paced_documents(self) -> int:
        return sum(phase.count for phase in self.phases)

    @property
    def backlog_documents(self) -> int:
        return max(0, self.seed_documents - self.paced_documents)


def acceptance_plan(mode: str = "smoke", *, drain_seconds: int | None = None) -> AcceptancePlan:
    mode = str(mode).lower()
    if mode not in {"smoke", "load"}:
        raise ValueError("mode must be smoke or load")
    phases = tuple(Phase(*item) for item in (SMOKE_PHASES if mode == "smoke" else LOAD_PHASES))
    return AcceptancePlan(mode=mode, seed_documents=20 if mode == "smoke" else LOAD_SEED_COUNT, phases=phases, drain_seconds=drain_seconds if drain_seconds is not None else (30 if mode == "smoke" else DEFAULT_DRAIN_SECONDS))


def scheduled_offsets(count: int, rate_per_minute: int) -> list[float]:
    """Return deterministic monotonic offsets for a paced phase."""

    if count < 0 or rate_per_minute <= 0:
        raise ValueError("count must be non-negative and rate must be positive")
    interval = 60.0 / rate_per_minute
    return [index * interval for index in range(count)]


def percentile(values: Iterable[float], percentile_value: float) -> float | None:
    values = sorted(float(value) for value in values)
    if not values:
        return None
    if not 0 <= percentile_value <= 100:
        raise ValueError("percentile must be between 0 and 100")
    if len(values) == 1:
        return values[0]
    rank = (len(values) - 1) * percentile_value / 100.0
    lower = int(rank)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (rank - lower)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_seed_document(run_id: str, index: int, *, now: datetime | None = None) -> Document:
    """Create one distinct, citable raw input for the recorded stack."""

    now = now or _utc_now()
    domain = f"acceptance-publisher-{index % 20}.example"
    unique = f"Acceptance record {run_id} {index}."
    text = f"{BASE_TEXT} {unique}"
    anchor = "Public records policy"
    target_index = 1 if index == 0 else index - 1
    target_url = f"https://acceptance-publisher-{target_index % 20}.example/records/{run_id}/{target_index}"
    return Document(
        id=f"{run_id}:document:{index}",
        source_id=f"{domain}:record:{index}",
        source_name=domain,
        source_type="government_record" if index % 2 else "national_news",
        url=f"https://{domain}/records/{run_id}/{index}",
        title=f"Public records policy {index}",
        published_at=now + timedelta(seconds=index),
        collected_at=now + timedelta(seconds=index),
        text=text,
        snippet=text,
        language="en",
        content_type="article",
        geographic_scope="national",
        entities=["public records"],
        phrases=["public records policy"],
        metadata={
            "provider": "federal_register",
            "source_policy": {"decision": "allow", "citable": True},
            "evidence_status": "canonical_evidence",
            "acquisition_receipt_valid": True,
            "acceptance_run_id": run_id,
        },
        references=[SourceReference(target_url=target_url, anchor_text=anchor, context=text, start=0, end=len(anchor), extraction_version="b5-acceptance-v1", reference_kind="hyperlink")],
    )


def build_seed_documents(run_id: str, count: int, *, now: datetime | None = None) -> list[Document]:
    if count < 0 or count > LOAD_SEED_COUNT:
        raise ValueError(f"count must be between 0 and {LOAD_SEED_COUNT}")
    return [build_seed_document(run_id, index, now=now) for index in range(count)]


def build_investigation_workspace(run_id: str, documents: list[Document], *, now: datetime | None = None) -> dict[str, Any]:
    """Build the persisted workspace payload used by graph/path probes."""

    now = now or _utc_now()
    investigation_id = f"{run_id}:investigation"
    timeline = [
        {"id": f"acceptance-event:{document.id}", "document_id": document.id, "timestamp": (document.published_at or now).isoformat(), "source_name": document.source_name, "source_type": document.source_type, "title": document.title, "url": document.url, "event_type": "first_observed" if index == 0 else "early_amplification", "narrative_side": "main", "importance_score": 0.5, "explanation": "Recorded acceptance timeline event."}
        for index, document in enumerate(documents[:200])
    ]
    return {
        "investigation_id": investigation_id,
        "query_text": "public records policy",
        "status": "retrieval_completed",
        "current_stage": "retriever",
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "retrieved_documents": [document.model_dump(mode="json") for document in documents],
        "timeline": {"investigation_id": investigation_id, "timeline_events": timeline, "timeline_summary": "Recorded B5 acceptance timeline.", "limitations": []},
    }


def persist_acceptance_investigation(repository: Any, run_id: str, documents: list[Document], *, now: datetime | None = None) -> dict[str, Any]:
    """Persist a valid plan/retrieval/timeline workspace and terminal snapshot."""

    from models.investigation import (
        CoverageSummary,
        FinalReportResult,
        FinalReportSections,
        InvestigationPlan,
        RetrievalDocumentAnnotation,
        RetrievalResult,
        TimelineEvent,
        TimelineResult,
    )
    from services.investigation_repository import InvestigationRepository

    now = now or _utc_now()
    investigation_id = f"{run_id}:investigation"
    plan = InvestigationPlan(query_text="public records policy", topic="public records policy", canonical_phrase="public records policy", intent="spread")
    annotations = [RetrievalDocumentAnnotation(document_id=document.id, retrieval_lane="corroboration", retrieval_query=plan.query_text, pass_number=1, relevance_score=0.8, source_uniqueness_score=0.7, primary_source_likelihood=0.9, date_confidence="high", quality_band="tier_a") for document in documents]
    retrieval = RetrievalResult(investigation_id=investigation_id, plan_snapshot=plan, retrieved_document_ids=[document.id for document in documents], high_relevance_document_ids=[document.id for document in documents], main_narrative_document_ids=[document.id for document in documents], document_annotations=annotations, coverage_summary=CoverageSummary(total_documents=len(documents), unique_sources=len({document.source_name for document in documents}), has_timeline_coverage=bool(documents), exact_phrase_hits=len(documents)), evidence_coverage_confidence="high")
    timeline_events = [TimelineEvent(id=f"acceptance-event:{document.id}", document_id=document.id, timestamp=document.published_at or now, source_name=document.source_name, source_type=document.source_type, title=document.title, url=document.url, snippet=document.snippet, event_type="first_observed" if index == 0 else "early_amplification", narrative_side="main", importance_score=0.5, explanation="Recorded B5 acceptance timeline event.") for index, document in enumerate(documents)]
    timeline = TimelineResult(investigation_id=investigation_id, plan_snapshot=plan, timeline_events=timeline_events, first_observed_doc_id=documents[0].id if documents else None, timeline_summary="Recorded B5 acceptance timeline.", confidence_score=0.8, confidence_label="high")
    report = FinalReportResult(
        investigation_id=investigation_id,
        plan_snapshot=plan,
        report_title="B5 acceptance evidence workspace",
        report_summary="Recorded canonical evidence for the B5 disposable-stack acceptance probes.",
        sections=FinalReportSections(
            headline="B5 acceptance evidence",
            executive_summary="Recorded canonical evidence for runtime probes.",
            observed_facts="Documents were accepted from the recorded provider fixture.",
            reasonable_inferences="",
            timeline_summary="Recorded B5 acceptance timeline.",
            counter_narrative_summary="",
            limitations="Acceptance run does not make analytical claims.",
            recommended_human_checks="",
        ),
        limitations=["Acceptance evidence is bounded to the seeded disposable run."],
        confidence_score=0.8,
        confidence_label="medium",
    )
    investigation_repository = InvestigationRepository(repository.target)
    investigation_repository.save_plan(investigation_id, plan.query_text, plan)
    investigation_repository.save_retrieval_result(retrieval, documents)
    investigation_repository.save_timeline_result(timeline)
    investigation_repository.save_final_report_result(report)
    workspace = investigation_repository.get_investigation_workspace(investigation_id)
    if workspace is None:
        raise RuntimeError("persisted acceptance workspace could not be reloaded")
    snapshot = repository.record_investigation(workspace, run_id=run_id, terminal_decision="published", source_event_id=f"b5-acceptance:{run_id}:terminal")
    return {"investigation_id": investigation_id, "snapshot": snapshot, "workspace": workspace}


def validate_disposable_settings(
    settings: Any, *, disposable_stack: bool, kubernetes_demo: bool = False
) -> list[str]:
    """Validate the explicit runtime boundary without exposing credentials."""

    errors: list[str] = []
    if not disposable_stack:
        errors.append("--disposable-stack is required for runtime acceptance")
    database_url = str(getattr(settings, "DATABASE_URL", ""))
    if database_url:
        if (urlparse(database_url).hostname or "").lower() != "postgres" or "test" not in urlparse(database_url).path.lower():
            errors.append("DATABASE_URL must target the disposable postgres service")
    else:
        errors.append("DATABASE_URL must be configured for the disposable postgres service")
    broker = str(getattr(settings, "KAFKA_BOOTSTRAP_SERVERS", ""))
    allowed_brokers = {"kafka"} if kubernetes_demo else {"broker"}
    if not broker or any(endpoint.split(":")[0].strip().lower() not in allowed_brokers for endpoint in broker.split(",")):
        errors.append("KAFKA_BOOTSTRAP_SERVERS must target the disposable broker service")
    for field, expected_host in (("ELASTICSEARCH_URL", "elasticsearch"), ("NEO4J_URL", "neo4j"), ("REDIS_URL", "redis")):
        value = str(getattr(settings, field, ""))
        if not value or (urlparse(value).hostname or "").lower() != expected_host:
            errors.append(f"{field} must target the disposable {expected_host} service")
    if not bool(getattr(settings, "EMBEDDING_LOCAL_ONLY", False)):
        errors.append("EMBEDDING_LOCAL_ONLY must be enabled")
    if not bool(getattr(settings, "ENABLE_B5_RETRIEVAL", False)):
        errors.append("ENABLE_B5_RETRIEVAL must be enabled")
    if not RECORDED_PROVIDER_FIXTURE.exists():
        errors.append("recorded provider fixture is missing")
    return errors


class _Latency:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.values: dict[str, list[float]] = {key: [] for key in ("raw_to_processed_ms", "raw_to_search_ms", "target_freshness_ms", "lexical_ms", "hybrid_ms", "graph_ms", "path_ms", "cache_hit_ms", "cache_miss_ms")}
        self.cache_unknown = 0

    def add(self, key: str, value_ms: float) -> None:
        with self._lock:
            self.values.setdefault(key, []).append(float(value_ms))

    def mark_cache_unknown(self) -> None:
        with self._lock:
            self.cache_unknown += 1

    def report(self) -> dict[str, Any]:
        report = {key: {"count": len(values), "p50_ms": percentile(values, 50), "p95_ms": percentile(values, 95), "max_ms": max(values) if values else None} for key, values in self.values.items()}
        report["cache_observations"] = {"unknown": self.cache_unknown, "classified": len(self.values["cache_hit_ms"]) + len(self.values["cache_miss_ms"])}
        return report


def _health_probe(settings: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    try:
        from services.kafka_runtime import kafka_consumer_health

        result["kafka"] = kafka_consumer_health(settings)
    except Exception as exc:
        result["kafka"] = {"status": "unavailable", "error_type": type(exc).__name__}
    try:
        import httpx

        flink_url = str(getattr(settings, "FLINK_REST_URL", "http://flink-jobmanager:8081")).rstrip("/")
        response = httpx.get(f"{flink_url}/jobs/overview", timeout=3)
        flink = {"status": "healthy" if response.status_code < 400 else "unhealthy", "status_code": response.status_code, "checkpoint_health": {"status": "unknown"}}
        if response.status_code < 400:
            jobs = response.json().get("jobs", []) if response.headers.get("content-type", "").startswith("application/json") else []
            checkpoint_rows = []
            for job in jobs:
                job_id = job.get("jid") or job.get("id")
                if not job_id:
                    continue
                checkpoint_response = httpx.get(f"{flink_url}/jobs/{job_id}/checkpoints", timeout=3)
                if checkpoint_response.status_code >= 400:
                    checkpoint_rows.append({"job_id": job_id, "status": "unavailable", "status_code": checkpoint_response.status_code})
                    continue
                checkpoint_payload = checkpoint_response.json()
                latest = checkpoint_payload.get("latest", {}).get("completed") or {}
                checkpoint_rows.append({"job_id": job_id, "status": "healthy", "completed_id": latest.get("id"), "completed_timestamp": latest.get("timestamp")})
            if checkpoint_rows:
                flink["checkpoint_health"] = {"status": "healthy" if all(item["status"] == "healthy" and item.get("completed_id") is not None for item in checkpoint_rows) else "unhealthy", "jobs": checkpoint_rows}
        result["flink"] = flink
    except Exception as exc:
        result["flink"] = {"status": "unavailable", "error_type": type(exc).__name__}
    return result


def _api_probe(stop: threading.Event, *, api_url: str, investigation_id: str, from_document_id: str, to_document_id: str, latency: _Latency, errors: list[str], clients: int = MAX_QUERY_CLIENTS) -> None:
    """Run bounded concurrent search/graph/path probes during ingestion."""

    try:
        import httpx
    except ImportError:
        errors.append("httpx_unavailable")
        return

    def client_loop() -> None:
        try:
            with httpx.Client(base_url=api_url.rstrip("/"), timeout=3.0) as client:
                while not stop.is_set():
                    for name, path in (
                        ("lexical_ms", f"/api/investigations/{investigation_id}/search?q=public%20records%20policy&mode=fulltext&limit=10&use_cache=false"),
                        ("hybrid_ms", f"/api/investigations/{investigation_id}/search?q=public%20records%20policy&mode=hybrid&limit=10&use_cache=false"),
                        ("graph_ms", f"/api/investigations/{investigation_id}/graph?include_inferred=true&use_cache=false"),
                        ("path_ms", f"/api/investigations/{investigation_id}/provenance-paths?from_document_id={from_document_id}&to_document_id={to_document_id}&use_cache=false"),
                    ):
                        started = time.monotonic()
                        response = client.get(path)
                        finished = time.monotonic()
                        elapsed = (finished - started) * 1000.0
                        latency.add(name, elapsed)
                        if response.status_code >= 400:
                            if len(errors) < 100:
                                errors.append(f"{name}:{response.status_code}")
                        elif len(errors) < 100:
                            payload = response.json()
                            if payload.get("fallback_active") or payload.get("pending") or not payload.get("complete"):
                                errors.append(name + ":degraded_query")
                            elif name in {"lexical_ms", "hybrid_ms"} and not payload.get("results"):
                                errors.append(name + ":seed_not_retrieved")
                            elif name == "path_ms" and not payload.get("paths"):
                                errors.append(name + ":recorded_path_missing")
                    # The second search is the cache comparison request.  The
                    # service may expose cache metadata in JSON or headers;
                    # timing is recorded either way.
                    started = time.monotonic()
                    response = client.get(f"/api/investigations/{investigation_id}/search?q=public%20records%20policy&mode=hybrid&limit=10")
                    elapsed = (time.monotonic() - started) * 1000.0
                    if response.status_code >= 400:
                        if len(errors) < 100:
                            errors.append(f"cache_search:{response.status_code}")
                    body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
                    cache_header = response.headers.get("x-b5-cache", "").lower()
                    if "cached" in body or "cache_hit" in body or cache_header in {"hit", "miss"}:
                        cache_hit = bool(body.get("cached",body.get("cache_hit"))) if "cached" in body or "cache_hit" in body else cache_header == "hit"
                        latency.add("cache_hit_ms" if cache_hit else "cache_miss_ms", elapsed)
                    else:
                        latency.mark_cache_unknown()
        except Exception as exc:
            if len(errors) < 100:
                errors.append(type(exc).__name__)

    threads = [threading.Thread(target=client_loop, name=f"b5-query-{index}", daemon=True) for index in range(max(1, min(MAX_QUERY_CLIENTS, clients)))]
    for thread in threads:
        thread.start()
    while not stop.wait(0.25):
        pass
    for thread in threads:
        thread.join(timeout=5)


def _target_probe(settings: Any, repository: Any, processed: dict[str, float], latency: _Latency, generation: str) -> dict[str, Any]:
    """Inspect configured targets using their generation-aware adapters."""

    from services.b5_targets import ElasticsearchTarget, Neo4jTarget

    try:
        expected = {str(snapshot["domain_id"]): snapshot for snapshot in repository.current_records(kind="document")}
    except Exception as exc:
        return {"status": "unavailable", "error_type": type(exc).__name__}
    result: dict[str, Any] = {}
    for name, adapter in (("elasticsearch", ElasticsearchTarget(settings)), ("neo4j", Neo4jTarget(settings))):
        try:
            inventory = adapter.inventory(generation)
            matched = 0
            for document_id, snapshot in expected.items():
                actual = inventory.get(document_id)
                if actual and (actual.get("revision"), actual.get("semantic_hash"), bool(actual.get("eligible"))) == (snapshot["revision"], snapshot["semantic_hash"], bool(snapshot["eligible"])):
                    matched += 1
            result[name] = {"status": "ready", "inventory_count": len(inventory), "canonical_documents": len(expected), "matching_documents": matched}
        except Exception as exc:
            result[name] = {"status": "unavailable", "error_type": type(exc).__name__}
        finally:
            if hasattr(adapter, "close"):
                adapter.close()
    return result


def recovery_probe(repository: Any, targets: dict[str, Any], *, generation_prefix: str = "b5-recovery") -> dict[str, Any]:
    """Rebuild two isolated generations and compare canonical inventories.

    This helper is intentionally explicit and bounded.  It is called only by
    an operator that already passed the disposable-stack gate; it never drops
    a database, deletes a volume, or changes the active generation.
    """

    snapshots = list(repository.current_records())
    if not snapshots:
        return {"status": "unavailable", "reason": "no_canonical_snapshots"}
    expected = {(snapshot["domain_id"] if snapshot["kind"] == "document" else f"investigation:{snapshot['domain_id']}"): {"revision": snapshot["revision"], "semantic_hash": snapshot["semantic_hash"], "eligible": snapshot["eligible"]} for snapshot in snapshots}
    inventories: dict[str, dict[str, Any]] = {}
    validation: dict[str, dict[str, Any]] = {}
    generations: list[str] = []
    from events.b5_ops import rebuild, reconcile
    for index in (1, 2):
        generation = f"{generation_prefix}-{index}"
        generations.append(generation)
        rebuilt = rebuild(repository, targets, generation)
        # Catch mutations after the first durable high-water mark. Reconciliation
        # must still prove a stable mark; concurrent writes cannot silently pass.
        rebuild(repository, targets, generation, after=rebuilt["next_after"])
        validation[generation] = reconcile(repository, targets, generation)
        inventories[generation] = {name: adapter.inventory(generation) for name, adapter in targets.items()}
    document_expected = {key: value for key, value in expected.items() if not key.startswith("investigation:")}
    matching = {generation: {name: all(inventory.get(key) == value for key, value in (expected.items() if name == "neo4j" else document_expected).items()) for name, inventory in by_target.items()} for generation, by_target in inventories.items()}
    withdraw_restore_checked = False
    cache_checked = False
    drift_repair_checked = False
    limitations: list[str] = []
    eligible_document = next((item for item in snapshots if item.get("kind") == "document" and item.get("eligible")), None)
    if eligible_document is not None:
        document_id = str(eligible_document["domain_id"])
        try:
            before_cache = repository.cache_token("document", document_id, "elasticsearch", generations[0])
            withdrawn = repository.withdraw(document_id, operation_id=f"{generation_prefix}:withdraw:{uuid4().hex}", reason="B5 recovery withdrawal/restore probe")
            withdrawal_ok = withdrawn.get("operation") == "withdraw" and withdrawn.get("eligible") is False
            after_cache = repository.cache_token("document", document_id, "elasticsearch", generations[0])
            restored = repository.withdraw(document_id, operation_id=f"{generation_prefix}:restore:{uuid4().hex}", reason="B5 recovery withdrawal/restore probe", restore=True)
            restore_ok = restored.get("operation") == "restore" and restored.get("eligible") is True
            withdraw_restore_checked = withdrawal_ok and restore_ok
            cache_checked = before_cache != after_cache
            for adapter in targets.values():
                adapter.repair(restored, generations[0])
            drift_repair_checked = all(not adapter.validate_snapshot(restored, generations[0]) for adapter in targets.values())
        except Exception as exc:
            limitations.append(f"Recovery mutation probe failed: {type(exc).__name__}")
            try:
                current = repository.current("document", document_id)
                if current and current.get("operation") == "withdraw":
                    repository.withdraw(document_id, operation_id=f"{generation_prefix}:cleanup:{uuid4().hex}", reason="B5 recovery probe cleanup", restore=True)
            except Exception:
                limitations.append("Recovery probe cleanup could not restore the selected document.")
    else:
        limitations.append("No eligible document was available for withdrawal/restore recovery checks.")
    equivalent = (inventories[generations[0]] == inventories[generations[1]]
                  and all(record["complete"] and not record["drift"] for record in validation.values()))
    checks_pass = equivalent and all(all(item.values()) for item in matching.values()) and withdraw_restore_checked and cache_checked and drift_repair_checked
    return {"status": "passed" if checks_pass else "failed", "generations": generations, "canonical_count": len(expected), "matching": matching, "semantic_content_equivalent": equivalent, "validation": validation, "withdraw_restore_checked": withdraw_restore_checked, "cache_checked": cache_checked, "drift_repair_checked": drift_repair_checked, "limitations": limitations}


def _sample_target_delivery(repository: Any, document_id: str, generation: str, latency: _Latency, seen: set[tuple[str, str]]) -> None:
    """Sample exact PostgreSQL snapshot-to-delivery timestamps once per target."""

    if all((target, document_id) in seen for target in ("elasticsearch", "neo4j", "minilm")):
        return
    snapshot = repository.current("document", document_id)
    if not snapshot or not snapshot.get("created_at"):
        return
    try:
        created_at = datetime.fromisoformat(str(snapshot["created_at"]).replace("Z", "+00:00"))
    except ValueError:
        return
    for target in ("elasticsearch", "neo4j", "minilm"):
        key = (target, document_id)
        if key in seen:
            continue
        delivery = repository.delivery(target, "document", document_id, generation)
        if not delivery or delivery.get("status") != "applied" or not delivery.get("applied_at"):
            continue
        try:
            applied_at = datetime.fromisoformat(str(delivery["applied_at"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        latency.add("target_freshness_ms", max(0.0, (applied_at - created_at).total_seconds() * 1000.0))
        seen.add(key)


def _delivery_integrity(repository: Any, document_ids: set[str], generation: str, *, observations: list[dict] | None = None) -> dict[str, Any]:
    counts: dict[str, int] = {}
    missing: dict[str, list[str]] = {}
    rows = repository.delivery_observations(generation) if observations is None else observations
    applied = {(row["target"], row["domain_id"]) for row in rows if _applied_observation(row)}
    for target in ("elasticsearch", "neo4j", "minilm"):
        absent = sorted(identity for identity in document_ids if (target, identity) not in applied)
        counts[target] = len(document_ids) - len(absent)
        missing[target] = absent[:100]
    return {"counts": counts, "missing": missing, "all_targets_applied": all(counts[target] == len(document_ids) for target in counts)}


def _applied_observation(row: dict) -> bool:
    return (row.get("status") == "applied" and row.get("revision") == row.get("applied_revision")
            and row.get("semantic_hash") == row.get("applied_hash"))


def _sample_deliveries(rows: list[dict], measured_ids: set[str], latency: _Latency, seen: set[tuple[str, str]]) -> None:
    for row in rows:
        key = (row.get("target"), row["domain_id"])
        if row["domain_id"] not in measured_ids or key in seen or not _applied_observation(row) or not row.get("applied_at"):
            continue
        created = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
        applied = datetime.fromisoformat(str(row["applied_at"]).replace("Z", "+00:00"))
        latency.add("target_freshness_ms", max(0.0, (applied - created).total_seconds() * 1000))
        seen.add(key)


def _sustained_backlog_growth(samples: list[dict], windows: list[dict]) -> bool | None:
    """Flag three consecutive minute increases exceeding 10% of input rate.

    Evaluate each measured rate window separately, rather than treating the
    transition to a burst as sustained growth. Preserve the raw trace in JSON.
    """
    if not samples or not windows:
        return None
    for window in windows:
        minutes = int(window["duration_seconds"] // 60)
        buckets = [[] for _ in range(minutes)]
        for sample in samples:
            index = int((sample["elapsed_seconds"] - window["started_elapsed_seconds"]) // 60)
            if 0 <= index < minutes:
                buckets[index].append(sample["pending"])
        if minutes >= 4 and any(not bucket for bucket in buckets):
            return None
        for target in ("elasticsearch", "neo4j", "minilm"):
            averages = [statistics.mean(item[target] for item in bucket) for bucket in buckets if bucket]
            growing = 0
            for before, after in zip(averages, averages[1:]):
                growing = growing + 1 if after - before > window["rate_per_minute"] * 0.1 else 0
                if growing >= 3:
                    return True
    return False


def _wait_for_canonical_documents(repository: Any, document_ids: Iterable[str], timeout: float = 180.0) -> bool:
    """Wait for the first probe corpus to be represented in B5 canonical state."""

    expected = {str(item) for item in document_ids}
    deadline = time.monotonic() + max(0.1, timeout)
    while time.monotonic() < deadline:
        present = {str(item.get("domain_id")) for item in repository.current_records(kind="document")}
        if expected.issubset(present):
            return True
        time.sleep(0.5)
    return False


def _duplicate_semantic_relation_probe(settings: Any, investigation_id: str, document_ids: list[str], generation: str) -> bool | None:
    """Return whether the live graph contains duplicate semantic relations.

    ``None`` means the graph could not be inspected; an unmeasured graph never
    qualifies a load run.
    """

    from services.b5_targets import Neo4jTarget

    adapter = Neo4jTarget(settings)
    try:
        graph = adapter.read_graph(investigation_id, document_ids[:200], generation)
        if graph.get("limitations"):
            return None
        edges = graph.get("edges") or []
        seen: set[tuple[str, str, str]] = set()
        for edge in edges:
            key = (str(edge.get("source", "")), str(edge.get("target", "")), str(edge.get("relationship", "")))
            if key in seen:
                return True
            seen.add(key)
        return False if edges else None
    except Exception:
        return None
    finally:
        adapter.close()


def _oom_probe() -> int | None:
    """Read container OOM flags when an explicit Compose project is supplied."""

    project = os.getenv("B5_COMPOSE_PROJECT", "").strip()
    if not project or shutil.which("docker") is None:
        return None
    try:
        result = subprocess.run(["docker", "compose", "-p", project, "ps", "-q"], capture_output=True, text=True, timeout=10, check=True)
        container_ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if not container_ids:
            return None
        oom = 0
        for container_id in container_ids:
            inspected = subprocess.run(["docker", "inspect", "--format", "{{.State.OOMKilled}}", container_id], capture_output=True, text=True, timeout=10, check=True)
            if inspected.stdout.strip().lower() == "true":
                oom += 1
        return oom
    except (OSError, subprocess.SubprocessError):
        return None


def _runtime_skip(plan: AcceptancePlan, reasons: list[str], *, status: str = "skipped") -> dict[str, Any]:
    return {"status": status, "qualified": False, "mode": plan.mode, "plan": {**asdict(plan), "phases": [asdict(phase) for phase in plan.phases]}, "limitations": reasons, "runtime": {"executed": False}}


def run_acceptance(*, mode: str = "smoke", disposable_stack: bool = False,
                   kubernetes_demo: bool = False, output: str | None = None,
                   drain_seconds: int | None = None, probe_seconds: int = 0) -> dict[str, Any]:
    """Run or explicitly skip the actual-stack probe and return a JSON report."""

    plan = acceptance_plan(mode, drain_seconds=drain_seconds)
    if os.getenv("B5_RUNTIME_TESTS", "false").lower() not in {"1", "true", "yes"}:
        result = _runtime_skip(plan, ["B5_RUNTIME_TESTS=true is required; no external stack was touched."])
        if output:
            Path(output).write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        return result
    # Acceptance configuration must come from the disposable Compose
    # environment, never from a developer's backend/.env file.
    from config import Settings

    settings = Settings(_env_file=None)
    config_errors = validate_disposable_settings(
        settings, disposable_stack=disposable_stack, kubernetes_demo=kubernetes_demo
    )
    if config_errors:
        result = _runtime_skip(plan, config_errors, status="blocked")
        if output:
            Path(output).write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        return result
    if probe_seconds < 0 or probe_seconds > 300:
        raise ValueError("probe_seconds must be between 0 and 300")
    if plan.drain_seconds < 1 or plan.drain_seconds > 900:
        raise ValueError("drain_seconds must be between 1 and 900")

    # Imports that create clients happen only after both gates pass.
    from confluent_kafka import Consumer
    from services.document_normalization import normalize_raw_event
    from services.event_factory import raw_event_from_document
    from services.kafka_runtime import KafkaEventPublisher, SchemaRegistry, kafka_client_config, physical_topic
    from services.b5_repository import ProjectionRepository

    run_id = "b5_acceptance_" + uuid4().hex
    repository = ProjectionRepository(settings.persistence_target)
    publisher = KafkaEventPublisher(SchemaRegistry(), settings=settings)
    consumer = Consumer({**kafka_client_config(settings), "group.id": run_id, "auto.offset.reset": "earliest", "enable.auto.commit": False, "isolation.level": "read_committed"})
    processed_topic = "documents.processed.v1"
    consumer.subscribe([physical_topic(processed_topic, settings)])
    stop = threading.Event()
    failures: list[str] = []
    query_errors: list[str] = []
    processed: dict[str, float] = {}
    publish_times: dict[str, float] = {}
    delivery_seen: set[tuple[str, str]] = set()
    latencies = _Latency()
    started = time.monotonic()

    def collect() -> None:
        try:
            from services.kafka_runtime import SchemaRegistry as _Registry

            registry = _Registry()
            while not stop.is_set():
                message = consumer.poll(0.5)
                if message is None:
                    continue
                if message.error():
                    raise RuntimeError(str(message.error()))
                event = registry.decode(processed_topic, message.value())
                if event.get("correlation_id") != run_id:
                    continue
                document_id = str(event["payload"]["document"]["id"])
                processed_at = time.monotonic()
                if document_id not in processed:
                    processed[document_id] = processed_at
                    published_at = publish_times.get(document_id)
                    if published_at is not None:
                        latencies.add("raw_to_processed_ms", (processed_at - published_at) * 1000.0)
        except Exception as exc:
            failures.append(type(exc).__name__)

    collector = threading.Thread(target=collect, name="b5-acceptance-collector", daemon=True)
    collector.start()
    documents = build_seed_documents(run_id, plan.seed_documents)
    generation = str(getattr(settings, "B5_GENERATION", "live"))
    warmup_count = plan.backlog_documents if mode == "load" else min(2, len(documents))
    measured_ids = {document.id for document in documents[warmup_count:]}
    visibility_seen: set[str] = set()
    backlog_samples: list[dict] = []
    phase_windows: list[dict] = []

    def observe() -> None:
        from services.b5_targets import ElasticsearchTarget
        adapter = ElasticsearchTarget(settings)
        try:
            while not stop.is_set():
                rows = repository.delivery_observations(generation)
                _sample_deliveries(rows, measured_ids, latencies, delivery_seen)
                published_ids = set(publish_times)
                integrity = _delivery_integrity(repository, published_ids, generation, observations=rows)
                backlog_samples.append({"elapsed_seconds": round(time.monotonic() - started, 3),
                                        "published": len(published_ids), "processed": len(processed),
                                        "applied": integrity["counts"],
                                        "pending": {target: len(published_ids) - count for target, count in integrity["counts"].items()}})
                candidates = sorted((published_ids & measured_ids) - visibility_seen)
                visible = adapter.visible_document_ids(generation, candidates)
                now = time.monotonic()
                for identity in visible:
                    latencies.add("raw_to_search_ms", (now - publish_times[identity]) * 1000)
                visibility_seen.update(visible)
                stop.wait(2)
        except Exception as exc:
            failures.append("observer:" + type(exc).__name__)
        finally:
            adapter.close()

    observer: threading.Thread | None = None
    query_probe: threading.Thread | None = None
    workspace_info: dict[str, Any] | None = None
    raw_started_at: float | None = None
    published = 0
    paced_published = 0
    try:
        # Seed the bounded backlog before the measured rate windows.  Every
        # record still uses raw Kafka, but the preseed is excluded from paced
        # throughput accounting and leaves no unpaced tail after the burst.
        preseed_documents = documents[:warmup_count]
        for document in preseed_documents:
            if raw_started_at is None:
                raw_started_at = time.monotonic()
            publish_times[document.id] = time.monotonic()
            publisher.publish("raw.documents.v1", raw_event_from_document(document, producer="b5-acceptance", correlation_id=run_id))
            published += 1
        probe_documents = documents[:min(200,warmup_count)]
        if not _wait_for_canonical_documents(repository, [document.id for document in probe_documents], timeout=min(180.0, float(plan.drain_seconds))):
            raise RuntimeError("canonical_probe_documents_not_persisted")
        workspace_info = persist_acceptance_investigation(repository, run_id, probe_documents)
        warmup_deadline = time.monotonic() + max(180, plan.drain_seconds)
        while True:
            document_ready = _delivery_integrity(repository, {document.id for document in preseed_documents}, generation)["all_targets_applied"]
            graph_ready = repository.coverage([workspace_info["snapshot"]], generation).get("neo4j", {}).get("pending", 1) == 0
            if document_ready and graph_ready:
                break
            if time.monotonic() >= warmup_deadline or failures:
                raise RuntimeError("warmup_projection_timeout")
            time.sleep(2)
        observer = threading.Thread(target=observe, name="b5-projection-observer", daemon=True)
        observer.start()
        query_probe = threading.Thread(target=_api_probe, kwargs={"stop": stop, "api_url": os.getenv("B5_API_URL", "http://127.0.0.1:8000"), "investigation_id": workspace_info["investigation_id"], "from_document_id": probe_documents[0].id, "to_document_id": probe_documents[1].id if len(probe_documents) > 1 else probe_documents[0].id, "latency": latencies, "errors": query_errors, "clients": plan.query_clients}, name="b5-acceptance-api-probes", daemon=True)
        query_probe.start()
        paced_offset = warmup_count
        for phase in plan.phases:
            phase_documents = documents[paced_offset:paced_offset + phase.count]
            phase_started = time.monotonic()
            for index, document in enumerate(phase_documents):
                due = phase_started + index * 60.0 / phase.rate_per_minute
                time.sleep(max(0.0, due - time.monotonic()))
                if raw_started_at is None:
                    raw_started_at = time.monotonic()
                publish_times[document.id] = time.monotonic()
                publisher.publish("raw.documents.v1", raw_event_from_document(document, producer="b5-acceptance", correlation_id=run_id))
                published += 1
                paced_published += 1
                paced_offset += 1
                if failures:
                    raise RuntimeError(failures[0])
            # Complete the entire specified rate window, including its final interval.
            stop.wait(max(0, phase_started + len(phase_documents) * 60 / phase.rate_per_minute - time.monotonic()))
            phase_windows.append({"name": phase.name, "count": len(phase_documents), "rate_per_minute": phase.rate_per_minute,
                                  "started_elapsed_seconds": phase_started - started, "duration_seconds": time.monotonic() - phase_started})
    except Exception as exc:
        failures.append(type(exc).__name__)
    finally:
        deadline = time.monotonic() + plan.drain_seconds
        next_integrity_check = 0.0
        while time.monotonic() < deadline and not failures:
            if time.monotonic() >= next_integrity_check:
                if len(processed) >= published and _delivery_integrity(repository, set(processed), generation)["all_targets_applied"] and measured_ids.issubset(visibility_seen):
                    break
                next_integrity_check = time.monotonic() + 2
            time.sleep(0.5)
        _sample_deliveries(repository.delivery_observations(generation), measured_ids, latencies, delivery_seen)
        stop.set()
        collector.join(timeout=5)
        if observer is not None:
            observer.join(timeout=5)
        if query_probe is not None:
            query_probe.join(timeout=5)
        consumer.close()

    canonical_ids = {str(snapshot["domain_id"]) for snapshot in repository.current_records(kind="document")}
    expected_ids = {document.id for document in documents}
    processed_ids = set(processed)
    delivery_integrity = _delivery_integrity(repository, expected_ids, generation)
    no_loss = expected_ids == processed_ids == canonical_ids and delivery_integrity["all_targets_applied"]
    health = _health_probe(settings)
    target_probe = _target_probe(settings, repository, processed, latencies, generation)
    recovery = {"status": "not_run", "reason": "pass --probe-seconds to run isolated-generation rebuild probe"}
    if probe_seconds:
        from services.b5_targets import ElasticsearchTarget, Neo4jTarget

        recovery_targets = {"elasticsearch": ElasticsearchTarget(settings), "neo4j": Neo4jTarget(settings)}
        try:
            recovery = recovery_probe(repository, recovery_targets)
        finally:
            for adapter in recovery_targets.values():
                adapter.close()
    duplicate_semantic_relations = _duplicate_semantic_relation_probe(settings, workspace_info["investigation_id"], [document.id for document in documents[:200]], generation) if workspace_info else None
    oom_events = _oom_probe()
    latency_report = latencies.report()
    latency_ok = all(latency_report[key]["p95_ms"] is not None and latency_report[key]["p95_ms"] <= limit for key, limit in (("target_freshness_ms", 30_000), ("lexical_ms", 1_000), ("hybrid_ms", 2_000), ("graph_ms", 1_000), ("path_ms", 2_000)))
    cache_metrics = latency_report["cache_observations"]
    cache_ok = cache_metrics["unknown"] == 0 and latency_report["cache_hit_ms"]["count"] > 0 and latency_report["cache_miss_ms"]["count"] > 0
    target_ok = all(
        isinstance(target_probe.get(name), dict)
        and target_probe[name].get("status") == "ready"
        and target_probe[name].get("matching_documents") == target_probe[name].get("canonical_documents")
        and target_probe[name].get("canonical_documents", 0) > 0
        for name in ("elasticsearch", "neo4j")
    )
    kafka_health = health.get("kafka", {}) if isinstance(health.get("kafka"), dict) else {}
    broker_dlq_total = kafka_health.get("dlq_total")
    flink_health = health.get("flink", {})
    flink_checkpoints_ok = flink_health.get("status") == "healthy" and flink_health.get("checkpoint_health", {}).get("status") == "healthy"
    integrity_ok = broker_dlq_total == 0 and delivery_integrity["all_targets_applied"] if broker_dlq_total is not None else False
    backlog_drained = expected_ids == processed_ids and delivery_integrity["all_targets_applied"] and measured_ids.issubset(visibility_seen)
    sustained_growth = _sustained_backlog_growth(backlog_samples, phase_windows)
    required_measures_known = (
        latency_ok
        and cache_ok
        and duplicate_semantic_relations is False
        and oom_events == 0
        and broker_dlq_total is not None
        and sustained_growth is False
        and len(delivery_seen) == len(measured_ids) * 3
        and measured_ids.issubset(visibility_seen)
        and len(phase_windows) == len(plan.phases)
        and flink_checkpoints_ok
    )
    run_ok = no_loss and backlog_drained and not failures and not query_errors and target_ok and integrity_ok
    load_qualified = plan.mode == "load" and run_ok and required_measures_known
    result = {
        "status": "passed" if run_ok else "failed",
        "qualified": bool(load_qualified),
        "mode": plan.mode,
        "run_id": run_id,
        "plan": {**asdict(plan), "phases": [asdict(phase) for phase in plan.phases]},
        "seed": {"requested": plan.seed_documents, "preseeded": warmup_count, "paced_published": paced_published, "published": published, "processed": len(processed), "canonical": len(canonical_ids), "lost_document_ids": sorted(expected_ids - processed_ids)[:100], "unexpected_document_ids": sorted(processed_ids - expected_ids)[:100]},
        "latency": latency_report,
        "phase_windows": phase_windows,
        "backlog_samples": backlog_samples,
        "targets": target_probe,
        "recovery": recovery,
        "integrity": {"no_lost_canonical_documents": no_loss, "delivery_integrity": delivery_integrity, "duplicate_semantic_relations": duplicate_semantic_relations, "oom_events": oom_events, "broker_dlq_total": broker_dlq_total, "backlog_drained": backlog_drained, "sustained_backlog_growth": sustained_growth, "freshness_samples_complete": len(delivery_seen) == len(measured_ids) * 3, "raw_to_search_samples_complete": measured_ids.issubset(visibility_seen), "latency_thresholds_met": latency_ok, "cache_metrics_complete": cache_ok, "targets_match_canonical": target_ok, "delivery_errors": failures},
        "health": health,
        "runtime": {"executed": True, "elapsed_seconds": round(time.monotonic() - started, 2), "failures": failures, "probe_seconds": probe_seconds, "concurrent_query_clients": plan.query_clients, "query_errors": query_errors[:100]},
        "limitations": [reason for reason, missing in (("cache hit/miss classification unavailable", not cache_ok), ("duplicate semantic relation probe unavailable", duplicate_semantic_relations is None), ("container OOM probe unavailable; use the host qualification supervisor", oom_events is None), ("Kafka DLQ health unavailable", broker_dlq_total is None), ("Flink completed checkpoint evidence unavailable", not flink_checkpoints_ok)) if missing],
    }
    if output:
        Path(output).write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("smoke", "load"), default="smoke")
    parser.add_argument("--disposable-stack", action="store_true", required=True, help="Acknowledge a disposable broker/database/target stack")
    parser.add_argument("--kubernetes-demo", action="store_true", help="Allow the isolated Kubernetes service names")
    parser.add_argument("--drain-seconds", type=int)
    parser.add_argument("--probe-seconds", type=int, default=0)
    parser.add_argument("--output")
    args = parser.parse_args()
    result = run_acceptance(mode=args.mode, disposable_stack=args.disposable_stack,
                            kubernetes_demo=args.kubernetes_demo, output=args.output,
                            drain_seconds=args.drain_seconds, probe_seconds=args.probe_seconds)
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status") == "skipped" or (result.get("status") == "passed" and (args.mode == "smoke" or result.get("qualified"))) else 1


if __name__ == "__main__":
    raise SystemExit(main())
