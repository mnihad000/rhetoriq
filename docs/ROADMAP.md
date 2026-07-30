# RhetoriQ Roadmap

RhetoriQ already has a working, tested MVP. This roadmap separates that product baseline from the larger distributed-system architecture described elsewhere in the repository.

The two tracks are intentionally developed together:

- The **Product and Agent Track** turns the current MVP into a polished, deployable resume project.
- The **Production Architecture Track** evolves the MVP toward Kafka, Flink, specialized databases, Kubernetes, and cloud infrastructure.

Status labels used throughout this document:

- **Completed** — implemented and verified in the current repository.
- **In Progress** — partially implemented or actively being hardened.
- **Next** — the next major implementation priority.
- **Planned** — designed but not implemented in the current runtime.
- **Optional** — valuable expansion that is not required for the core project.

---

## Current Working Baseline

**Status: Completed**

The current repository is a functional narrative-investigation MVP, not an empty scaffold.

### Frontend

- React 19, TypeScript, Vite, and Tailwind CSS application.
- Landing and dashboard experiences with a trending narrative feed, investigation entry points, and recent persisted investigations.
- Client-side routing for dashboard and investigation workspaces.
- Responsive investigation page with loading, running, unavailable, and completed states.
- Narrative provenance flowchart, timeline-oriented source exploration, evidence gaps, claim ledger, agent debate, and final report presentation.
- Live backend API integration with demo fallback behavior.
- Production frontend build passes.

### Backend API and Persistence

- FastAPI application with health, embedding health, ingestion, GDELT search, trending, investigation, graph, timeline, mutation, receipts, debate, and report functionality.
- SQLite-backed persistence for investigation plans, retrieved documents, intermediate artifacts, and final workspaces.
- Recent-investigation listing and workspace reload support.
- Background execution for supervised research runs with frontend polling.
- Optional Redis-backed caching, agent memory, phrase storage, and vector retrieval.
- Demo and live operating modes with graceful degradation when optional services are unavailable.

### Investigation and Agent Pipeline

- Query planner that produces topics, subquestions, search queries, retrieval lanes, uncertainty requirements, and stop conditions.
- Retriever with iterative query generation, source-lane planning, page fetching, normalization, deduplication, relevance scoring, and source profiling.
- Supervised multi-pass research loop with evidence-gap-driven retries.
- Deterministic and model-assisted artifacts for:
  - source diversity;
  - timeline construction;
  - counter-narratives;
  - narrative-family grouping;
  - provenance tracing;
  - gap analysis;
  - skeptic review;
  - analyst synthesis;
  - claim counterpoints;
  - receipts and claim grounding;
  - claim and gap ledgers;
  - agent debate;
  - final report assembly.
- Gemini, Groq, local Ollama, fixture, and deterministic mock model-client paths.
- Local embedding and optional Redis vector-memory support.

### Data and Trending Baseline

- Direct document ingestion and normalization.
- GDELT search and ingestion support.
- Hacker News ingestion through the public Algolia API.
- Trending discovery orchestration, persistence, ranking, snapshots, cache support, and investigation creation.
- Demo trending data when live discovery cannot produce a publishable snapshot.

### Research-stack decision

- The next investigative runtime will use self-hosted LangGraph for autonomous orchestration and RhetoriQ-controlled tracing and evaluation.
- It will use pluggable self-operated search and browser adapters rather than managed SaaS integrations.
- No managed observability, search, browser, or orchestration SaaS is planned. The runtime is not implemented yet.
- Current verification baseline:
  - 183 backend tests pass;
  - 10 backend tests are skipped for unavailable optional runtime dependencies;
  - Python source compilation passes;
  - the frontend production build passes.

### Known MVP Boundary

Live internet search is temporarily unconfigured. The current planner and retriever still determine queries, follow-up searches, and retrieval lanes through the `SearchProvider` abstraction. The next implementation phase will introduce the LangGraph autonomous research workflow.

The current MVP does **not** claim that LangGraph, React Query, Sigma.js, WebSockets, PostgreSQL, Kafka, Flink, Elasticsearch, Neo4j, Docker orchestration, Kubernetes, Terraform, ArgoCD, Prometheus, or Grafana are implemented.

---

## Track A — Product and Agent

### A1. Documentation and Baseline Hardening

**Status: Completed**

