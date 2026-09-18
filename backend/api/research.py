from __future__ import annotations

import httpx
from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import Response

from config import get_settings
from demo_data import ALL_DOCUMENTS
from models.research import ReplayResponse, ResearchTrailResponse
from services.autonomous_research import get_research_manager, get_research_repository
from services.event_store import EventStore
from services.signal_repository import SignalRepository
from services.flink_health import get_flink_health
from services.research_stream import ResearchStreamResponse, parse_cursor

router = APIRouter(prefix="/api")


@router.get("/research/health")
def research_health() -> dict:
    settings = get_settings()
    components: dict[str, dict] = {}
    event_store = EventStore(settings.persistence_target)
    components["outbox"] = event_store.health()
    try:
        signal_health = SignalRepository(settings.persistence_target).health()
    except Exception as exc:
        signal_health = {"status": "unavailable", "detail": "Signal projection unavailable", "latest_evaluation": None}
    components["signals"] = signal_health
    components["enrichment"] = {
        "status": signal_health.get("enrichment_status", "unavailable"),
        "artifact_failure_count": signal_health.get("artifact_failure_count", 0),
        "workers": signal_health.get("enrichment_workers", []),
    }
    components["flink"] = get_flink_health(
        settings,
        evaluation=signal_health.get("latest_evaluation"),
    )
    try:
        from services.kafka_runtime import KafkaEventPublisher, SchemaRegistry, kafka_consumer_health
        components["kafka"] = KafkaEventPublisher().health()
        components["schema_registry"] = SchemaRegistry().health()
        components["event_consumers"] = kafka_consumer_health(settings)
        enrichment_group = next((group for group in components["event_consumers"].get("groups", []) if group.get("role") == "enrichment"), {})
        components["enrichment"]["backlog"] = enrichment_group.get("lag")
        components["enrichment"]["dlq_depth"] = components["event_consumers"].get("dlq_counts", {}).get("documents.enrichment-requested.dlq.v1")
    except Exception as exc:
        unavailable = {"status": "unavailable", "detail": str(exc)[:180]}
        components["kafka"] = unavailable
        components["schema_registry"] = unavailable
        components["event_consumers"] = unavailable
    try:
        import langgraph
        components["langgraph"] = {"status": "ready"}
    except Exception as exc:
        components["langgraph"] = {"status": "missing", "detail": str(exc)}
    try:
        if settings.DATABASE_URL:
            import psycopg
            with psycopg.connect(settings.DATABASE_URL) as conn:
                conn.execute("SELECT 1")
        else:
            import sqlite3
            with sqlite3.connect(settings.RESEARCH_CHECKPOINT_DB_PATH) as conn:
                conn.execute("SELECT 1")
        components["checkpointer"] = {"status": "ready"}
    except Exception as exc:
        components["checkpointer"] = {"status": "error", "detail": str(exc)}
    service_urls = {
        "searxng": f"{settings.SEARXNG_BASE_URL.rstrip('/')}/search?q=rhetoriq&format=json",
        "federal_register": (
            f"{settings.FEDERAL_REGISTER_BASE_URL.rstrip('/')}/documents.json?"
            "conditions%5Bterm%5D=public%20records&per_page=1"
        ),
    }
    if settings.BROWSER_RENDERING_ENABLED and settings.BROWSER_SERVICE_URL:
        service_urls["browser"] = f"{settings.BROWSER_SERVICE_URL.rstrip('/')}/health"
    for name, url in service_urls.items():
        try:
            headers = {"X-RhetoriQ-Browser-Token": settings.BROWSER_SERVICE_TOKEN} if name == "browser" and settings.BROWSER_SERVICE_TOKEN else {}
            response = httpx.get(url, headers=headers, timeout=2)
            status = "ready" if response.is_success else "unavailable"
            detail = None
            if response.is_success and name in {"searxng", "federal_register"}:
                try:
                    payload = response.json()
                    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                        status = "unavailable"
                        detail = "Provider returned a malformed response."
                except ValueError:
                    status = "unavailable"
                    detail = "Provider returned malformed JSON."
            components[name] = {
                "status": status,
                "http_status": response.status_code,
                "detail": detail,
                "fallback": "internal_corpus" if name == "searxng" else "broad_web_search",
            }
        except Exception as exc:
            components[name] = {
                "status": "unavailable",
                "detail": str(exc)[:180],
                "fallback": "internal_corpus" if name == "searxng" else "broad_web_search",
            }
    models = []
    if settings.GEMINI_API_KEY:
        models.append({"provider": "gemini", "model": settings.GEMINI_MODEL})
    if settings.GROQ_API_KEY:
        models.append({"provider": "groq", "model": settings.GROQ_MODEL})
    try:
        ollama = httpx.get(f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/tags", timeout=2)
        if ollama.is_success:
            models.append({"provider": "ollama", "model": settings.OLLAMA_MODEL})
    except Exception:
        pass
    components["models"] = {"status": "ready" if models else "fallback_only", "configured": models}
    investigation_group = next(
        (
            group for group in components["event_consumers"].get("groups", [])
            if group.get("role") == "investigations"
        ),
        None,
    )
    components["worker"] = {
        "status": "ready" if investigation_group and investigation_group["state"] == "stable" else "unavailable",
        "mode": "kafka",
        "group": investigation_group,
    }
    components["internal_retrieval"] = {
        "status": "ready",
        "normalized_document_count": len(ALL_DOCUMENTS),
        "vector_search_configured": settings.ENABLE_VECTOR_SEARCH,
    }
    required = [
        "langgraph", "checkpointer", "worker", "internal_retrieval",
        "outbox", "kafka", "schema_registry", "event_consumers",
    ]
    if settings.RESEARCH_RUNTIME in {"auto", "langgraph"} and not settings.DEMO_MODE:
        required.extend(["searxng", "federal_register"])
        if settings.BROWSER_RENDERING_ENABLED:
            required.append("browser")
    return {
        "status": "ready" if all(components[name]["status"] == "ready" for name in required) else "degraded",
        "runtime": settings.RESEARCH_RUNTIME,
        "execution_mode": "kafka",
        "components": components,
    }


@router.get("/investigations/{investigation_id}/research-trail", response_model=ResearchTrailResponse)
def research_trail(
    investigation_id: str,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> ResearchTrailResponse:
    return get_research_repository().get_trail(investigation_id, after_sequence, limit)


@router.get("/investigations/{investigation_id}/events", response_model=None)
async def research_events(
    request: Request,
    investigation_id: str,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    run_id: str | None = Query(default=None),
) -> Response:
    selected_run, after = parse_cursor(last_event_id, run_id)
    hub = getattr(request.app.state, "research_stream_hub", None)
    if hub is None:
        raise HTTPException(503, "Research streams unavailable.", headers={"Retry-After": "5"})
    subscription = await hub.subscribe(investigation_id, selected_run, after)
    if subscription is None:
        return Response(status_code=204, headers={"Cache-Control": "no-cache"})
    return ResearchStreamResponse(subscription)


@router.get("/investigations/{investigation_id}/runs/{run_id}/checkpoints")
def sanitized_checkpoints(investigation_id: str, run_id: str) -> dict:
    repo = get_research_repository()
    run = repo.get_run(run_id)
    if run is None or run.investigation_id != investigation_id:
        raise HTTPException(status_code=404, detail="Research run not found.")
    events = [event for event in repo.list_events(run_id, 0, 500) if event.event_type.startswith("node.")]
    return {"run_id": run_id, "checkpoints": [event.model_dump(mode="json") for event in events]}


@router.post("/investigations/{investigation_id}/runs/{run_id}/replay", response_model=ReplayResponse)
def replay_run(investigation_id: str, run_id: str) -> ReplayResponse:
    try:
        replay = get_research_manager().replay(investigation_id, run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Research run not found.") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ReplayResponse(run=replay, source_run_id=run_id)
