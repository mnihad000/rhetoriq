from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import b5


def test_b5_api_validation_and_not_found(monkeypatch):
    class Service:
        def search(self, *args, **kwargs):
            raise KeyError("missing")

    monkeypatch.setattr(b5, "_service", Service())
    app = FastAPI()
    app.include_router(b5.router)
    client = TestClient(app)
    assert client.get("/api/investigations/missing/search?q=x").status_code == 404
    assert client.get("/api/investigations/missing/search?q=x&limit=101").status_code == 422
    assert client.get("/api/investigations/missing/search?q=x&offset=10001").status_code == 422
    assert client.get("/api/investigations/missing/search?q=x&mode=unknown").status_code == 422
    assert client.get("/api/investigations/missing/search?q=x&published_after=2026-09-17&published_before=2026-09-01").status_code == 422
    assert client.get("/api/investigations/missing/graph?relationships=arbitrary_cypher").status_code == 422


def test_b5_api_routes_forward_filters(monkeypatch):
    seen = {}

    class Service:
        def search(self, *args, **kwargs):
            seen.update(kwargs)
            return {"results": [], "complete": True}

        def graph(self, *args, **kwargs):
            return {"nodes": [], "edges": []}

        def paths(self, *args, **kwargs):
            return {"paths": []}

    monkeypatch.setattr(b5, "_service", Service())
    app = FastAPI()
    app.include_router(b5.router)
    client = TestClient(app)
    response = client.get("/api/investigations/inv_1/search?q=tax&source_type=national_news&include_leads=true")
    assert response.status_code == 200
    assert seen["filters"]["source_type"] == "national_news"
    assert seen["filters"]["include_leads"] is True
    assert client.get("/api/investigations/inv_1/graph").status_code == 200
    assert client.get("/api/investigations/inv_1/provenance-paths?from_document_id=d1&to_document_id=d2&max_depth=7").status_code == 422
