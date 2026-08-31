import html
import logging
import re
import threading
from fastapi import APIRouter, HTTPException, Query
from urllib.parse import urlparse
from uuid import uuid4

logger = logging.getLogger(__name__)

# Tracks investigation IDs currently running in background threads
_running_investigations: set[str] = set()

from agents.receipts_agent import build_receipts as build_receipts_agent
from agents.claim_counterpoint_agent import build_claim_counterpoints
from agents.narrative_family_agent import build_narrative_family as build_narrative_family_artifact
from agents.planner_agent import plan_investigation
from agents.retriever_agent import RetrieverAgent
from config import get_settings
from demo_data import ALL_DOCUMENTS, DEMO_GRAPHS, DEMO_NARRATIVES
from models.graph import NarrativeGraph
from models.investigation import (
    AgentDebateRequest,
    AgentDebateResult,
    AnalystRequest,
    AnalystResult,
    ClaimCounterpointRequest,
    ClaimCounterpointResult,
    ClaimVerificationRequest,
    ClaimVerificationResult,
    CounterNarrativeRequest,
    CounterNarrativeResult,
    FinalReportRequest,
    FinalReportResult,
    InvestigationWorkspace,
    NarrativeFamilyRequest,
    NarrativeFamilyResult,
    PlannerRequest,
    PlannerResponse,
    RecentInvestigationSummary,
    ReceiptsRequest,
    ReceiptsResult,
    RunInvestigationRequest,
    RetrieveRequest,
    RetrievalResult,
    SourceDiversityRequest,
    SourceDiversityResult,
    TimelineRequest,
    TimelineResult,
)
from services.analyst_builder import build_analyst_result as build_analyst_result_artifact
from services.agent_debate_builder import build_agent_debate as build_agent_debate_artifact
from models.narrative import NarrativeCluster
from services.counter_narrative_builder import (
    build_counter_narratives as build_counter_narratives_artifact,
)
from services.final_report_builder import (
    apply_receipts_annotations,
    build_final_report as build_final_report_artifact,
)
from services.embedding_service import get_embedding_service
from services.graph_builder import GraphBuilder
from services.ingestion import get_merged_documents
from services.investigation_cache import get_investigation_cache
from services.investigation_repository import InvestigationRepository
from services.page_fetcher import get_page_fetcher
from services.search_provider import CachedSearchProvider, build_search_provider
from services.trending_cache import TrendingRedisCache
from services.research_loop_runner import InvestigationRunner
from services.autonomous_research import (
    autonomous_runtime_enabled,
    get_research_manager,
    get_research_repository,
)
from services.claim_evidence_verifier import ClaimEvidenceVerifier, apply_claim_verification
from services.mutation_detection import MutationDetector
from services.redis_memory import get_redis_memory_service
from services.retrieval import Retriever
from services.source_diversity_builder import build_source_diversity as build_source_diversity_artifact
from services.spike_detection import SpikeDetector
from services.timeline_builder import build_timeline as build_timeline_artifact


def _active_documents():
    """Returns live store + demo corpus merged. Falls back to demo when store is empty."""
    return get_merged_documents(ALL_DOCUMENTS)


router = APIRouter(prefix="/api")

_retriever = Retriever()
_spike_detector = SpikeDetector()
_mutation_detector = MutationDetector()
_graph_builder = GraphBuilder()
_investigation_repo = InvestigationRepository(get_settings().INVESTIGATION_DB_PATH)
_investigation_cache = get_investigation_cache()
_TITLE_TAG_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_TITLE_SITE_SPLIT_RE = re.compile(r"\s+[\-|:|]\s+")


def _update_workspace_cache(investigation_id: str) -> None:
    """Re-load the workspace from SQLite and write it back to Redis. No-op if cache unavailable."""
    if not _investigation_cache:
        return
    workspace = _investigation_repo.get_investigation_workspace(investigation_id)
    if workspace:
        _investigation_cache.cache_workspace(workspace)


def _looks_like_url(value: str) -> bool:
    try:
        parsed = urlparse(value.strip())
    except Exception:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _extract_page_title(html_text: str) -> str | None:
    meta_match = re.search(
        r'<meta[^>]+(?:property|name)=["\']og:title["\'][^>]+content=["\'](.*?)["\']',
        html_text,
        re.IGNORECASE | re.DOTALL,
    )
    if meta_match:
        return html.unescape(meta_match.group(1)).strip()

    title_match = _TITLE_TAG_RE.search(html_text)
    if title_match:
        return html.unescape(title_match.group(1)).strip()
    return None


def _clean_resolved_title(title: str) -> str:
    cleaned = re.sub(r"\s+", " ", title).strip()
    if not cleaned:
        return ""

    parts = [part.strip() for part in _TITLE_SITE_SPLIT_RE.split(cleaned) if part.strip()]
    if len(parts) >= 2:
        first, last = parts[0], parts[-1]
        if len(last) <= 24 and len(first.split()) >= 4:
            return first

    return cleaned


def _resolve_investigation_query(
    query_text: str,
    prior_context: dict | None,
) -> tuple[str, dict | None, list[str]]:
    trimmed = query_text.strip()
    if not _looks_like_url(trimmed):
        return trimmed, prior_context, []

    warnings: list[str] = []
    page = get_page_fetcher().fetch(trimmed)
    if hasattr(page, "error_type"):
        warnings.append(
            "Submitted URL could not be read, so RhetoriQ used the raw link as the investigation query."
        )
        return trimmed, prior_context, warnings

    resolved_title = _clean_resolved_title(_extract_page_title(page.html) or "")
    if not resolved_title:
        warnings.append(
            "Submitted URL loaded, but no readable headline was found, so RhetoriQ used the raw link as the investigation query."
        )
        return trimmed, prior_context, warnings

    merged_context = dict(prior_context or {})
    merged_context["seed_url"] = {
        "submitted_url": trimmed,
        "resolved_url": page.final_url,
        "headline": resolved_title,
        "source_domain": urlparse(page.final_url).netloc.lower(),
    }
    warnings.append(
        "Submitted URL resolved to a page headline, and retrieval will use that headline to find related coverage."
    )
    return resolved_title, merged_context, warnings


