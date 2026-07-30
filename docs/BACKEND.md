# RhetoriQ Backend

The current backend is a FastAPI application in `backend/`. It provides the MVP API for ingestion, trending discovery, investigation workspaces, evidence artifacts, and optional Redis-backed features.

## Run locally

From the repository root:

```powershell
cd backend
python -m venv ..\.venv
..\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload
```

The API listens on `http://127.0.0.1:8000`. Open `http://127.0.0.1:8000/docs` for the generated, authoritative endpoint reference.

## Current API surface

| Area | Routes |
|---|---|
| Health | `GET /`, `GET /health`, `GET /health/embeddings`, `GET /api/health/redis` |
| Ingestion | `POST /api/ingest`, `GET /api/gdelt/search`, `GET /api/store/status`, `DELETE /api/store` |
| Narratives | `GET /api/narratives`, `GET /api/narratives/{id}`, `GET /api/narratives/{id}/timeline`, `GET /api/graph/{id}`, `GET /api/mutations/{id}` |
| Investigations | `POST /api/investigate`, `GET /api/investigations`, `GET /api/investigations/{id}`, and artifact/run routes below that workspace path |
| Trending | `GET /api/trending`, `GET /api/trending/status`, `POST /api/trending/refresh`, `POST /api/trending/{topic_id}/investigate` |

Investigation routes support retrieval, supervised runs, source-diversity, timeline, counter-narrative, family, analyst, receipts, debate, report, and optional memory recall artifacts. Request and response schemas are defined by the running application and exposed through `/docs`.

## Persistence and runtime behavior

- SQLite stores persisted investigation workspaces. `INVESTIGATION_DB_PATH` defaults to `backend/investigations.sqlite3` when configured with a relative path.
- Redis is optional. When available, it can provide caching, phrase storage, vector retrieval, and agent memory; the API degrades when it is unavailable.
- `DEMO_MODE=true` is the default. It serves the demo-friendly experience and disables automatic background trending refreshes.
- With `DEMO_MODE=false`, the backend warms and periodically refreshes the trending feed. GDELT and Hacker News ingestion are the implemented live discovery paths.
- Broad-web search and the LangGraph autonomous research runtime are planned, not implemented.

## Configuration

Settings load from `backend/.env` when present. No `.env` file is required for the default demo mode.

| Variable | Default | Purpose |
|---|---:|---|
| `DEMO_MODE` | `true` | Enables the deterministic demo-oriented runtime. |
| `INVESTIGATION_DB_PATH` | `investigations.sqlite3` | SQLite workspace database path. |
| `GEMINI_API_KEY`, `GROQ_API_KEY` | empty | Optional model-provider credentials. |
| `REDIS_URL` | `redis://localhost:6379` | Optional Redis endpoint. |
| `EMBEDDING_MODEL_NAME` | `sentence-transformers/all-MiniLM-L6-v2` | Local embedding model identifier. |
| `GDELT_BASE_URL`, `GDELT_MAX_RECORDS` | configured defaults | GDELT discovery endpoint and result cap. |
| `HN_SEARCH_URL`, `HN_DEFAULT_RESULTS` | configured defaults | Hacker News Algolia endpoint and result cap. |
| `FETCH_TIMEOUT_SECONDS` | `20` | Canonical-page retrieval timeout. |

Do not put credentials in source control. See [Troubleshooting](TROUBLESHOOTING.md) for optional-service failures and [Data sources](DATA_SOURCES.md) for source policy.

## Not yet implemented

The current API has no authentication requirement, WebSocket event stream, PostgreSQL, Kafka consumer, Elasticsearch client, or Neo4j client. These are target-architecture concerns and must not be assumed by callers or deployment instructions.
