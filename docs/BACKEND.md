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
| Autonomous research | `GET /api/research/health`, `GET /api/investigations/{id}/research-trail`, `GET /api/investigations/{id}/events`, `GET /api/investigations/{id}/runs/{run_id}/checkpoints`, `POST /api/investigations/{id}/runs/{run_id}/replay` |

Investigation routes support retrieval, supervised runs, source-diversity, timeline, counter-narrative, family, analyst, receipts, debate, report, and optional memory recall artifacts. Request and response schemas are defined by the running application and exposed through `/docs`.

## Persistence and runtime behavior

- SQLite stores persisted investigation workspaces for local development. `INVESTIGATION_DB_PATH` defaults to `backend/investigations.sqlite3` when configured with a relative path. Production uses `DATABASE_URL` and the same repository boundaries over PostgreSQL.
- Redis is optional. When available, it can provide caching, phrase storage, vector retrieval, and agent memory; the API degrades when it is unavailable.
- `DEMO_MODE=true` is the default. It serves the demo-friendly experience and disables automatic background trending refreshes.
- With `DEMO_MODE=false`, the backend warms and periodically refreshes the trending feed. GDELT and Hacker News ingestion are the implemented live discovery paths.
- `RESEARCH_RUNTIME=langgraph` enables the durable A2 runtime. In local SQLite mode, checkpoints use a separate SQLite database; with `DATABASE_URL`, checkpoints and sanitized research records persist in PostgreSQL.
- Investigation execution is Kafka-only. The API atomically creates a queued run and `investigations.requested.v1` outbox event; the investigation consumer claims it with a renewable database lease. `python -m research_worker` is a legacy command name that launches that Kafka consumer, not a database-scanning fallback.
- With `DEPLOYMENT_ENV=production`, `DATABASE_URL` is required. The API image runs committed migrations before starting FastAPI; the migration runner records applied versions in `schema_migrations` and is safe to invoke again.
- Neon is the managed PostgreSQL target for the initial Railway deployment. Preserve Neon's TLS query parameter, normally `sslmode=require`, and keep the URL in Railway secret configuration.
- The additive B1 semantic corpus uses the existing 384-dimensional `sentence-transformers/all-MiniLM-L6-v2` model and is selected by `ENABLE_POSTGRES_VECTOR_SEARCH`. Keep that flag `false` until the Neon migration and resumable backfill are verified; the existing retrieval path remains the fallback when it is false or unavailable.

## Configuration

Settings load from `backend/.env` when present. No `.env` file is required for the default demo mode.

| Variable | Default | Purpose |
|---|---:|---|
| `DEMO_MODE` | `true` | Enables the deterministic demo-oriented runtime. |
| `INVESTIGATION_DB_PATH` | `investigations.sqlite3` | SQLite workspace database path. |
| `DATABASE_URL` | empty | PostgreSQL connection string. Required when `DEPLOYMENT_ENV=production`; use the Neon URL with TLS enabled. |
| `DEPLOYMENT_ENV` | `development` | Set to `production` for Railway; production requires `DATABASE_URL`. |
| `CORS_ALLOW_ORIGINS` | empty | Comma-separated allowed frontend origins. |
| `ENABLE_POSTGRES_VECTOR_SEARCH` | `false` | Selects the additive Neon pgvector retrieval path after migration/backfill verification. |
| `POSTGRES_VECTOR_SEARCH_TOP_K` | `8` | Maximum persisted semantic corpus results when the feature is enabled. |
| `POSTGRES_VECTOR_BACKFILL_BATCH_SIZE` | `100` | Batch size for the resumable corpus backfill command. |
| `GEMINI_API_KEY`, `GROQ_API_KEY` | empty | Optional model-provider credentials. |
| `REDIS_URL` | `redis://localhost:6379` | Optional Redis endpoint. |
| `EMBEDDING_MODEL_NAME` | `sentence-transformers/all-MiniLM-L6-v2` | Local embedding model identifier. |
| `GDELT_BASE_URL`, `GDELT_MAX_RECORDS` | configured defaults | GDELT discovery endpoint and result cap. |
| `HN_SEARCH_URL`, `HN_DEFAULT_RESULTS` | configured defaults | Hacker News Algolia endpoint and result cap. |
| `FETCH_TIMEOUT_SECONDS` | `20` | Canonical-page retrieval timeout. |
| `RESEARCH_RUNTIME` | `auto` | `auto`, `native`, or `langgraph`. |
| `RESEARCH_EXECUTION_MODE` | `kafka` | Deployment label; accepted runs always use Kafka. |
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Kafka bootstrap addresses. |
| `KAFKA_SCHEMA_REGISTRY_URL` | `http://localhost:8081/apis/ccompat/v7` | Apicurio Confluent-compatible API. |
| `KAFKA_SECURITY_PROTOCOL` | `PLAINTEXT` | Kafka security protocol; SASL/TLS credentials are optional environment secrets. |
| `RESEARCH_CHECKPOINT_DB_PATH` | `langgraph.sqlite3` | Separate LangGraph checkpoint database. |
| `SEARXNG_BASE_URL` | `http://127.0.0.1:8080` | Broad discovery service. |
| `BROWSER_SERVICE_URL` | `http://127.0.0.1:8010` | Isolated renderer service. |

Do not put credentials in source control. See [Troubleshooting](TROUBLESHOOTING.md) for optional-service failures and [Data sources](DATA_SOURCES.md) for source policy.

## Not yet implemented

The current API has no authentication requirement, WebSocket stream,
Elasticsearch client, or Neo4j client. Research progress is one-way
SSE with polling fallback. PostgreSQL persistence is supported for the A5
deployment. B1 semantic pgvector corpus retrieval is available behind its
feature flag but requires migration/backfill verification before production
enablement. These remaining concerns must not be assumed by callers or
deployment instructions.