def _collect_cited_document_ids(report, claim_counterpoint_result) -> list[str]:
    doc_ids: list[str] = []
    for claim in report.key_claims:
        for citation in [*claim.citations, *claim.counter_citations]:
            doc_ids.append(citation.document_id)
    if claim_counterpoint_result is not None:
        for pair in claim_counterpoint_result.pairs:
            for citation in [*pair.main_receipts, *pair.counter_receipts]:
                doc_ids.append(citation.document_id)
    return list(dict.fromkeys(doc_ids))


def _build_memory_prior_context(query_text: str) -> dict:
    """Retrieve related prior context from Redis memory for planner/agents."""
    try:
        memory = get_redis_memory_service()
        if not memory.available:
            return {}
        query_embedding = get_embedding_service().embed_query(query_text)
        similar_claims = memory.search_similar_claims(query_embedding, top_k=5)
        related_articles = memory.search_related_articles(query_embedding, top_k=5)
        if not similar_claims and not related_articles:
            return {}
        return {
            "redis_memory": {
                "similar_claims": similar_claims,
                "related_articles": related_articles,
                "guidance": (
                    "Use prior Redis memory as context only. Do not treat prior claims "
                    "as proof without fresh receipts."
                ),
            }
        }
    except Exception:
        return {}


def _merge_prior_context(existing: dict | None, redis_context: dict) -> dict | None:
    if not redis_context:
        return existing
    merged = dict(existing or {})
    merged.update(redis_context)
    return merged


def _store_retrieved_documents_in_memory(investigation_id: str, documents) -> None:
    try:
        memory = get_redis_memory_service()
        if not memory.available or not documents:
            return
        embedding_service = get_embedding_service()
        for doc in documents[:40]:
            text = f"{doc.title}. {doc.snippet or doc.text[:500]}"
            metadata = {
                "investigation_id": investigation_id,
                "document_id": doc.id,
                "source_name": doc.source_name,
                "source_url": doc.url,
                "published_at": doc.published_at.isoformat() if doc.published_at else None,
                "collected_at": doc.collected_at.isoformat() if doc.collected_at else None,
                "content_type": "article",
                "narrative_role": (doc.metadata or {}).get("narrative_role") if doc.metadata else None,
                "credibility_score": (doc.metadata or {}).get("credibility_score") if doc.metadata else None,
            }
            embedding = doc.embedding or embedding_service.embed_document(doc)
            memory.store_article_vector(f"{investigation_id}:article:{doc.id}", text, embedding, metadata)
            for index, claim in enumerate(doc.claims or []):
                memory.store_claim_vector(
                    f"{investigation_id}:doc:{doc.id}:claim:{index}",
                    claim,
                    embedding_service.embed_text(claim),
                    {**metadata, "content_type": "claim"},
                )
    except Exception:
        pass


def _store_timeline_in_memory(result: TimelineResult) -> None:
    try:
        memory = get_redis_memory_service()
        if not memory.available:
            return
        embedding_service = get_embedding_service()
        for event in result.timeline_events:
            text = f"{event.title}. {event.explanation}. {event.snippet or ''}"
            memory.store_timeline_event(
                f"{result.investigation_id}:timeline:{event.id}",
                text,
                embedding_service.embed_text(text),
                {
                    "investigation_id": result.investigation_id,
                    "event_id": event.id,
                    "document_id": event.document_id,
                    "source_name": event.source_name,
                    "source_url": event.url,
                    "published_at": event.timestamp.isoformat(),
                    "agent_name": "Timeline Agent",
                    "narrative_role": event.narrative_side,
                    "content_type": "timeline_event",
                },
            )
    except Exception:
        pass


def _store_narrative_family_in_memory(result: NarrativeFamilyResult) -> None:
    try:
        memory = get_redis_memory_service()
        if not memory.available:
            return
        for child in result.child_narratives:
            memory.store_agent_finding(
                f"{result.investigation_id}:family:{child.id}",
                "Narrative Family Agent",
                result.investigation_id,
                f"{child.title}. {child.branch_summary}",
                {
                    "narrative_role": child.branch_type,
                    "content_type": "agent_finding",
                    "credibility_score": child.source_diversity_score,
                },
            )
    except Exception:
        pass


def _store_analyst_in_memory(result: AnalystResult) -> None:
    try:
        memory = get_redis_memory_service()
        if not memory.available:
            return
        embedding_service = get_embedding_service()
        sections = result.draft_report_sections
        memory.store_agent_finding(
            f"{result.investigation_id}:analyst:summary",
            "Analyst Agent",
            result.investigation_id,
            sections.executive_summary,
            {"content_type": "agent_finding"},
            )
        for claim in result.candidate_claims:
            memory.store_claim_vector(
                f"{result.investigation_id}:analyst:{claim.id}",
                claim.claim_text,
                embedding_service.embed_text(claim.claim_text),
                {
                    "investigation_id": result.investigation_id,
                    "agent_name": "Analyst Agent",
                    "credibility_score": claim.confidence_score,
                    "narrative_role": claim.claim_type,
                    "content_type": "claim",
                },
            )
    except Exception:
        pass


def _store_claim_counterpoints_in_memory(result: ClaimCounterpointResult) -> None:
    try:
        memory = get_redis_memory_service()
        if not memory.available:
            return
        embedding_service = get_embedding_service()
        for pair in result.pairs:
            memory.store_claim_vector(
                f"{result.investigation_id}:counterpoint:{pair.claim_id}:main",
                pair.main_claim_text,
                embedding_service.embed_text(pair.main_claim_text),
                {
                    "investigation_id": result.investigation_id,
                    "agent_name": "Claim Counterpoint Agent",
                    "credibility_score": pair.confidence_score,
                    "narrative_role": "main",
                    "content_type": "claim",
                },
            )
            memory.store_claim_vector(
                f"{result.investigation_id}:counterpoint:{pair.claim_id}:counter",
                pair.counter_claim_text,
                embedding_service.embed_text(pair.counter_claim_text),
                {
                    "investigation_id": result.investigation_id,
                    "agent_name": "Claim Counterpoint Agent",
                    "credibility_score": pair.confidence_score,
                    "narrative_role": pair.counter_type,
                    "content_type": "claim",
                },
            )
    except Exception:
        pass


