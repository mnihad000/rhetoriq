# RhetoriQ Troubleshooting

## Backend does not start

Run the backend from `backend/` so imports resolve correctly. Confirm the virtual environment is active and dependencies are installed:

```powershell
cd backend
..\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload
```

Use `http://127.0.0.1:8000/health` to confirm the API is running.

## Frontend cannot reach the API

Start the backend first, then start Vite from `frontend/`. The interface deliberately falls back to demo data for unavailable live content; inspect the browser network panel and backend logs to distinguish fallback behavior from a successful API response.

## Redis is unavailable

Redis is optional. Check `GET /api/redis/status` or `GET /api/health/redis`; the backend should continue using its non-Redis paths. To use Redis features, ensure `REDIS_URL` points to a reachable instance and restart the backend.

## Embeddings are unavailable or slow

Check `GET /health/embeddings`. The local embedding model can require a first-run download unless the environment is configured for local-only operation. Demo-mode flows should remain usable when optional embedding support is unavailable.

## Trending data is stale or empty

In demo mode, expected data is deterministic. With `DEMO_MODE=false`, inspect GDELT/Hacker News responses and backend logs, then use `POST /api/trending/refresh` to request a refresh. External providers can throttle or return no publishable snapshot; the API may fall back to demo-friendly results.

## Investigation is incomplete

Review the workspace status, receipts, and evidence gaps. A partial or unavailable external retrieval is a limitation, not a reason to invent a conclusion. Broad-web autonomous research is not configured in the current MVP.

## Not applicable to the MVP

Kafka lag, Flink checkpoints, Kubernetes probes, PostgreSQL migrations, Elasticsearch indexes, Neo4j queries, and WebSocket connection errors have no current local runtime because those components are planned work.
