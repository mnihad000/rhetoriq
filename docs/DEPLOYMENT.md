# Deployment

RhetoriQ requires the API/frontend, PostgreSQL/pgvector, private SearXNG, Apache Kafka, Apicurio Registry, topic initializer, outbox publisher, and role-scoped event workers. Browser rendering/Playwright remains optional. The root Compose file is the local production rehearsal. A managed deployment must provide equivalent private Kafka, registry, and worker services; deploying only the earlier API/frontend/Neon topology is no longer sufficient for investigation execution.

## Local production rehearsal

Copy the template, choose fresh local-only values, then start the stack:

```powershell
Copy-Item .env.production.example .env
docker compose up --build
```

Open `http://localhost:4173`, verify `http://localhost:8000/health`, and run a
live investigation. PostgreSQL data is stored in `postgres-data` and broker state in `kafka-data`.
Stop services with `docker compose down`; do not use `-v` unless you
intend to discard local database data.

The backend container runs committed migrations before it starts FastAPI.

## Railway services

Create one Railway project and one production environment for the Railway
services below; provision Neon separately:

| Service | Exposure | Source | Purpose |
|---|---|---|---|
| `Neon` | managed external database | Neon PostgreSQL | durable application state, LangGraph checkpoints, and future pgvector corpus |
| `searxng` | private | SearXNG image/config | web discovery only |
| `broker` | private | Apache Kafka 4.3.1 | KRaft event backbone |
| `schema-registry` | private | Apicurio Registry 3.3.0 | Confluent-compatible JSON Schema registry |
| `topic-init` | private one-shot | repository `backend/` directory | Topics, retention, schemas, and compatibility |
| `outbox-publisher` | private | repository `backend/` directory | Publish committed outbox records |
| event workers | private | repository `backend/` directory | Documents, signals, investigations, and projections |
| `api` | public | repository `backend/` directory | FastAPI, durable reads, and outbox writes |
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
RESEARCH_EXECUTION_MODE=kafka
SEARXNG_BASE_URL=http://${{searxng.RAILWAY_PRIVATE_DOMAIN}}:8080
KAFKA_BOOTSTRAP_SERVERS=<private Kafka bootstrap addresses>
KAFKA_SCHEMA_REGISTRY_URL=<private Apicurio URL>/apis/ccompat/v7
KAFKA_CLIENT_ID=rhetoriq-api
KAFKA_CONSUMER_GROUP_PREFIX=rhetoriq
KAFKA_SECURITY_PROTOCOL=<PLAINTEXT or managed TLS/SASL protocol>
KAFKA_SASL_USERNAME=<secret when required>
KAFKA_SASL_PASSWORD=<secret when required>
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
2. Open `https://<api-domain>/api/research/health`; SearXNG, checkpointer, Kafka, registry, outbox, and all consumer groups must be ready. Lag and DLQ counts must be numeric. Browser rendering may be intentionally absent.
3. Open the frontend, start a short investigation, and confirm the SSE
   progress rail updates.
4. Redeploy the API and confirm the completed investigation and research trail
   remain available.
5. Confirm the frontend is using `PUBLIC_API_BASE_URL`, CORS allows only the
   frontend origin, and Railway waits for GitHub CI before deploys.
6. Record timestamps, deployed commit, response bodies/statuses, and the
   investigation/run identifiers in [A5_LAUNCH_EVIDENCE.md](A5_LAUNCH_EVIDENCE.md).

## Operational limits

Research runs execute in Kafka consumers, so API replicas do not execute jobs or invoke a synchronous fallback. The existing fetch/domain/model budgets remain active and must be kept within the chosen provider quotas. The API also limits each client to 120 requests per
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
