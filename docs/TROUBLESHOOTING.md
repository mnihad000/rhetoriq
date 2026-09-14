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

For local use, start the backend first, then start Vite from `frontend/` and
check `VITE_API_BASE_URL`. For Railway, inspect the generated
`runtime-config.js` value from `PUBLIC_API_BASE_URL`, confirm the API public
origin is reachable, and ensure the exact frontend origin is listed in
`CORS_ALLOW_ORIGINS`. The interface deliberately falls back to demo data for
unavailable live content; inspect the browser network panel and API logs to
distinguish fallback behavior from a successful API response.

## Neon or PostgreSQL connection fails

Check that Railway's API service has `DEPLOYMENT_ENV=production` and a secret
`DATABASE_URL` copied from Neon with its TLS query parameter, normally
`sslmode=require`. The API refuses to start in production without a database
URL. If startup reports a migration error, stop promotion, inspect the failed
migration and Neon logs, and use the tested forward fix or restore the
pre-change Neon backup/branch. Do not delete migration records manually.

## Redis is unavailable

Redis is optional. Check `GET /api/redis/status` or `GET /api/health/redis`; the backend should continue using its non-Redis paths. To use Redis features, ensure `REDIS_URL` points to a reachable instance and restart the backend.

## Embeddings are unavailable or slow

Check `GET /health/embeddings`. The local embedding model can require a first-run download unless the environment is configured for local-only operation. Demo-mode flows should remain usable when optional embedding support is unavailable.

## Trending data is stale or empty

In demo mode, expected data is deterministic. With `DEMO_MODE=false`, inspect GDELT/Hacker News responses and backend logs, then use `POST /api/trending/refresh` to request a refresh. External providers can throttle or return no publishable snapshot; the API may fall back to demo-friendly results.

## Investigation is incomplete

Review the workspace status, receipts, and evidence gaps. A partial or unavailable external retrieval is a limitation, not a reason to invent a conclusion. Broad-web autonomous research is not configured in the current MVP.

## Browser rendering in production

The initial public topology intentionally has no browser-renderer service and
sets `BROWSER_RENDERING_ENABLED=false`. JavaScript-only sources may therefore
appear as retrieval limitations. Do not point the public API at a local
browser service or add a public browser endpoint; use the documented local
research Compose stack when testing that adapter.

## Not applicable to the MVP

Kafka lag, Flink checkpoints, Kubernetes probes, Elasticsearch indexes,
Neo4j queries, and WebSocket connection errors have no current local runtime
because those components are planned work. PostgreSQL migrations do have a
local/CI check and are run by the production API startup.
