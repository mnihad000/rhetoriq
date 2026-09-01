from fastapi import APIRouter, HTTPException, Query

from config import get_settings
from models.trending import TrendingFeedResponse, TrendingInvestigationResponse, TrendingStatusResponse
from services.investigation_repository import InvestigationRepository
from services.trending_repository import TrendingRepository
from services.trending_runtime import TrendingRuntimeStore
from services.trending_service import TrendingService

router = APIRouter(prefix="/api")

_settings = get_settings()
_trending_repository = TrendingRepository(_settings.persistence_target)
_runtime_store = TrendingRuntimeStore(_settings.REDIS_URL)
_investigation_repository = InvestigationRepository(_settings.persistence_target)
_service = TrendingService(
    repository=_trending_repository,
    runtime_store=_runtime_store,
)


@router.get("/trending", response_model=TrendingFeedResponse)
def get_trending(limit: int = Query(default=6, ge=1, le=12)) -> TrendingFeedResponse:
    return _service.get_feed(limit=limit)


@router.get("/trending/status", response_model=TrendingStatusResponse)
def get_trending_status() -> TrendingStatusResponse:
    return _service.get_status()


@router.post("/trending/refresh", response_model=TrendingFeedResponse)
def refresh_trending(reseed: bool = Query(default=False)) -> TrendingFeedResponse:
    try:
        snapshot = _service.refresh_now(is_reseed=reseed)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Trending refresh failed: {exc}") from exc
    return TrendingFeedResponse(
        state=snapshot.state,
        generated_at=snapshot.generated_at,
        fresh_until=snapshot.fresh_until,
        last_completed_run_at=snapshot.last_completed_run_at,
        last_reseed_at=snapshot.last_reseed_at,
        warning=snapshot.warning,
        topics=snapshot.topics,
    )


@router.post("/trending/{topic_id}/investigate", response_model=TrendingInvestigationResponse)
def investigate_trending_topic(topic_id: str) -> TrendingInvestigationResponse:
    try:
        return _service.start_investigation_for_topic(topic_id, _investigation_repository)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Unable to start investigation: {exc}") from exc

