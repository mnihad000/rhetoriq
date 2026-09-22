# RhetoriQ

RhetoriQ is an evidence-first narrative investigation system. It detects public narrative signals, retrieves source material, maps how language changes and spreads, and produces reports whose material claims point back to inspectable evidence.

The product deliberately distinguishes **first observed in the available dataset** from true origin and does not treat correlation as proof of coordination.

## What the current repository implements

The product, Kafka/Flink pipeline, and B5 projection code are implemented in
the repository. Actual-stack qualification, public deployment evidence, and
Kubernetes/cloud work are tracked separately; code presence is not presented as
proof that those gates have passed.

- FastAPI endpoints for ingestion, trending topics, investigations, timelines, graphs, mutations, receipts, and reports.
- GDELT DOC 2.0 ingestion for news discovery.
- Hacker News ingestion through the public Algolia API.
- Direct HTTP retrieval of canonical pages for evidence enrichment.
- A durable LangGraph research runtime with budgets, leases, checkpoints, idempotent actions, replay, SSE progress, and a deterministic publication gate.
- Self-hosted SearXNG discovery plus GDELT, Hacker News, canonical HTTP, internal-corpus, and an isolated Playwright adapter for local research only.
- Federal Register first-party research with policy-aware receipts, retries, pagination, deduplication, and visible limitations.
- Apache Kafka KRaft and Apicurio Registry with 12 versioned primary topics and
  matching DLQs, a transactional outbox, idempotent consumers, controlled
  replay, and Kafka-only investigation dispatch.
- SQLite-backed development storage and PostgreSQL/pgvector production
  persistence, with optional Redis capabilities.
- A React and TypeScript investigation interface with a live graph, research rail, evidence gate, and replay controls.
- Flink document processing and narrative signals plus B5 Elasticsearch,
  Neo4j, MiniLM/pgvector, and Redis projections behind acceptance-gated flags.
- Production containers, committed PostgreSQL migrations, and CI checks for
  backend, frontend, schemas, documentation, and migration compatibility.

Kubernetes manifests, Terraform/GitOps, production observability, and the
recurring-monitoring connector fleet remain roadmap work. See the
[documentation index](docs/README.md) and [roadmap](docs/ROADMAP.md) for current
status and acceptance boundaries.

## Research strategy

RhetoriQ is **agent-led and source-policy-first**, not crawler-first:

1. A user question starts a bounded investigation; the LangGraph investigator selects live broad-web search, internal-corpus recall, canonical-page retrieval, or an approved primary-source API for each evidence gap.
2. Search results and API records are discovery leads, not automatically evidence.
3. RhetoriQ retrieves a canonical source page when permitted and needed to create an evidence record, then preserves receipts and limitations.
4. RSS/Atom polling, public event streams, and other scheduled connectors provide continuous monitoring alongside on-demand investigations.

A website, post, transcript, or official record is a source. An API, feed, or HTML fetch is the transport used to retrieve it.

Current source status is documented in [DATA_SOURCES.md](docs/DATA_SOURCES.md).

## Current data flow

```mermaid
flowchart LR
    G[GDELT DOC 2.0 API] --> I[Ingestion services]
    H[HN Algolia API] --> I
    S[Search and browser tools] --> D[LangGraph investigator]
    I --> O[Transactional outbox]
    O --> K[(Kafka)]
    D --> F[Canonical-page fetcher]
    F --> O
    K --> N[Normalized Document records]
    N --> T[Trending and investigation pipelines]
    T --> A[FastAPI]
    A --> U[React frontend]
```

The implemented flow uses durable Kafka replay, PostgreSQL authority, Flink
processing, and optional specialized search and graph projections built around
the normalized document contract. Recurring monitoring connectors remain
future work.

## Repository layout

```text
rhetoriq/
|-- backend/
|   |-- agents/       # planning, retrieval, synthesis, and receipts
|   |-- api/          # FastAPI route modules
|   |-- models/       # shared Pydantic contracts
|   |-- services/     # ingestion, retrieval, analysis, and persistence
|   `-- tests/
|-- frontend/         # React, TypeScript, and Vite application
|-- docs/             # design and operating documentation
|-- SYSTEM_DESIGN.md
`-- README.md
```

## Local development

### Backend

```powershell
cd backend
python -m venv ..\.venv
..\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload
```

The API is available at `http://127.0.0.1:8000`; interactive documentation is at `/docs`.

The running OpenAPI document is the authoritative endpoint reference. The API
includes health, ingestion, narratives, trending, investigations, research
events/replay, evidence search, and provenance-path routes. Accepted ingestion
and investigation work is Kafka-only; the API commits an outbox record and
returns without executing the job synchronously.

### Frontend

```powershell
cd frontend
npm install
npm run dev
```

Vite normally serves `http://127.0.0.1:5173`. Routes are `/`, `/dashboard`, and
`/investigation/:id`. The production Nginx image reads `PUBLIC_API_BASE_URL` at
container startup; local Vite development uses `VITE_API_BASE_URL`.

### Full local stack

