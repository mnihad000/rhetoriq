from contextlib import asynccontextmanager
import logging
import threading
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.health import router as health_router
from api.ingest import router as ingest_router
from api.narratives import router as narratives_router
from api.redis_status import router as redis_status_router
from api.research import router as research_router
from api.trending import router as trending_router
from api.trending import _service as trending_service
from config import get_settings

settings = get_settings()


def _start_background_services() -> None:
    from services.autonomous_research import get_research_manager
    get_research_manager().resume_incomplete()

    if settings.DEMO_MODE:
        return

    # Warm the feed immediately on startup (runs in background thread)
    trending_service.ensure_warm_async()

    # Hourly refresh loop — keeps the feed fresh without waiting for a user request
    def _hourly_refresh() -> None:
        logger = logging.getLogger("rq.trending.scheduler")
        while True:
            time.sleep(3600)  # 1 hour
            try:
                logger.info("Hourly trending refresh starting")
                trending_service.refresh_now(is_reseed=False)
                logger.info("Hourly trending refresh complete")
            except Exception as exc:
                logger.error("Hourly trending refresh failed: %s", exc)

    threading.Thread(
        target=_hourly_refresh,
        daemon=True,
        name="rq-trending-hourly",
    ).start()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _start_background_services()
    yield


app = FastAPI(
    title="RhetoriQ API",
    description="Civic AI narrative intelligence platform. Demo mode active.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    # Local development remains convenient; production must declare its public
    # frontend origin explicitly instead of combining credentials with a wildcard.
    allow_origins=settings.cors_allow_origins or (["*"] if settings.DEMO_MODE else []),
    allow_credentials=bool(settings.cors_allow_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(ingest_router)
app.include_router(narratives_router)
app.include_router(redis_status_router)
app.include_router(research_router)
app.include_router(trending_router)


@app.get("/")
def root() -> dict:
    return {
        "name": "RhetoriQ",
        "demo_mode": settings.DEMO_MODE,
        "docs": "/docs",
        "endpoints": [
            "GET    /health",
            "GET    /api/gdelt/search",
            "POST   /api/ingest",
            "GET    /api/store/status",
            "DELETE /api/store",
            "GET    /api/narratives",
            "GET    /api/narratives/{id}",
            "GET    /api/narratives/{id}/timeline",
            "POST   /api/investigate  (query_text -> planner artifact)",
            "GET    /api/investigations  (list recent persisted investigations)",
            "GET    /api/investigations/{id}  (load persisted investigation workspace)",
            "GET    /api/investigations/{id}/memory  (load Redis agent memory context)",
            "GET    /api/investigations/{id}/similar-claims  (semantic claim recall from Redis memory)",
            "GET    /api/investigations/{id}/related-articles  (semantic article recall from Redis memory)",
            "POST   /api/investigations/{id}/run  (execute supervised research loop)",
            "POST   /api/investigations/{id}/retrieve  (run retriever agent)",
            "POST   /api/investigations/{id}/source-diversity  (build deterministic source diversity artifact)",
            "POST   /api/investigations/{id}/timeline  (build deterministic timeline artifact)",
            "POST   /api/investigations/{id}/counter-narratives  (build counter-frame artifact)",
            "POST   /api/investigations/{id}/family  (build narrative family artifact)",
            "POST   /api/investigations/{id}/analyst  (build synthesis artifact)",
            "POST   /api/investigations/{id}/claim-counterpoints  (build claim-level counterpoint artifact)",
            "POST   /api/investigations/{id}/receipts  (build claim grounding artifact)",
            "POST   /api/investigations/{id}/agent-debate  (build readable multi-agent debate summary)",
            "POST   /api/investigations/{id}/report  (assemble final investigation report)",
            "GET    /api/health/redis  (Redis connection and memory health)",
            "GET    /health/embeddings  (embedding model readiness and cache status)",
            "GET    /api/redis/status  (Redis health: cache, vector store, phrase store)",
            "GET    /api/graph/{narrative_id}",
            "GET    /api/mutations/{narrative_id}",
            "GET    /api/trending",
            "GET    /api/trending/status",
            "POST   /api/trending/refresh",
            "POST   /api/trending/{topic_id}/investigate",
        ],
    }
