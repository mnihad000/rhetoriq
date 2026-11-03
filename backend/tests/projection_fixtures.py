"""Canonical evidence fixtures shared by projection tests."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from models.document import Document, SourceReference

BASE_TEXT = "Public records policy changes today."
LOAD_SEED_COUNT = 10_000


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