def _store_agent_debate_in_memory(result: AgentDebateResult) -> None:
    try:
        memory = get_redis_memory_service()
        if not memory.available:
            return
        debate_items = [
            ("Analyst Agent", "analyst_position", result.analyst_position),
            ("Skeptic Agent", "skeptic_response", result.skeptic_response),
            ("Receipts Agent", "receipts_check", result.receipts_check),
            ("Counter-Narrative Agent", "counter_narrative_note", result.counter_narrative_note),
            ("Safety Agent", "safety_grounding_decision", result.safety_grounding_decision),
            ("Final Language Agent", "final_language_decision", result.final_language_decision),
        ]
        for agent_name, suffix, text in debate_items:
            memory.store_agent_finding(
                f"{result.investigation_id}:debate:{suffix}",
                agent_name,
                result.investigation_id,
                text,
                {
                    "content_type": "agent_finding",
                    "credibility_score": result.confidence_score,
                },
            )
    except Exception:
        pass


def _store_report_in_memory(result: FinalReportResult) -> None:
    try:
        memory = get_redis_memory_service()
        if not memory.available:
            return
        embedding_service = get_embedding_service()
        memory.store_agent_finding(
            f"{result.investigation_id}:report:summary",
            "Final Report Agent",
            result.investigation_id,
            result.report_summary,
            {
                "content_type": "agent_finding",
                "credibility_score": result.confidence_score,
            },
        )
        for claim in result.key_claims:
            memory.store_claim_vector(
                f"{result.investigation_id}:report:{claim.claim_id}",
                claim.claim_text,
                embedding_service.embed_text(claim.claim_text),
                {
                    "investigation_id": result.investigation_id,
                    "agent_name": "Final Report Agent",
                    "credibility_score": claim.confidence_score,
                    "narrative_role": claim.claim_type,
                    "content_type": "claim",
                },
            )
    except Exception:
        pass


def _build_retriever_agent() -> RetrieverAgent:
    cache = TrendingRedisCache(get_settings().REDIS_URL)
    provider = CachedSearchProvider(build_search_provider(), cache=cache)
    return RetrieverAgent(
        repository=_investigation_repo,
        search_provider=provider,
        page_fetcher=get_page_fetcher(cache=cache),
    )


def _build_investigation_runner() -> InvestigationRunner:
    return InvestigationRunner(
        repository=_investigation_repo,
        retriever=_build_retriever_agent(),
    )


def _ensure_counter_narrative_result(
    investigation_id: str,
    plan,
    retrieval,
    documents,
) -> CounterNarrativeResult:
    counter_result = _investigation_repo.get_counter_narrative_result(investigation_id)
    if counter_result is None:
        counter_result = build_counter_narratives_artifact(investigation_id, plan, retrieval, documents)
        _investigation_repo.save_counter_narrative_result(counter_result)
    return counter_result


def _ensure_timeline_result(
    investigation_id: str,
    plan,
    retrieval,
    documents,
) -> TimelineResult:
    timeline_result = _investigation_repo.get_timeline_result(investigation_id)
    if timeline_result is None:
        timeline_result = build_timeline_artifact(investigation_id, plan, retrieval, documents)
        _investigation_repo.save_timeline_result(timeline_result)
    return timeline_result


def _ensure_analyst_result(
    investigation_id: str,
    plan,
    retrieval,
    documents,
) -> AnalystResult:
    analyst_result = _investigation_repo.get_analyst_result(investigation_id)
    if analyst_result is not None:
        return analyst_result

    timeline_result = _ensure_timeline_result(investigation_id, plan, retrieval, documents)
    counter_result = _ensure_counter_narrative_result(investigation_id, plan, retrieval, documents)
    analyst_result = build_analyst_result_artifact(
        investigation_id,
        plan,
        retrieval,
        documents,
        timeline_result,
        counter_result,
    )
    _investigation_repo.save_analyst_result(analyst_result)
    return analyst_result


def _ensure_narrative_family_result(
    investigation_id: str,
    plan,
    retrieval,
    documents,
) -> NarrativeFamilyResult:
    narrative_family_result = _investigation_repo.get_narrative_family_result(investigation_id)
    if narrative_family_result is not None:
        return narrative_family_result

    timeline_result = _ensure_timeline_result(investigation_id, plan, retrieval, documents)
    counter_result = _ensure_counter_narrative_result(investigation_id, plan, retrieval, documents)
    narrative_family_result = build_narrative_family_artifact(
        investigation_id,
        plan,
        retrieval,
        documents,
        timeline_result,
        counter_result,
    )
    _investigation_repo.save_narrative_family_result(narrative_family_result)
    return narrative_family_result


def _ensure_claim_counterpoint_result(
    investigation_id: str,
    plan,
    retrieval,
    documents,
) -> ClaimCounterpointResult:
    claim_counterpoint_result = _investigation_repo.get_claim_counterpoint_result(investigation_id)
    if claim_counterpoint_result is not None:
        return claim_counterpoint_result

    counter_result = _ensure_counter_narrative_result(investigation_id, plan, retrieval, documents)
    analyst_result = _ensure_analyst_result(investigation_id, plan, retrieval, documents)
    claim_counterpoint_result = build_claim_counterpoints(
        investigation_id,
        plan,
        retrieval,
        documents,
        counter_result,
        analyst_result,
    )
    _investigation_repo.save_claim_counterpoint_result(claim_counterpoint_result)
    return claim_counterpoint_result


def _build_base_report_result(
    investigation_id: str,
    plan,
    retrieval,
    documents,
) -> FinalReportResult:
    timeline_result = _ensure_timeline_result(investigation_id, plan, retrieval, documents)
    counter_result = _ensure_counter_narrative_result(investigation_id, plan, retrieval, documents)
    family_result = _ensure_narrative_family_result(investigation_id, plan, retrieval, documents)
    analyst_result = _ensure_analyst_result(investigation_id, plan, retrieval, documents)
    claim_counterpoint_result = _ensure_claim_counterpoint_result(
        investigation_id,
        plan,
        retrieval,
        documents,
    )
    return build_final_report_artifact(
        investigation_id,
        plan,
        retrieval,
        documents,
        timeline_result,
        counter_result,
        family_result,
        analyst_result,
        claim_counterpoint_result,
    )