**Estimate: 1–2 weeks**

#### Goals

- Align the README and frontend, backend, agent, testing, and troubleshooting documentation with the code that actually exists.
- Remove mojibake and other encoding corruption from project-authored Markdown.
- Document accurate local startup commands, ports, environment variables, demo behavior, and optional dependencies.
- Clearly label current implementation versus target architecture throughout the documentation.
- Remove stale references to deleted sponsor integrations and unimplemented libraries.

#### Dependencies

- Current MVP baseline.
- Completed legacy SaaS removal.

#### Completion condition

All project-authored Markdown renders cleanly, contains no broken local links or conflict markers, and accurately describes both the current runtime and future architecture.

Completion record (2026-07-30): the project-authored Markdown was reconciled with the FastAPI/React MVP, stale implemented-as-planned backend and frontend descriptions were replaced, and local-link, conflict-marker, and encoding checks passed.

---

### A2. Autonomous Internet Research

**Status: Next**

**Estimate: 2–3 weeks**

#### Goals

- Implement a self-hosted LangGraph supervisor that autonomously selects a permitted search adapter, browser adapter, canonical-page retrieval, or internal-corpus retrieval by evidence gap.
- Normalize discovered sources into the existing `SearchResult`, page-fetching, document-normalization, receipt, and persistence pipeline.
- Preserve the source URL, title, snippet, search query/action summary, retrieval lane, provider metadata, and citation metadata.
- Add tool, time, spend, result-count, and per-domain budgets; bounded retries; duplicate suppression; partial-result behavior; and visible warnings.
- Add RhetoriQ-controlled tracing and evaluation without a managed observability SaaS.
- Enforce public-access-only research, prompt-injection handling, and an evidence-threshold publication gate.

#### Dependencies

- Existing planner, retriever, `SearchProvider`, page fetcher, and document normalizer.

#### Completion condition

A live investigation can start from a user question, autonomously choose permitted web-research tools, persist a structured evidence trail, and either publish a cited report that meets thresholds or return `insufficient_evidence`.

---

### A3. Investigation Quality and Evaluation

**Status: Planned**

**Estimate: 3–5 weeks**

#### Goals

- Improve semantic evidence matching beyond lexical overlap while retaining deterministic auditability.
- Strengthen source classification, primary-source detection, date confidence, provenance links, and duplicate-cluster handling.
- Improve contradiction analysis, claim rejection, claim softening, uncertainty language, and unsupported-claim filtering.
- Define evaluation fixtures for origin uncertainty, conflicting evidence, duplicated reporting, sparse evidence, unavailable pages, and misleading chronology.
- Add measurable quality checks for citation completeness, evidence diversity, provenance confidence, and overclaim prevention.

#### Dependencies

- A2 autonomous internet research.
- Stable retrieved-document and receipt contracts.

#### Completion condition

The evaluation suite demonstrates that difficult and low-evidence investigations produce cautious, cited output and reject or soften conclusions that the retrieved record cannot support.

---

### A4. Frontend Completion

**Status: Planned**

**Estimate: 2–4 weeks**

#### Goals

- Improve final-report readability and navigation between claims, receipts, sources, timeline events, gaps, and provenance nodes.
- Add source filtering, investigation search/history, and richer source-detail views.
- Replace polling with live progress updates after backend event streaming is implemented.
- Complete keyboard navigation, focus management, contrast checks, semantic labeling, and reduced-motion behavior.
- Add frontend component tests and end-to-end coverage for the main investigation flow.

#### Dependencies

- Stable API contracts from A2 and A3.
- Backend event-stream interface before replacing polling.

#### Completion condition

A user can create, monitor, reload, inspect, and navigate a real investigation end to end; accessibility checks pass; and automated tests cover the primary dashboard-to-report journey.

---

### A5. Deployable MVP

**Status: Planned**

**Estimate: 2–3 weeks**

#### Goals

- Containerize the current frontend and backend.
- Add production-safe environment configuration, CORS restrictions, request limits, secret handling, structured logs, and health probes.
- Add CI that runs backend tests, Python compilation, documentation checks, and the frontend production build.
- Deploy a public MVP with persistent investigation storage and documented operational limits.
- Publish a concise demo script and architecture summary suitable for a portfolio or interview.

#### Dependencies

