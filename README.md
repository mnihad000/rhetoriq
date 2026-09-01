# RhetoriQ

RhetoriQ is an evidence-first narrative investigation system. It detects public narrative signals, retrieves source material, maps how language changes and spreads, and produces reports whose material claims point back to inspectable evidence.

The product deliberately distinguishes **first observed in the available dataset** from true origin and does not treat correlation as proof of coordination.

## What the current repository implements

- FastAPI endpoints for ingestion, trending topics, investigations, timelines, graphs, mutations, receipts, and reports.
- GDELT DOC 2.0 ingestion for news discovery.
- Hacker News ingestion through the public Algolia API.
- Direct HTTP retrieval of canonical pages for evidence enrichment.
- A durable LangGraph research runtime with budgets, leases, checkpoints, idempotent actions, replay, SSE progress, and a deterministic publication gate.
- Self-hosted SearXNG discovery plus GDELT, Hacker News, canonical HTTP, internal-corpus, and isolated Playwright adapters.
- In-memory and SQLite-backed development storage, with optional Redis caching, vector search, and agent memory.
- A React and TypeScript investigation interface with a live graph, research rail, evidence gate, and replay controls.

Kafka, Flink, PostgreSQL/pgvector, Elasticsearch, Neo4j, Kubernetes, and the wider source-connector fleet remain target architecture. See [the roadmap](docs/ROADMAP.md) for exact implementation status.

## Collection strategy

RhetoriQ is **API/feed-first**, not scraper-first:

1. First-party APIs, public datasets, event streams, and RSS/Atom feeds provide structured discovery records.
2. A broad web-search API discovers relevant URLs outside those monitored sources.
3. The LangGraph investigator selects structured search or isolated browser research per evidence gap, subject to budgets and source policy.
4. RhetoriQ retrieves a canonical source page only when needed to create an evidence record.

A website, post, transcript, or official record is a source. An API, feed, or HTML fetch is the transport used to retrieve it.

Current source status is documented in [DATA_SOURCES.md](docs/DATA_SOURCES.md).

## Current data flow

```mermaid
flowchart LR
    G[GDELT DOC 2.0 API] --> I[Ingestion services]
    H[HN Algolia API] --> I
    S[Search and browser tools] --> D[LangGraph investigator]
    I --> N[Normalized Document records]
    D --> F[Canonical-page fetcher]
    F --> N
    N --> T[Trending and investigation pipelines]
    T --> A[FastAPI]
    A --> U[React frontend]
```

The target production flow adds durable connector checkpoints, Kafka replay, stream processing, and production data stores without changing the normalized document contract.

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

### Frontend

```powershell
cd frontend
npm install
npm run dev
```

### A2 research services

The research-only Compose stack supplies SearXNG and the isolated browser renderer:

```powershell
Copy-Item infra/research/.env.example infra/research/.env
docker compose -f infra/research/docker-compose.yml up -d
$env:RESEARCH_RUNTIME="langgraph"
$env:DEMO_MODE="false"
```

See [Autonomous Research](docs/AUTONOMOUS_RESEARCH.md) for worker mode, model configuration, budgets, replay, and security boundaries.

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
| `RESEARCH_EXECUTION_MODE` | `embedded` FastAPI execution or separate `worker` process. |
| `SEARXNG_BASE_URL` | Self-hosted broad-search endpoint. |
| `BROWSER_SERVICE_URL` | Isolated browser-rendering endpoint. |
| `REDIS_URL` | Optional Redis cache, phrase store, vector store, and memory. |
| `GDELT_BASE_URL` | GDELT DOC 2.0 endpoint. |
| `GDELT_MAX_RECORDS` | Maximum GDELT records requested per query. |
| `FETCH_TIMEOUT_SECONDS` | Canonical-page retrieval timeout. |

Future connectors must add their credentials only when implemented and approved. Reddit access, commercial search APIs, and licensed news products require terms and retention review before production use.

## Documentation

| Document | Purpose |
|---|---|
| [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md) | Concise system principles and investigation lifecycle. |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Current and target architecture, including collection boundaries. |
| [DATA_SOURCES.md](docs/DATA_SOURCES.md) | Source hierarchy, provider status, and compliance requirements. |
| [SERVICES.md](docs/SERVICES.md) | Implemented service boundaries and planned connectors. |
| [KAFKA.md](docs/KAFKA.md) | Planned replayable event contracts. |
| [INFRASTRUCTURE.md](docs/INFRASTRUCTURE.md) | Current deployment status and production principles. |
| [ROADMAP.md](docs/ROADMAP.md) | Delivery status and future phases. |
| [BACKEND.md](docs/BACKEND.md) | Backend contracts and endpoint reference. |
| [FRONTEND.md](docs/FRONTEND.md) | Frontend behavior and component model. |
| [TESTING.md](docs/TESTING.md) | Current verification commands and planned test layers. |
| [AUTONOMOUS_RESEARCH.md](docs/AUTONOMOUS_RESEARCH.md) | A2 architecture, setup, security, replay, and operating guide. |
| [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Local runtime and optional-service troubleshooting. |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Live Railway deployment, environment configuration, and launch checks. |