def _collect_verification_map(report, claim_counterpoint_result) -> dict[str, str]:
    unique_doc_ids = _collect_cited_document_ids(report, claim_counterpoint_result)
    return {doc_id: "pending" for doc_id in unique_doc_ids}


def _ensure_receipts_and_report(
    investigation_id: str,
    plan,
    retrieval,
    documents,
    *,
    force_refresh: bool = False,
) -> tuple[FinalReportResult, ReceiptsResult, ClaimCounterpointResult]:
    claim_counterpoint_result = _ensure_claim_counterpoint_result(
        investigation_id,
        plan,
        retrieval,
        documents,
    )

    cached_report = None if force_refresh else _investigation_repo.get_final_report_result(investigation_id)
    cached_receipts = None if force_refresh else _investigation_repo.get_receipts_result(investigation_id)

    base_report = cached_report or _build_base_report_result(
        investigation_id,
        plan,
        retrieval,
        documents,
    )

    receipts_result = cached_receipts
    if force_refresh or receipts_result is None:
        receipts_result = build_receipts_agent(
            investigation_id,
            plan,
            documents,
            base_report,
            claim_counterpoint_result,
            _collect_verification_map(base_report, claim_counterpoint_result),
        )
        _investigation_repo.save_receipts_result(receipts_result)

    annotated_report = apply_receipts_annotations(base_report, receipts_result)
    _investigation_repo.save_final_report_result(annotated_report, update_stage=False)
    return annotated_report, receipts_result, claim_counterpoint_result


def _verify_report(
    investigation_id: str,
    plan,
    documents,
    report: FinalReportResult,
    counterpoints: ClaimCounterpointResult | None,
    *,
    force_refresh: bool = False,
) -> tuple[FinalReportResult, ClaimVerificationResult]:
    cached = None if force_refresh else _investigation_repo.get_claim_verification_result(investigation_id)
    verification = cached or ClaimEvidenceVerifier().verify(
        investigation_id, plan, documents, report, counterpoints
    )
    _investigation_repo.save_claim_verification_result(verification)
    return apply_claim_verification(report, verification), verification


# ---------------------------------------------------------------------------
# GET /api/narratives - list spiking narratives
# ---------------------------------------------------------------------------

@router.get("/narratives", response_model=list[NarrativeCluster])
def list_narratives() -> list[NarrativeCluster]:
    return _spike_detector.get_spiking_narratives(DEMO_NARRATIVES, _active_documents())


# ---------------------------------------------------------------------------
# GET /api/narratives/{id} - get single narrative cluster
# ---------------------------------------------------------------------------

@router.get("/narratives/{narrative_id}", response_model=NarrativeCluster)
def get_narrative(narrative_id: str) -> NarrativeCluster:
    cluster = next((n for n in DEMO_NARRATIVES if n.id == narrative_id), None)
    if not cluster:
        raise HTTPException(status_code=404, detail=f"Narrative '{narrative_id}' not found.")
    return cluster


# ---------------------------------------------------------------------------
# GET /api/narratives/{id}/timeline - phrase spike timeline for chart
# ---------------------------------------------------------------------------

@router.get("/narratives/{narrative_id}/timeline")
def get_narrative_timeline(narrative_id: str) -> list[dict]:
    cluster = next((n for n in DEMO_NARRATIVES if n.id == narrative_id), None)
    if not cluster:
        raise HTTPException(status_code=404, detail=f"Narrative '{narrative_id}' not found.")
    docs = _retriever.get_related_documents(cluster, _active_documents())
    phrase = cluster.canonical_phrases[0]
    return _spike_detector.compute_phrase_timeline(phrase, docs)


# ---------------------------------------------------------------------------
# GET /api/investigations - list recent persisted investigations
# ---------------------------------------------------------------------------

@router.get("/investigations", response_model=list[RecentInvestigationSummary])
def list_recent_investigations(
    limit: int = Query(default=6, ge=1, le=12),
) -> list[RecentInvestigationSummary]:
    return _investigation_repo.get_recent_investigations(limit=limit)


# ---------------------------------------------------------------------------
# GET /api/investigations/{id} - fetch persisted investigation workspace state
# ---------------------------------------------------------------------------

@router.get(
    "/investigations/{investigation_id}",
    response_model=InvestigationWorkspace,
)
def get_investigation_workspace(investigation_id: str) -> InvestigationWorkspace:
    if _investigation_cache:
        cached = _investigation_cache.get_workspace(investigation_id)
        if cached is not None:
            return cached.model_copy(update={"research_run": get_research_repository().get_latest_run(investigation_id)})

    workspace = _investigation_repo.get_investigation_workspace(investigation_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail=f"Investigation '{investigation_id}' not found.")

    if _investigation_cache:
        _investigation_cache.cache_workspace(workspace)

    return workspace.model_copy(update={"research_run": get_research_repository().get_latest_run(investigation_id)})


@router.get("/investigations/{investigation_id}/memory")
def get_investigation_memory(investigation_id: str) -> dict:
    if not _investigation_repo.investigation_exists(investigation_id):
        raise HTTPException(status_code=404, detail=f"Investigation '{investigation_id}' not found.")
    memory = get_redis_memory_service()
    context = memory.get_investigation_context(investigation_id)
    return {
        "available": memory.available,
        **context,
    }


@router.get("/investigations/{investigation_id}/similar-claims")
def get_similar_claims(
    investigation_id: str,
    top_k: int = Query(default=5, ge=1, le=20),
) -> dict:
    plan = _investigation_repo.get_plan(investigation_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"Investigation plan for '{investigation_id}' not found.")
    memory = get_redis_memory_service()
    if not memory.available:
        return {"available": False, "results": []}
    query_embedding = get_embedding_service().embed_query(plan.query_text)
    return {
        "available": True,
        "results": memory.search_similar_claims(query_embedding, top_k=top_k),
    }


