from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import AsyncIterator

import httpx
from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from config import get_settings
from demo_data import ALL_DOCUMENTS
from models.research import ReplayResponse, ResearchTrailResponse
from services.autonomous_research import get_research_manager, get_research_repository

router = APIRouter(prefix="/api")


@router.get("/research/health")
def research_health() -> dict:
    settings = get_settings()
    components: dict[str, dict] = {}
    try:
        import langgraph
        components["langgraph"] = {"status": "ready"}
    except Exception as exc:
        components["langgraph"] = {"status": "missing", "detail": str(exc)}
    try:
        conn = sqlite3.connect(settings.RESEARCH_CHECKPOINT_DB_PATH)
        conn.execute("SELECT 1")
        conn.close()
        components["checkpointer"] = {"status": "ready"}
    except Exception as exc:
        components["checkpointer"] = {"status": "error", "detail": str(exc)}
    for name, url in {
        "searxng": f"{settings.SEARXNG_BASE_URL.rstrip('/')}/search?q=rhetoriq&format=json",
        "browser": f"{settings.BROWSER_SERVICE_URL.rstrip('/')}/health",
    }.items():
        try:
            headers = {"X-RhetoriQ-Browser-Token": settings.BROWSER_SERVICE_TOKEN} if name == "browser" and settings.BROWSER_SERVICE_TOKEN else {}
            response = httpx.get(url, headers=headers, timeout=2)
            components[name] = {"status": "ready" if response.is_success else "unavailable", "http_status": response.status_code}
        except Exception as exc:
            components[name] = {"status": "unavailable", "detail": str(exc)[:180]}
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
    audit = get_research_repository()
    workers = audit.recent_workers(max_age_seconds=settings.RESEARCH_LEASE_SECONDS)
    if settings.RESEARCH_EXECUTION_MODE == "embedded":
        components["worker"] = {"status": "ready", "mode": "embedded", "workers": workers}
    else:
        live_workers = [item for item in workers if item["mode"] == "worker"]
        components["worker"] = {
            "status": "ready" if live_workers else "unavailable",
            "mode": "worker",
            "workers": live_workers,
        }
    components["internal_retrieval"] = {
        "status": "ready",
        "normalized_document_count": len(ALL_DOCUMENTS),
        "vector_search_configured": settings.ENABLE_VECTOR_SEARCH,
    }
    required = ["langgraph", "checkpointer", "worker", "internal_retrieval"]
    if settings.RESEARCH_RUNTIME == "langgraph" and not settings.DEMO_MODE:
        required.extend(["searxng", "browser"])
    return {
        "status": "ready" if all(components[name]["status"] == "ready" for name in required) else "degraded",
        "runtime": settings.RESEARCH_RUNTIME,
        "execution_mode": settings.RESEARCH_EXECUTION_MODE,
        "components": components,
    }


@router.get("/investigations/{investigation_id}/research-trail", response_model=ResearchTrailResponse)
def research_trail(
    investigation_id: str,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> ResearchTrailResponse:
    return get_research_repository().get_trail(investigation_id, after_sequence, limit)


@router.get("/investigations/{investigation_id}/events")
async def research_events(
    investigation_id: str,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    if get_research_repository().get_latest_run(investigation_id) is None:
        raise HTTPException(status_code=404, detail="Research run not found.")
    try:
        after = int(last_event_id or 0)
    except ValueError:
        after = 0

    async def stream() -> AsyncIterator[str]:
        cursor = after
        idle_ticks = 0
        while True:
            trail = get_research_repository().get_trail(investigation_id, cursor, 100)
            for event in trail.events:
                cursor = event.sequence
                yield f"id: {event.sequence}\nevent: {event.event_type}\ndata: {json.dumps(event.model_dump(mode='json'))}\n\n"
                idle_ticks = 0
            run = trail.run
            if run and run.status not in {"queued", "running"} and not trail.events:
                break
            await asyncio.sleep(1)
            idle_ticks += 1
            if idle_ticks >= 15:
                yield ": heartbeat\n\n"
                idle_ticks = 0

    return StreamingResponse(
        stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
