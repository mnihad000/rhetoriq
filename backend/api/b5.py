"""B5 investigation search, graph, and provenance endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from config import get_settings
from services.b5_search import SearchService

router = APIRouter(prefix="/api")
_service: SearchService | None = None


def get_b5_service() -> SearchService:
    global _service
    if _service is None:
        settings = get_settings()
        _service = SearchService(settings=settings)
    return _service


def _date(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid date: {value}") from exc


def _filters(
    source_id: str | None,
    source_type: str | None,
    language: str | None,
    published_after: str | None,
    published_before: str | None,
    collected_after: str | None,
    collected_before: str | None,
    include_unknown_dates: bool,
    include_leads: bool,
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "source_type": source_type,
        "language": language,
        "published_after": _date(published_after),
        "published_before": _date(published_before),
        "collected_after": _date(collected_after),
        "collected_before": _date(collected_before),
        "include_unknown_dates": include_unknown_dates,
        "include_leads": include_leads,
    }


@router.get("/investigations/{investigation_id}/search")
def investigation_search(
    investigation_id: str,
    q: str | None = Query(default=None, min_length=1),
    query: str | None = Query(default=None, min_length=1),
    mode: str = Query(default="hybrid"),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    source_id: str | None = Query(default=None),
    source_type: str | None = Query(default=None),
    language: str | None = Query(default=None),
    published_after: str | None = Query(default=None),
    published_before: str | None = Query(default=None),
    collected_after: str | None = Query(default=None),
    collected_before: str | None = Query(default=None),
    include_unknown_dates: bool = Query(default=False),
    include_leads: bool = Query(default=False),
) -> dict[str, Any]:
    search_query = q or query
    if not search_query:
        raise HTTPException(status_code=422, detail="Query parameter q or query is required.")
    try:
        return get_b5_service().search(
            search_query, mode=mode, investigation_id=investigation_id,
            filters=_filters(
                source_id, source_type, language, published_after,
                published_before, collected_after, collected_before,
                include_unknown_dates, include_leads,
            ),
            limit=limit, offset=offset,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Investigation not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/investigations/{investigation_id}/graph")
def investigation_graph(
    investigation_id: str,
    include_inferred: bool = Query(default=True),
    relationships: list[str] | None = Query(default=None),
) -> dict[str, Any]:
    try:
        return get_b5_service().graph(
            investigation_id, include_inferred=include_inferred,
            relationships=relationships,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Investigation not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/investigations/{investigation_id}/provenance-paths")
def investigation_paths(
    investigation_id: str,
    from_document_id: str = Query(..., min_length=1),
    to_document_id: str = Query(..., min_length=1),
    max_depth: int = Query(default=4, ge=1, le=6),
    include_inferred: bool = Query(default=False),
) -> dict[str, Any]:
    try:
        return get_b5_service().paths(
            investigation_id, from_document_id, to_document_id,
            max_depth=max_depth, include_inferred=include_inferred,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Investigation not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