@router.get("/investigations/{investigation_id}/related-articles")
def get_related_articles(
    investigation_id: str,
    top_k: int = Query(default=5, ge=1, le=20),
) -> dict:
    plan = _investigation_repo.get_plan(investigation_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"Investigation plan for '{investigation_id}' not found.")
    memory = get_redis_memory_service()
    if not memory.available:
        return {"available": False, "results": []}
    query_embedding = get_embedding_service().embed_query(plan.query_text)
    return {
        "available": True,
        "results": memory.search_related_articles(query_embedding, top_k=top_k),
    }


# ---------------------------------------------------------------------------
# POST /api/investigate - create an investigation plan from free-text query
# ---------------------------------------------------------------------------

@router.post("/investigate", response_model=PlannerResponse)
def investigate(request: PlannerRequest) -> PlannerResponse:
    resolved_query_text, request_prior_context, url_warnings = _resolve_investigation_query(
        request.query_text,
        request.prior_context,
    )
    redis_context = _build_memory_prior_context(resolved_query_text)
    prior_context = _merge_prior_context(request_prior_context, redis_context)
    plan = plan_investigation(resolved_query_text, prior_context)
    investigation_id = f"inv_{uuid4().hex}"
    warnings: list[str] = list(url_warnings)

    if get_settings().DEMO_MODE:
        warnings.append(
            "Planner ran in deterministic local mode. Retrieval runs as a separate investigation step."
        )
    if redis_context:
        counts = redis_context["redis_memory"]
        warnings.append(
            "Redis memory attached "
            f"{len(counts['similar_claims'])} similar claim(s) and "
            f"{len(counts['related_articles'])} related article(s) as prior context."
        )

    _investigation_repo.save_plan(investigation_id, resolved_query_text, plan)
    try:
        get_redis_memory_service().store_agent_finding(
            f"{investigation_id}:planner:plan",
            "Query Planner Agent",
            investigation_id,
            f"{plan.topic}. {plan.canonical_phrase or plan.query_text}",
            {
                "content_type": "agent_finding",
                "narrative_role": plan.intent,
            },
        )
    except Exception:
        pass
    _update_workspace_cache(investigation_id)
    return PlannerResponse(
        investigation_id=investigation_id,
        query_text=resolved_query_text,
        plan=plan,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# POST /api/investigations/{id}/retrieve - run retriever agent on persisted plan
# ---------------------------------------------------------------------------

@router.post("/investigations/{investigation_id}/retrieve", response_model=RetrievalResult)
def retrieve(investigation_id: str, request: RetrieveRequest) -> RetrievalResult:
    if not _investigation_repo.investigation_exists(investigation_id):
        raise HTTPException(status_code=404, detail=f"Investigation '{investigation_id}' not found.")

    plan = _investigation_repo.get_plan(investigation_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"Investigation plan for '{investigation_id}' not found.")

    agent = _build_retriever_agent()
    try:
        result = agent.retrieve(
            investigation_id=investigation_id,
            plan=plan,
            max_rounds=request.max_rounds,
            force_refresh=request.force_refresh,
        )
        _store_retrieved_documents_in_memory(
            investigation_id,
            _investigation_repo.get_retrieved_documents(investigation_id),
        )
        _update_workspace_cache(investigation_id)
        return result
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Retriever failed: {exc}") from exc


# ---------------------------------------------------------------------------
# POST /api/investigations/{id}/run - execute the supervised research loop
# ---------------------------------------------------------------------------

@router.post(
    "/investigations/{investigation_id}/run",
    response_model=InvestigationWorkspace,
)
def run_investigation(
    investigation_id: str,
    request: RunInvestigationRequest,
) -> InvestigationWorkspace:
    if not _investigation_repo.investigation_exists(investigation_id):
        raise HTTPException(status_code=404, detail=f"Investigation '{investigation_id}' not found.")

    plan = _investigation_repo.get_plan(investigation_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"Investigation plan for '{investigation_id}' not found.")

    workspace = _investigation_repo.get_investigation_workspace(investigation_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail=f"Investigation workspace for '{investigation_id}' not found.")

    research_manager = get_research_manager()
    same_repository = getattr(research_manager.repository, "_db_path", None) == getattr(_investigation_repo, "_db_path", None)
    if autonomous_runtime_enabled() and same_repository:
        run = research_manager.start(investigation_id, force_refresh=request.force_refresh)
        if _investigation_cache:
            _investigation_cache.invalidate(investigation_id)
        return workspace.model_copy(update={"research_run": run})

    # Already complete — return cached result
    if workspace.research_loop and not request.force_refresh:
        return workspace

    # Already running in background — return current state so frontend can keep polling
    if investigation_id in _running_investigations and not request.force_refresh:
        return workspace

    # Launch research loop in a background thread so this endpoint returns immediately.
    # The frontend polls GET /api/investigations/{id} every few seconds for updates.
    def _run_bg() -> None:
        _running_investigations.add(investigation_id)
        try:
            _build_investigation_runner().run(
                investigation_id=investigation_id,
                plan=plan,
                force_refresh=request.force_refresh,
            )
            if _investigation_cache:
                _investigation_cache.invalidate(investigation_id)
            logger.info("Research loop completed for %s", investigation_id)
        except Exception as exc:
            logger.error("Research loop failed for %s: %s", investigation_id, exc)
        finally:
            _running_investigations.discard(investigation_id)

    thread = threading.Thread(
        target=_run_bg,
        daemon=True,
        name=f"rq-run-{investigation_id[:8]}",
    )
    thread.start()

    # Return current workspace so the frontend has something to render immediately
    return workspace


# ---------------------------------------------------------------------------
# POST /api/investigations/{id}/source-diversity - build deterministic source diversity artifact
# ---------------------------------------------------------------------------

@router.post(
    "/investigations/{investigation_id}/source-diversity",
    response_model=SourceDiversityResult,
)
def source_diversity(
    investigation_id: str,
    request: SourceDiversityRequest,
) -> SourceDiversityResult:
    if not _investigation_repo.investigation_exists(investigation_id):
        raise HTTPException(status_code=404, detail=f"Investigation '{investigation_id}' not found.")

    plan = _investigation_repo.get_plan(investigation_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"Investigation plan for '{investigation_id}' not found.")

    retrieval = _investigation_repo.get_retrieval_result(investigation_id)
    if retrieval is None:
        raise HTTPException(status_code=404, detail=f"Retrieval result for '{investigation_id}' not found.")

    documents = _investigation_repo.get_retrieved_documents(investigation_id)

    if not request.force_refresh:
        cached = _investigation_repo.get_source_diversity_result(investigation_id)
        if cached is not None:
            return cached.model_copy(update={"cached": True})

    try:
        result = build_source_diversity_artifact(investigation_id, plan, retrieval, documents)
        _investigation_repo.save_source_diversity_result(result)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Source diversity build failed: {exc}") from exc


# ---------------------------------------------------------------------------
# POST /api/investigations/{id}/timeline - build deterministic timeline artifact
# ---------------------------------------------------------------------------

@router.post("/investigations/{investigation_id}/timeline", response_model=TimelineResult)
def timeline(investigation_id: str, request: TimelineRequest) -> TimelineResult:
    if not _investigation_repo.investigation_exists(investigation_id):
        raise HTTPException(status_code=404, detail=f"Investigation '{investigation_id}' not found.")

    plan = _investigation_repo.get_plan(investigation_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"Investigation plan for '{investigation_id}' not found.")

    retrieval = _investigation_repo.get_retrieval_result(investigation_id)
    if retrieval is None:
        raise HTTPException(status_code=404, detail=f"Retrieval result for '{investigation_id}' not found.")

    documents = _investigation_repo.get_retrieved_documents(investigation_id)

    if not request.force_refresh:
        cached = _investigation_repo.get_timeline_result(investigation_id)
        if cached is not None:
            return cached.model_copy(update={"cached": True})

    try:
        result = build_timeline_artifact(investigation_id, plan, retrieval, documents)
        _investigation_repo.save_timeline_result(result)
        _store_timeline_in_memory(result)
        _update_workspace_cache(investigation_id)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Timeline build failed: {exc}") from exc


# ---------------------------------------------------------------------------
# POST /api/investigations/{id}/counter-narratives - build counter-frame artifact
# ---------------------------------------------------------------------------

@router.post(
    "/investigations/{investigation_id}/counter-narratives",
    response_model=CounterNarrativeResult,
)
def counter_narratives(
    investigation_id: str,
    request: CounterNarrativeRequest,
) -> CounterNarrativeResult:
    if not _investigation_repo.investigation_exists(investigation_id):
        raise HTTPException(status_code=404, detail=f"Investigation '{investigation_id}' not found.")

    plan = _investigation_repo.get_plan(investigation_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"Investigation plan for '{investigation_id}' not found.")

    retrieval = _investigation_repo.get_retrieval_result(investigation_id)
    if retrieval is None:
        raise HTTPException(status_code=404, detail=f"Retrieval result for '{investigation_id}' not found.")

    documents = _investigation_repo.get_retrieved_documents(investigation_id)

    if not request.force_refresh:
        cached = _investigation_repo.get_counter_narrative_result(investigation_id)
        if cached is not None:
            return cached.model_copy(update={"cached": True})

    try:
        result = build_counter_narratives_artifact(investigation_id, plan, retrieval, documents)
        _investigation_repo.save_counter_narrative_result(result)
        _update_workspace_cache(investigation_id)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Counter-narrative build failed: {exc}") from exc


# ---------------------------------------------------------------------------
# POST /api/investigations/{id}/family - build narrative family artifact
# ---------------------------------------------------------------------------

@router.post(
    "/investigations/{investigation_id}/family",
    response_model=NarrativeFamilyResult,
)
def narrative_family(
    investigation_id: str,
    request: NarrativeFamilyRequest,
) -> NarrativeFamilyResult:
    if not _investigation_repo.investigation_exists(investigation_id):
        raise HTTPException(status_code=404, detail=f"Investigation '{investigation_id}' not found.")

    plan = _investigation_repo.get_plan(investigation_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"Investigation plan for '{investigation_id}' not found.")

    retrieval = _investigation_repo.get_retrieval_result(investigation_id)
    if retrieval is None:
        raise HTTPException(status_code=404, detail=f"Retrieval result for '{investigation_id}' not found.")

    documents = _investigation_repo.get_retrieved_documents(investigation_id)

    if not request.force_refresh:
        cached = _investigation_repo.get_narrative_family_result(investigation_id)
        if cached is not None:
            return cached.model_copy(update={"cached": True})

    try:
        timeline_result = _ensure_timeline_result(investigation_id, plan, retrieval, documents)
        counter_result = _ensure_counter_narrative_result(investigation_id, plan, retrieval, documents)
        result = build_narrative_family_artifact(
            investigation_id,
            plan,
            retrieval,
            documents,
            timeline_result,
            counter_result,
        )
        _investigation_repo.save_narrative_family_result(result)
        _store_narrative_family_in_memory(result)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Narrative family build failed: {exc}") from exc


# ---------------------------------------------------------------------------
# POST /api/investigations/{id}/analyst - build synthesis artifact
# ---------------------------------------------------------------------------

@router.post(
    "/investigations/{investigation_id}/analyst",
    response_model=AnalystResult,
)
def analyst(
    investigation_id: str,
    request: AnalystRequest,
) -> AnalystResult:
    if not _investigation_repo.investigation_exists(investigation_id):
        raise HTTPException(status_code=404, detail=f"Investigation '{investigation_id}' not found.")

    plan = _investigation_repo.get_plan(investigation_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"Investigation plan for '{investigation_id}' not found.")

    retrieval = _investigation_repo.get_retrieval_result(investigation_id)
    if retrieval is None:
        raise HTTPException(status_code=404, detail=f"Retrieval result for '{investigation_id}' not found.")

    documents = _investigation_repo.get_retrieved_documents(investigation_id)

    if not request.force_refresh:
        cached = _investigation_repo.get_analyst_result(investigation_id)
        if cached is not None:
            return cached.model_copy(update={"cached": True})

    source_diversity_result = _investigation_repo.get_source_diversity_result(investigation_id)
    if source_diversity_result is None:
        source_diversity_result = build_source_diversity_artifact(investigation_id, plan, retrieval, documents)
        _investigation_repo.save_source_diversity_result(source_diversity_result)

    timeline_result = _investigation_repo.get_timeline_result(investigation_id)
    if timeline_result is None:
        timeline_result = build_timeline_artifact(investigation_id, plan, retrieval, documents)
        _investigation_repo.save_timeline_result(timeline_result)

    counter_result = _investigation_repo.get_counter_narrative_result(investigation_id)
    if counter_result is None:
        counter_result = _ensure_counter_narrative_result(investigation_id, plan, retrieval, documents)

    try:
        result = build_analyst_result_artifact(
            investigation_id,
            plan,
            retrieval,
            documents,
            timeline_result,
            counter_result,
        )
        _investigation_repo.save_analyst_result(result)
        _store_timeline_in_memory(timeline_result)
        _store_analyst_in_memory(result)
        _update_workspace_cache(investigation_id)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Analyst build failed: {exc}") from exc


# ---------------------------------------------------------------------------
# POST /api/investigations/{id}/claim-counterpoints - build claim-level counterpoint artifact
# ---------------------------------------------------------------------------

@router.post(
    "/investigations/{investigation_id}/claim-counterpoints",
    response_model=ClaimCounterpointResult,
)
def claim_counterpoints(
    investigation_id: str,
    request: ClaimCounterpointRequest,
) -> ClaimCounterpointResult:
    if not _investigation_repo.investigation_exists(investigation_id):
        raise HTTPException(status_code=404, detail=f"Investigation '{investigation_id}' not found.")

    plan = _investigation_repo.get_plan(investigation_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"Investigation plan for '{investigation_id}' not found.")

    retrieval = _investigation_repo.get_retrieval_result(investigation_id)
    if retrieval is None:
        raise HTTPException(status_code=404, detail=f"Retrieval result for '{investigation_id}' not found.")

    documents = _investigation_repo.get_retrieved_documents(investigation_id)

    if not request.force_refresh:
        cached = _investigation_repo.get_claim_counterpoint_result(investigation_id)
        if cached is not None:
            return cached.model_copy(update={"cached": True})

    try:
        result = _ensure_claim_counterpoint_result(
            investigation_id,
            plan,
            retrieval,
            documents,
        )
        if request.force_refresh:
            counter_result = _ensure_counter_narrative_result(investigation_id, plan, retrieval, documents)
            analyst_result = _ensure_analyst_result(investigation_id, plan, retrieval, documents)
            result = build_claim_counterpoints(
                investigation_id,
                plan,
                retrieval,
                documents,
                counter_result,
                analyst_result,
            )
            _investigation_repo.save_claim_counterpoint_result(result)
        _store_claim_counterpoints_in_memory(result)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Claim counterpoint build failed: {exc}") from exc


# ---------------------------------------------------------------------------
# POST /api/investigations/{id}/receipts - build grounding artifact for claims and counter-claims
# ---------------------------------------------------------------------------

@router.post(
    "/investigations/{investigation_id}/receipts",
    response_model=ReceiptsResult,
)
def receipts(
    investigation_id: str,
    request: ReceiptsRequest,
) -> ReceiptsResult:
    if not _investigation_repo.investigation_exists(investigation_id):
        raise HTTPException(status_code=404, detail=f"Investigation '{investigation_id}' not found.")

    plan = _investigation_repo.get_plan(investigation_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"Investigation plan for '{investigation_id}' not found.")

    retrieval = _investigation_repo.get_retrieval_result(investigation_id)
    if retrieval is None:
        raise HTTPException(status_code=404, detail=f"Retrieval result for '{investigation_id}' not found.")

    documents = _investigation_repo.get_retrieved_documents(investigation_id)

    if not request.force_refresh:
        cached = _investigation_repo.get_receipts_result(investigation_id)
        if cached is not None:
            return cached.model_copy(update={"cached": True})

    try:
        _report, result, _claim_counterpoint_result = _ensure_receipts_and_report(
            investigation_id,
            plan,
            retrieval,
            documents,
            force_refresh=request.force_refresh,
        )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Receipts build failed: {exc}") from exc


# ---------------------------------------------------------------------------
# POST /api/investigations/{id}/agent-debate - summarize analyst/skeptic/receipts tension
# ---------------------------------------------------------------------------

@router.post(
    "/investigations/{investigation_id}/agent-debate",
    response_model=AgentDebateResult,
)
def agent_debate(
    investigation_id: str,
    request: AgentDebateRequest,
) -> AgentDebateResult:
    if not _investigation_repo.investigation_exists(investigation_id):
        raise HTTPException(status_code=404, detail=f"Investigation '{investigation_id}' not found.")

    plan = _investigation_repo.get_plan(investigation_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"Investigation plan for '{investigation_id}' not found.")

    retrieval = _investigation_repo.get_retrieval_result(investigation_id)
    if retrieval is None:
        raise HTTPException(status_code=404, detail=f"Retrieval result for '{investigation_id}' not found.")

    documents = _investigation_repo.get_retrieved_documents(investigation_id)

    if not request.force_refresh:
        cached = _investigation_repo.get_agent_debate_result(investigation_id)
        if cached is not None:
            return cached.model_copy(update={"cached": True})

    try:
        counter_result = _ensure_counter_narrative_result(investigation_id, plan, retrieval, documents)
        family_result = _ensure_narrative_family_result(investigation_id, plan, retrieval, documents)
        analyst_result = _ensure_analyst_result(investigation_id, plan, retrieval, documents)
        report_result, receipts_result, claim_counterpoint_result = _ensure_receipts_and_report(
            investigation_id,
            plan,
            retrieval,
            documents,
            force_refresh=request.force_refresh,
        )
        result = build_agent_debate_artifact(
            investigation_id,
            plan,
            analyst_result,
            counter_result,
            family_result,
            claim_counterpoint_result,
            receipts_result,
            report_result,
        )
        _investigation_repo.save_agent_debate_result(result)
        _store_agent_debate_in_memory(result)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Agent debate build failed: {exc}") from exc


# ---------------------------------------------------------------------------
# POST /api/investigations/{id}/verification - explicitly re-verify a report
# ---------------------------------------------------------------------------

@router.post(
    "/investigations/{investigation_id}/verification",
    response_model=ClaimVerificationResult,
)
def verify_investigation_claims(
    investigation_id: str,
    request: ClaimVerificationRequest,
) -> ClaimVerificationResult:
    if not _investigation_repo.investigation_exists(investigation_id):
        raise HTTPException(status_code=404, detail=f"Investigation '{investigation_id}' not found.")
    plan = _investigation_repo.get_plan(investigation_id)
    retrieval = _investigation_repo.get_retrieval_result(investigation_id)
    if plan is None or retrieval is None:
        raise HTTPException(status_code=409, detail="Investigation needs a plan and retrieved evidence before verification.")
    documents = _investigation_repo.get_retrieved_documents(investigation_id)
    try:
        report, _receipts, counterpoints = _ensure_receipts_and_report(
            investigation_id, plan, retrieval, documents, force_refresh=False
        )
        verified_report, verification = _verify_report(
            investigation_id,
            plan,
            documents,
            report,
            counterpoints,
            force_refresh=request.force_refresh,
        )
        _investigation_repo.save_final_report_result(verified_report, update_stage=False)
        _update_workspace_cache(investigation_id)
        return verification.model_copy(update={"cached": not request.force_refresh})
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Claim verification failed: {exc}") from exc


# ---------------------------------------------------------------------------
# POST /api/investigations/{id}/report - assemble final investigation report
# ---------------------------------------------------------------------------

@router.post(
    "/investigations/{investigation_id}/report",
    response_model=FinalReportResult,
)
def final_report(
    investigation_id: str,
    request: FinalReportRequest,
) -> FinalReportResult:
    if not _investigation_repo.investigation_exists(investigation_id):
        raise HTTPException(status_code=404, detail=f"Investigation '{investigation_id}' not found.")

    plan = _investigation_repo.get_plan(investigation_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"Investigation plan for '{investigation_id}' not found.")

    retrieval = _investigation_repo.get_retrieval_result(investigation_id)
    if retrieval is None:
        raise HTTPException(status_code=404, detail=f"Retrieval result for '{investigation_id}' not found.")

    documents = _investigation_repo.get_retrieved_documents(investigation_id)

    try:
        cached_report = None if request.force_refresh else _investigation_repo.get_final_report_result(investigation_id)
        cached_receipts = None if request.force_refresh else _investigation_repo.get_receipts_result(investigation_id)
        cached_agent_debate = None if request.force_refresh else _investigation_repo.get_agent_debate_result(investigation_id)
        if cached_report is not None and cached_receipts is not None and cached_agent_debate is not None:
            return cached_report.model_copy(update={"cached": True})

        source_diversity_result = _investigation_repo.get_source_diversity_result(investigation_id)
        if source_diversity_result is None:
            source_diversity_result = build_source_diversity_artifact(investigation_id, plan, retrieval, documents)
            _investigation_repo.save_source_diversity_result(source_diversity_result)

        result, receipts_result, claim_counterpoint_result = _ensure_receipts_and_report(
            investigation_id,
            plan,
            retrieval,
            documents,
            force_refresh=request.force_refresh,
        )
        if get_settings().CLAIM_VERIFIER_ENABLED:
            result, _verification = _verify_report(
                investigation_id,
                plan,
                documents,
                result,
                claim_counterpoint_result,
                force_refresh=request.force_refresh,
            )
        counter_result = _ensure_counter_narrative_result(investigation_id, plan, retrieval, documents)
        family_result = _ensure_narrative_family_result(investigation_id, plan, retrieval, documents)
        analyst_result = _ensure_analyst_result(investigation_id, plan, retrieval, documents)
        if request.force_refresh or cached_agent_debate is None:
            debate_result = build_agent_debate_artifact(
                investigation_id,
                plan,
                analyst_result,
                counter_result,
                family_result,
                claim_counterpoint_result,
                receipts_result,
                result,
            )
            _investigation_repo.save_agent_debate_result(debate_result)
            _store_agent_debate_in_memory(debate_result)
        _investigation_repo.save_final_report_result(result)
        _store_retrieved_documents_in_memory(investigation_id, documents)
        _store_analyst_in_memory(analyst_result)
        _store_claim_counterpoints_in_memory(claim_counterpoint_result)
        _store_narrative_family_in_memory(family_result)
        _store_report_in_memory(result)
        _update_workspace_cache(investigation_id)

        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Final report assembly failed: {exc}") from exc


# ---------------------------------------------------------------------------
# GET /api/cache/stats - Redis cache statistics (sponsor evidence)
# ---------------------------------------------------------------------------

@router.get("/cache/stats")
def cache_stats() -> dict:
    if _investigation_cache is None:
        return {"redis_cache": "disabled", "reason": "Redis unavailable or cache disabled"}
    stats = _investigation_cache.get_stats()
    stats["redis_cache"] = "enabled"
    stats["cached_investigations"] = _investigation_cache.list_cached_investigations(limit=20)
    return stats


# ---------------------------------------------------------------------------
# GET /api/graph/{narrative_id} - narrative spread graph
# ---------------------------------------------------------------------------

@router.get("/graph/{narrative_id}", response_model=NarrativeGraph)
def get_graph(narrative_id: str) -> NarrativeGraph:
    settings = get_settings()

    if settings.DEMO_MODE and narrative_id in DEMO_GRAPHS:
        return DEMO_GRAPHS[narrative_id]

    cluster = next((n for n in DEMO_NARRATIVES if n.id == narrative_id), None)
    if not cluster:
        raise HTTPException(status_code=404, detail=f"Narrative '{narrative_id}' not found.")

    docs = _retriever.get_related_documents(cluster, _active_documents())
    mutations = _mutation_detector.detect_mutations(docs)
    return _graph_builder.build_graph(docs, mutations, cluster)


# ---------------------------------------------------------------------------
# GET /api/mutations/{narrative_id} - mutation trail
# ---------------------------------------------------------------------------

@router.get("/mutations/{narrative_id}")
def get_mutations(narrative_id: str) -> list[dict]:
    cluster = next((n for n in DEMO_NARRATIVES if n.id == narrative_id), None)
    if not cluster:
        raise HTTPException(status_code=404, detail=f"Narrative '{narrative_id}' not found.")

    if get_settings().DEMO_MODE:
        return [m.model_dump(mode="json") for m in cluster.mutation_trail]

    docs = _retriever.get_related_documents(cluster, _active_documents())
    return _mutation_detector.detect_mutations(docs)
