# Live Railway Deployment

RhetoriQ's initial public deployment is live research, not demo mode. It uses
one API service with embedded research execution, managed PostgreSQL, and a
private SearXNG service. Browser rendering/Playwright is intentionally not
deployed in this release.

## Local production rehearsal

Copy the template, choose fresh local-only values, then start the stack:

```powershell
Copy-Item .env.production.example .env
docker compose up --build
```

Open `http://localhost:4173`, verify `http://localhost:8000/health`, and run a
live investigation. PostgreSQL data is stored in the named `postgres-data`
volume. Stop services with `docker compose down`; do not use `-v` unless you
intend to discard local database data.

The backend container runs committed migrations before it starts FastAPI.

## Railway services

Create one Railway project and one production environment with these services:

| Service | Exposure | Source | Purpose |
|---|---|---|---|
| `Postgres` | private | Railway PostgreSQL | durable application state and LangGraph checkpoints |
| `searxng` | private | SearXNG image/config | web discovery only |
| `api` | public | repository `backend/` directory | FastAPI and embedded research execution |
| `frontend` | public | repository `frontend/` directory | static React application served by Nginx |

Do not create a browser-renderer service. Only `api` and `frontend` receive a
Railway public domain. Railway services communicate privately using their
`*.railway.internal` domains.

### API variables

Set these in Railway's `api` service. Use Railway reference variables rather
than copying connection credentials.

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
DEPLOYMENT_ENV=production
DEMO_MODE=false
RESEARCH_RUNTIME=langgraph
RESEARCH_EXECUTION_MODE=embedded
SEARXNG_BASE_URL=http://${{searxng.RAILWAY_PRIVATE_DOMAIN}}:8080
BROWSER_RENDERING_ENABLED=false
CORS_ALLOW_ORIGINS=https://<frontend-public-domain>
GEMINI_API_KEY=<secret, if using Gemini>
GROQ_API_KEY=<secret, if using Groq>
```

Set at least one hosted-model secret before running an investigation. Never
copy an existing local `.env` file to Railway or commit it.

### SearXNG variables

Deploy SearXNG from the repository with `infra/research/` as its Railway root
directory and `searxng/Dockerfile` as its Dockerfile path. That image copies
the committed `searxng-settings.yml` into the service. Set a newly generated
`SEARXNG_SECRET`, and do not generate a public domain for this service.

### Frontend variables

Set this runtime variable after generating the API public domain:

```text
PUBLIC_API_BASE_URL=https://<api-public-domain>
```

The frontend reads this value at container startup, so it does not need a
rebuild when the API domain changes.

## Launch checks

1. Open `https://<api-domain>/health`; it must report `demo_mode: false`.
2. Open `https://<api-domain>/api/research/health`; SearXNG, checkpointer, and
   embedded worker must be ready. Browser rendering is intentionally absent.
3. Open the frontend, start a short investigation, and confirm the SSE
   progress rail updates.
4. Redeploy the API and confirm the completed investigation and research trail
   remain available.
5. Enable Railway's option to wait for GitHub CI before deploys.

## Operational limits

Initial production is deliberately a single API instance because it executes
research in-process. PostgreSQL makes the state safe for a future dedicated
worker, but the worker split should be introduced and tested separately. The
existing fetch/domain/model budgets remain active and must be kept within the
chosen provider quotas.
