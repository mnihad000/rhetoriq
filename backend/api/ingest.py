from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, field_validator

from models.document import Document
from services.document_store import live_store
from services.gdelt import GDELTIngestion, build_first_observed_label, build_timeline
from services.ingestion import IngestionCoordinator

router = APIRouter(prefix="/api")

_coordinator = IngestionCoordinator()
_gdelt = GDELTIngestion()


class IngestRequest(BaseModel):
    query: str
    start_date: str  # YYYY-MM-DD
    end_date: str  # YYYY-MM-DD
    include_hn: bool = True
    hn_num_results: int = 50

    @field_validator("start_date", "end_date")
    @classmethod
    def validate_date(cls, v: str) -> str:
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Date must be YYYY-MM-DD format")
        return v


class GDELTSearchResponse(BaseModel):
    query: str
    start_date: str
    end_date: str
    max_records: int
    stored: bool
    documents: list[Document]
    timeline: list[dict]
    first_observed_in_dataset: dict | None


class IngestionSubmission(BaseModel):
    collection_id: str
    accepted_at: datetime
    status: str
    event_ids: list[str]
    accepted_count: int
    query: str
    errors: list[str] = Field(default_factory=list)


def _parse_date(date_str: str) -> datetime:
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Date must be YYYY-MM-DD format") from exc


@router.post("/ingest", response_model=IngestionSubmission, status_code=status.HTTP_202_ACCEPTED)
def ingest(request: IngestRequest) -> IngestionSubmission:
    """
    Fetch real articles from GDELT + Hacker News for the given query and date range.
    Results are saved to the live DocumentStore and immediately available
    to all /api/narratives and /api/investigate endpoints.

    Both sources are free and require no API key.

    Example:
      POST /api/ingest
      {"query": "energy tax", "start_date": "2026-06-01", "end_date": "2026-06-19"}
    """
    start_dt = _parse_date(request.start_date)
    end_dt = _parse_date(request.end_date)

    if end_dt <= start_dt:
        raise HTTPException(status_code=400, detail="end_date must be after start_date")

    result = _coordinator.ingest(
        query=request.query,
        start_dt=start_dt,
        end_dt=end_dt,
        include_hn=request.include_hn,
        hn_num_results=request.hn_num_results,
    )
    return IngestionSubmission.model_validate(result)


@router.get("/gdelt/search", response_model=GDELTSearchResponse | IngestionSubmission)
def gdelt_search(
    response: Response,
    query: str = Query(..., min_length=2),
    start_date: str = Query(..., description="YYYY-MM-DD"),
    end_date: str = Query(..., description="YYYY-MM-DD"),
    max_records: int = Query(25, ge=1, le=250),
    store: bool = Query(True, description="Persist normalized documents to the live document store"),
) -> GDELTSearchResponse:
    """
    Search GDELT DOC 2.0, normalize results into our Document schema,
    build a day-level article timeline, and label the earliest article as
    "first observed in our dataset" rather than true origin.
    """
    start_dt = _parse_date(start_date)
    end_dt = _parse_date(end_date)

    if end_dt <= start_dt:
        raise HTTPException(status_code=400, detail="end_date must be after start_date")

    try:
        documents = _gdelt.fetch_articles(
            query=query,
            start_dt=start_dt,
            end_dt=end_dt,
            max_records=max_records,
        )
    except httpx.HTTPStatusError as exc:
        status_code = 503 if exc.response.status_code == 429 else 502
        detail = (
            "GDELT DOC 2.0 is rate-limiting this request right now. Retry shortly."
            if exc.response.status_code == 429
            else f"GDELT request failed with status {exc.response.status_code}."
        )
        raise HTTPException(status_code=status_code, detail=detail) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"GDELT request failed: {exc}") from exc

    if store:
        from uuid import uuid4
        collection_id = f"collection_{uuid4().hex}"
        event_ids = _coordinator.stage_documents(
            documents,
            producer="gdelt-search-api",
            correlation_id=collection_id,
        )
        response.status_code = status.HTTP_202_ACCEPTED
        return IngestionSubmission(
            collection_id=collection_id,
            accepted_at=datetime.now(timezone.utc),
            status="queued",
            event_ids=event_ids,
            accepted_count=len(event_ids),
            query=query,
            errors=[],
        )

    return GDELTSearchResponse(
        query=query,
        start_date=start_date,
        end_date=end_date,
        max_records=max_records,
        stored=store,
        documents=documents,
        timeline=build_timeline(documents),
        first_observed_in_dataset=build_first_observed_label(documents),
    )


@router.get("/store/status")
def store_status() -> dict:
    """Show how many live documents are in the store and their source breakdown."""
    docs = live_store.get_all()
    breakdown: dict[str, int] = {}
    for doc in docs:
        breakdown[doc.source_type] = breakdown.get(doc.source_type, 0) + 1

    return {
        "total": live_store.count(),
        "source_type_breakdown": breakdown,
        "doc_ids_sample": live_store.ids()[:10],
    }


@router.delete("/store")
def clear_store() -> dict:
    """Clear the live document store. Demo data is unaffected."""
    count = live_store.count()
    live_store.clear()
    return {"cleared": count}