The root Compose stack supplies PostgreSQL/pgvector, Kafka, Apicurio Registry,
SearXNG, topic initialization, the outbox publisher, role-scoped workers,
Flink, API, and frontend:

```powershell
$env:POSTGRES_PASSWORD="<local-secret>"
$env:SEARXNG_SECRET="<local-secret>"
docker compose up --build -d
docker compose ps
```

See [Architecture](docs/ARCHITECTURE.md) for research and service boundaries and
[Operations](docs/OPERATIONS.md) for startup, replay, and recovery.

### Tests

```powershell
pytest backend/tests
cd frontend
npm run build
```

## Important configuration

Settings are loaded from `backend/.env` when present.

| Variable | Purpose |
|---|---|
| `DEMO_MODE` | Use the bundled demo corpus and skip background live refreshes. |
| `GEMINI_API_KEY` | Optional Gemini model access. |
| `GROQ_API_KEY` | Optional Groq model access. |
| `RESEARCH_RUNTIME` | `auto`, `native`, or `langgraph` runtime selection. |
| `RESEARCH_EXECUTION_MODE` | Deployment label; execution is Kafka-only and defaults to `kafka`. |
| `KAFKA_BOOTSTRAP_SERVERS` | Kafka bootstrap addresses. |
| `KAFKA_SCHEMA_REGISTRY_URL` | Apicurio Confluent-compatible API base URL. |
| `KAFKA_CLIENT_ID` / `KAFKA_CONSUMER_GROUP_PREFIX` | Producer and consumer identity prefixes. |
| `KAFKA_SECURITY_PROTOCOL` | Kafka transport security protocol. |
| `KAFKA_SASL_USERNAME` / `KAFKA_SASL_PASSWORD` | Optional SASL credentials; never written to events or logs. |
| `KAFKA_TOPIC_PREFIX` | Optional environment-specific physical topic prefix. |
| `KAFKA_RETRY_MAX_ATTEMPTS` / `KAFKA_RETRY_BACKOFF_SECONDS` | Bounded consumer/producer retry policy. |
| `SEARXNG_BASE_URL` | Self-hosted broad-search endpoint. |
| `BROWSER_SERVICE_URL` | Isolated browser-rendering endpoint. |
| `BROWSER_RENDERING_ENABLED` | Keep `false` in the initial public deployment; enables the local browser adapter only when explicitly configured. |
| `DATABASE_URL` | PostgreSQL connection string for production persistence; use the managed Neon value and keep `sslmode=require` when supplied by Neon. |
| `DEPLOYMENT_ENV` | Set to `production` on the Railway API service; production startup requires `DATABASE_URL`. |
| `CORS_ALLOW_ORIGINS` | Comma-separated public frontend origin(s) allowed by the API. |
| `ENABLE_POSTGRES_VECTOR_SEARCH` | Feature flag for the additive Neon pgvector retrieval path; keep `false` until migration, backfill, and comparison checks pass. |
| `POSTGRES_VECTOR_SEARCH_TOP_K` | Maximum persisted semantic corpus results when the Neon path is enabled. |
| `POSTGRES_VECTOR_BACKFILL_BATCH_SIZE` | Resumable Neon corpus backfill batch size. |
| `REDIS_URL` | Optional Redis cache, phrase store, vector store, and memory. |
| `GDELT_BASE_URL` | GDELT DOC 2.0 endpoint. |
| `GDELT_MAX_RECORDS` | Maximum GDELT records requested per query. |
| `FETCH_TIMEOUT_SECONDS` | Canonical-page retrieval timeout. |

Connector credentials are configured only for approved deployments. Reddit access, commercial search APIs, and licensed news products require terms and retention review before production use.

## Documentation

| Document | Purpose |
|---|---|
| [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md) | Concise system principles and investigation lifecycle. |
| [Documentation index](docs/README.md) | Entry point for all durable project documentation. |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Current topology, services, research workflow, processing, and storage boundaries. |
| [DATA_SOURCES.md](docs/DATA_SOURCES.md) | Source hierarchy, provider status, and compliance requirements. |
| [KAFKA.md](docs/KAFKA.md) | Implemented replayable event contracts and operations. |
| [OPERATIONS.md](docs/OPERATIONS.md) | Startup, health, replay, recovery, projection administration, and troubleshooting. |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Public release, rollback, Kubernetes boundary, and AWS demonstration strategy. |
| [PRE_B6_GUIDE.md](docs/PRE_B6_GUIDE.md) | Current workstation progress and readiness sequence before Kubernetes. |
| [B6_OPERATIONS.md](docs/B6_OPERATIONS.md) | Implemented full-stack Helm/EKS runbook, current blockers, evidence gates, and teardown. |
| [TESTING.md](docs/TESTING.md) | Routine checks and B3–B5 acceptance gates. |
| [100K stress-test plan](docs/100k%20stress%20test%20plan.md) | Standalone daily-capacity experiment. |
| [ROADMAP.md](docs/ROADMAP.md) | Delivery status, remaining acceptance, and future phases. |