- A1 documentation hardening.
- A2 live research for a fully live demo; demo-mode deployment may happen earlier.
- A4 frontend acceptance checks.

#### Completion condition

Every push is automatically validated, a reproducible containerized deployment is documented, and a public instance can complete or replay an investigation without manual database repair.

---

## Track B — Production Architecture

This track implements the larger distributed architecture described in [ARCHITECTURE.md](ARCHITECTURE.md), [KAFKA.md](KAFKA.md), [SERVICES.md](SERVICES.md), and [INFRASTRUCTURE.md](INFRASTRUCTURE.md). These phases remain unchecked because their runtime implementations do not yet exist in this repository.

### B1. PostgreSQL and pgvector Migration

**Status: Planned**

**Estimate: 2–3 weeks**

#### Goals

- Define migrations for investigations, documents, artifacts, claims, receipts, sources, and trending snapshots.
- Move durable workspace persistence from SQLite to PostgreSQL.
- Move production vector retrieval to pgvector while preserving the current repository and service boundaries.
- Provide a repeatable development migration and seed workflow.

#### Dependencies

- Stable MVP schemas.
- Containerized local PostgreSQL.

#### Completion condition

The full backend test suite runs against PostgreSQL, persisted workspaces survive restarts, and retrieval queries return validated pgvector results.

---

### B2. Source Connector Expansion

**Status: Planned**

**Estimate: 3–5 weeks**

#### Goals

- Implement a broad web-search provider behind the existing `SearchProvider` boundary.
- Implement API/feed-first production connectors for RSS/Atom, GDELT, official public records, and selected public event streams.
- Add Reddit only through approved official API access with documented retention and deletion handling.
- Retrieve speech and video evidence through first-party public records, metadata APIs, and authorized transcripts rather than assuming a public C-SPAN transcript API.
- Normalize every source into a shared document contract.
- Add rate-limit handling, pagination, checkpoints, deduplication, and source-specific tests.
- Preserve source timestamps, canonical URLs, collection timestamps, and provenance metadata.

#### Dependencies

- B1 durable document storage.
- Reviewed data-source and legal/terms-of-use constraints.

#### Completion condition

Each connector can resume after interruption, emits schema-valid documents, and passes fixture-based tests for pagination, rate limits, duplicates, and malformed source data.

---

### B3. Kafka Contracts and Replayable Ingestion

**Status: Planned**

**Estimate: 2–3 weeks**

#### Goals

- Implement versioned Kafka schemas and topic creation for raw documents, processed documents, detected signals, investigation requests, stage events, and completed reports.
- Define partition keys, consumer groups, retries, dead-letter topics, and idempotency rules.
- Adapt source connectors and downstream consumers to event-driven operation.

#### Dependencies

- B2 normalized connector contract.
- Local Docker-based Kafka environment.

#### Completion condition

A source event can be produced, consumed, replayed, and processed idempotently; incompatible schemas are rejected; and failed messages reach a dead-letter topic with diagnostic context.

---

### B4. Flink Processing and Anomaly Detection

**Status: Planned**

**Estimate: 3–5 weeks**

#### Goals

- Implement text normalization, entity and phrase extraction, embedding generation, and event-time handling.
- Implement windowed narrative-frequency baselines and configurable spike detection.
- Handle late events, checkpointing, replay, and deterministic output schemas.
- Publish processed documents and detected narrative signals through Kafka.

#### Dependencies

- B3 Kafka contracts.
- Stable embedding and normalization behavior.

#### Completion condition

Replaying a known event fixture produces reproducible processed documents and anomaly signals, including correct behavior for late, duplicate, and out-of-order events.

---

### B5. Elasticsearch and Neo4j

**Status: Planned**

**Estimate: 3–4 weeks**

#### Goals

- Index normalized documents and phrases in Elasticsearch for exact, filtered, and time-bounded search.
- Persist source, document, phrase, citation, mutation, and amplification relationships in Neo4j.
- Replace local graph approximations with graph-backed provenance queries where appropriate.
- Add consistency checks between PostgreSQL, Elasticsearch, and Neo4j records.

#### Dependencies

- B1 canonical relational identifiers.
- B4 processed-document events.

#### Completion condition

The same investigation can retrieve semantically related documents from pgvector, phrase matches from Elasticsearch, and an explainable provenance path from Neo4j using consistent document identifiers.

