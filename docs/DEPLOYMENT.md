# Railway + Neon Deployment

RhetoriQ's initial public deployment is a non-demo research release built from
the repository's API and frontend containers. The intended topology is public
Railway `api` and `frontend` services, managed Neon PostgreSQL, and a private
SearXNG service. Browser rendering/Playwright is intentionally excluded from
this release. The deployment foundation is committed; public launch evidence
is tracked in [A5_LAUNCH_EVIDENCE.md](A5_LAUNCH_EVIDENCE.md), and A5 remains in
progress until that evidence is recorded.

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

Create one Railway project and one production environment for the Railway
services below; provision Neon separately:

| Service | Exposure | Source | Purpose |
|---|---|---|---|
| `Neon` | managed external database | Neon PostgreSQL | durable application state, LangGraph checkpoints, and future pgvector corpus |
| `searxng` | private | SearXNG image/config | web discovery only |
| `api` | public | repository `backend/` directory | FastAPI and embedded research execution |
| `frontend` | public | repository `frontend/` directory | static React application served by Nginx |

Do not create a Railway Postgres service or a browser-renderer service. Only
`api` and `frontend` receive a Railway public domain. The API reaches SearXNG
over its private Railway address and reaches Neon using the secret connection
string; the Neon host is not a Railway private-domain service.

### API variables

Set these in Railway's `api` service. Store the Neon connection string as a
secret variable; use Railway references for Railway-managed service addresses.

```text
DATABASE_URL=<Neon connection string, including sslmode=require>
DEPLOYMENT_ENV=production
DEMO_MODE=false
RESEARCH_RUNTIME=langgraph
RESEARCH_EXECUTION_MODE=embedded
SEARXNG_BASE_URL=http://${{searxng.RAILWAY_PRIVATE_DOMAIN}}:8080
BROWSER_RENDERING_ENABLED=false
ENABLE_POSTGRES_VECTOR_SEARCH=false
POSTGRES_VECTOR_SEARCH_TOP_K=8
POSTGRES_VECTOR_BACKFILL_BATCH_SIZE=100
CORS_ALLOW_ORIGINS=https://<frontend-public-domain>
REQUEST_RATE_LIMIT_PER_MINUTE=120
INVESTIGATION_START_LIMIT_PER_HOUR=5
REQUEST_RATE_LIMIT_MAX_CLIENTS=10000
# Set true only when Railway is the sole ingress to this API service.
TRUST_PROXY_HEADERS=true
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
rebuild when the API domain changes. For local Vite development, use
`VITE_API_BASE_URL` instead.

### Database ownership and TLS

Neon owns the PostgreSQL service and its backups. Railway stores the Neon
`DATABASE_URL` as an API secret. Keep the provider's TLS query parameter,
normally `sslmode=require`; do not copy the URL into source control or logs.

The API image runs `python -m migrations` before Uvicorn starts. That process
creates `schema_migrations` and applies each committed migration once. The API
release is the single migration owner. Before applying a schema change, create
a Neon backup/branch and verify the migration against the CI PostgreSQL job.

The B1 semantic corpus is additive and disabled by default during launch
closeout. New documents are stored as pending corpus rows without loading the
embedding model until backfill or feature enablement. First create a Neon
backup or non-production branch, apply the
migration there, and run the resumable backfill from the backend image with
`python -m services.postgres_corpus backfill --batch-size 100`. The command
prints a cursor after each batch and returns a nonzero status if any document
could not be embedded. Resume with `--after-id <last-cursor>` after an
interruption; use `--retry-unavailable` from the start to revisit rows whose
embedding was unavailable. After a successful backfill, run
`python -m services.postgres_corpus build-index`, compare cosine results with
the legacy path, and record the results. Only then back up the production Neon
branch, deploy the additive migration with the flag still `false`, repeat the
production backfill and index build, compare production retrieval, and set
`ENABLE_POSTGRES_VECTOR_SEARCH=true`.
Switching that flag back to `false` is the application rollback for a retrieval
regression; the additive corpus rows may remain for diagnosis.

## Launch checks

1. Open `https://<api-domain>/health`; it must report `demo_mode: false`.
2. Open `https://<api-domain>/api/research/health`; SearXNG, checkpointer, and
   embedded worker must be ready. The checkpointer probe tests database
   connectivity. Browser rendering is intentionally absent.
3. Open the frontend, start a short investigation, and confirm the SSE
   progress rail updates.
4. Redeploy the API and confirm the completed investigation and research trail
   remain available.
5. Confirm the frontend is using `PUBLIC_API_BASE_URL`, CORS allows only the
   frontend origin, and Railway waits for GitHub CI before deploys.
6. Record timestamps, deployed commit, response bodies/statuses, and the
   investigation/run identifiers in [A5_LAUNCH_EVIDENCE.md](A5_LAUNCH_EVIDENCE.md).

## Operational limits

Initial production is deliberately a single API instance because it executes
research in-process. PostgreSQL makes the state safe for a future dedicated
worker, but the worker split should be introduced and tested separately. The
existing fetch/domain/model budgets remain active and must be kept within the
chosen provider quotas. The API also limits each client to 120 requests per
minute and five investigation starts per hour by default. These limits are
in-process and are appropriate only while the API remains a single instance;
move the counters to shared storage before increasing the API replica count.

## Rollback

For an application regression, redeploy the last known-good Railway API and
frontend commits and leave the Neon schema in place when it is backward
compatible. The migration runner does not provide destructive down migrations.
For an incompatible schema change, stop promotion, use the tested forward fix,
or restore the pre-change Neon backup/branch according to the recorded incident
decision. After rollback, verify `/health`, `/api/research/health`, a persisted
workspace reload, and replay before reopening traffic.