---

### B6. Kubernetes Local Deployment

**Status: Planned**

**Estimate: 2–4 weeks**

#### Goals

- Create manifests or Helm charts for connectors, Kafka consumers, Flink jobs, backend API, frontend, and required data services.
- Add ConfigMaps, Secrets, resource requests, readiness probes, liveness probes, and horizontal-scaling rules.
- Run the system locally with minikube or kind.

#### Dependencies

- Container images for deployable services.
- B3–B5 service boundaries and runtime dependencies.

#### Completion condition

A clean local cluster deployment starts all required workloads, passes probes, processes a seeded event end to end, and exposes the frontend and API through documented endpoints.

---

### B7. Terraform, CI/CD, and GitOps

**Status: Planned**

**Estimate: 2–4 weeks**

#### Goals

- Add GitHub Actions for tests, builds, image publishing, vulnerability checks, and manifest validation.
- Provision target infrastructure through Terraform modules.
- Deploy through ArgoCD with environment-specific configuration and rollback support.
- Prevent direct, undocumented production changes.

#### Dependencies

- B6 stable deployment manifests.
- Selected cloud and container registry.

#### Completion condition

A merge to the deployment branch creates validated images and causes ArgoCD to deploy the declared version, with a tested rollback path and no required manual cluster edits.

---

### B8. Observability and Operations

**Status: Planned**

**Estimate: 2–3 weeks**

#### Goals

- Instrument services with Prometheus-compatible metrics and structured logs.
- Add Grafana dashboards for connector health, Kafka lag, Flink throughput, retrieval latency, investigation duration, failures, and model usage.
- Add distributed tracing using an open standard rather than a sponsor-specific service.
- Define alerts, service-level objectives, backup procedures, replay procedures, and incident runbooks.

#### Dependencies

- B6 deployed services.
- B7 stable environments and release process.

#### Completion condition

Operators can identify where a failed or delayed investigation stalled, receive actionable alerts, restore persisted state, and replay affected events using documented runbooks.

---

### B9. Managed-Cloud Deployment

**Status: Optional**

**Estimate: 3–6 weeks**

#### Goals

- Deploy the production architecture to a selected cloud provider.
- Use managed services where they reduce operational risk without weakening portability.
- Add domain, TLS, secrets management, backups, cost budgets, and autoscaling.
- Run a documented load and failure-recovery exercise.

#### Dependencies

- B7 reproducible infrastructure and delivery.
- B8 observability and operational readiness.

#### Completion condition

The production environment survives a documented load test and controlled service failure, restores from backup, stays within its cost budget, and serves the public application over TLS.

---

## Recommended Sprint Order

Product and infrastructure work should alternate so the project remains demoable while architectural depth grows.

| Order | Focus | Status |
|---|---|---|
| 1 | A1 Documentation and baseline hardening | Completed |
| 2 | A2 Autonomous internet research | Next |
| 3 | A3 Investigation quality and evaluation | Planned |
| 4 | A5 Deployable MVP foundation | Planned |
| 5 | B1 PostgreSQL and pgvector migration | Planned |
| 6 | A4 Frontend completion | Planned |
| 7 | B2 Source connector expansion | Planned |
| 8 | B3 Kafka contracts and replayable ingestion | Planned |
| 9 | B4 Flink processing and anomaly detection | Planned |
| 10 | B5 Elasticsearch and Neo4j | Planned |
| 11 | B6 Kubernetes local deployment | Planned |
| 12 | B7 CI/CD and GitOps | Planned |
| 13 | B8 Observability and operations | Planned |
| 14 | B9 Managed-cloud deployment | Optional |

Phase estimates are planning ranges, not a single five-to-six-month promise. Actual timing depends on whether a sprint prioritizes product quality, distributed-systems learning, or infrastructure depth.

---

## Roadmap Rules

1. Keep the current MVP runnable while replacing individual subsystems.
2. Mark a phase completed only when its measurable completion condition passes.
3. Do not describe documented target architecture as implemented runtime behavior.
4. Preserve evidence-first language: “first observed in the available dataset” is not proof of definitive origin.
5. Require tests and migration or rollback notes for changes to persistence, event contracts, or public APIs.
6. Update this roadmap whenever a phase changes status or its completion condition changes materially.
