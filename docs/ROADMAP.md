# RhetoriQ Roadmap

RhetoriQ already has a working, tested MVP. This roadmap separates that product baseline from the larger distributed-system architecture described elsewhere in the repository.

The two tracks are intentionally developed together:

- The **Product and Agent Track** turns the current MVP into a polished, deployable resume project.
- The **Production Architecture Track** now includes PostgreSQL/pgvector, B2 research acquisition, and the B3 Kafka backbone; it continues toward Flink, specialized databases, Kubernetes, and cloud operations.

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
- SQLite-backed development persistence and PostgreSQL-backed production persistence for investigation plans, retrieved documents, intermediate artifacts, and final workspaces.
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

- The implemented A2 investigative runtime uses self-hosted LangGraph for autonomous orchestration and RhetoriQ-controlled tracing and evaluation.
- It uses pluggable self-operated search and an optional isolated browser adapter rather than managed search or browser SaaS integrations.
- No managed observability, search, browser, or orchestration SaaS is planned.
- Current verification baseline:
  - 202 non-integration backend tests pass and 8 optional tests skip locally;
  - PostgreSQL/pgvector integration tests are configured in CI but were not run locally without a PostgreSQL service;
  - Python source compilation passes;
  - the frontend production build and four frontend tests pass.

### Known MVP Boundary

The A2 runtime supports SearXNG discovery, but its availability on the reported public deployment is not yet verified in the A5 evidence record. The initial production topology excludes browser rendering; JavaScript-only sources remain a recorded retrieval limitation. The planner and retriever determine queries, follow-up searches, and retrieval lanes through the `SearchProvider` abstraction.

The current repository includes LangGraph, production containers, PostgreSQL/pgvector, and the B3 Kafka/Apicurio event backbone. It does **not** claim that React Query, Sigma.js, WebSockets, Flink, Elasticsearch, Neo4j, Kubernetes, Terraform, ArgoCD, Prometheus, or Grafana are implemented. The pgvector corpus path is opt-in and awaits production corpus rollout verification.

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

**Status: Completed**

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

#### Implementation record (2026-07-31)

The repository now contains the LangGraph state graph, durable checkpointer, Kafka-backed leased scheduler, idempotent action recovery, SearXNG/GDELT/Hacker News/canonical/browser/internal adapters, shared public-network policy, deterministic publication gate, recorded replay, audit APIs, SSE stream, interactive React Flow console, and the versioned 14-fixture A2 scorecard.

The phase is **Completed**. The implementation passes the full backend regression suite, frontend production build, dependency and compilation checks, Compose configuration validation, deterministic graph/recovery/replay tests, and every threshold in the committed 14-fixture A2 scorecard. Live provider demonstrations remain repeatable portfolio evidence rather than a blocker to the completed implementation phase.

---

### A3. Investigation Quality and Evaluation

**Status: Completed**

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

#### Implementation record (2026-08-30)

The repository now contains the versioned A3 claim-evidence verifier, persisted span-level audit records, source-independence and date-confidence signals, optional hosted second-opinion support, a fail-closed local NLI path, A2 publication-gate integration, explicit historical re-verification, and a claim-level frontend audit panel. The local `cross-encoder/nli-deberta-v3-base` model has been preloaded for the active development environment. Backend regression and frontend production-build checks pass. A captured-real-source scorecard and live-canary utility are committed; the 30-case curated corpus remains a future quality-expansion artifact by explicit product decision.

---

### A4. Frontend Completion

**Status: Completed (2026-08-30)**

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

#### Implementation record (2026-08-30)

Implemented the report-first investigation workspace with URL-addressable Report, Evidence, Narrative, and Method & audit views; source filtering and accessible source detail; source-aware narrative/timeline navigation; A3 audit preservation; SSE-driven workspace refresh with polling fallback; and client-side recent-investigation search and status filtering over the existing 12-record API limit. The frontend production build and the initial Vitest history-filter test pass. Browser end-to-end and visual-regression coverage are explicitly deferred to move directly into A5 deployment.

---

### A5. Deployable MVP

**Status: In Progress (2026-09-11)**

**Estimate: 2–3 weeks**

#### Goals

- Deploy a live, non-demo MVP on Railway: public frontend and FastAPI API, with private research services.
- Containerize the current frontend and backend.
- Add production-safe environment configuration, CORS restrictions, request limits, secret handling, structured logs, and health probes.
- Add CI that runs backend tests, Python compilation, documentation checks, and the frontend production build.
- Migrate investigation, research-checkpoint, and related durable state from SQLite to managed Neon PostgreSQL before launch, with repeatable migrations and rollback notes.
- Deploy SearXNG as a private service reachable only from the backend. The initial deployment deliberately excludes the Playwright browser-renderer service; unavailable JavaScript-rendered sources must be recorded as a retrieval limitation, not treated as a failure or bypassed.
- Deploy a public MVP with persistent PostgreSQL-backed investigation storage and documented operational limits.
- Publish a concise demo script and architecture summary suitable for a portfolio or interview.

#### Dependencies

- A1 documentation hardening.
- A2 live research runtime.
- A4 frontend acceptance checks.

#### Completion condition

Every push is automatically validated, a reproducible Railway deployment is documented, and a public live instance can complete or replay an investigation without manual database repair. The initial release uses PostgreSQL and does not depend on Playwright/browser rendering.

#### Implementation record (2026-09-11)

The deployment foundation is implemented: the frontend and API have
production container definitions, local Compose provisions PostgreSQL and
private SearXNG, committed PostgreSQL migrations run before API startup, and
CI validates backend, frontend, and documentation checks. The API applies
bounded per-client request limits, including a stricter investigation-start
limit; these in-process limits suit the initial single-API deployment. The
project owner reports that v1 is live on Railway with Neon PostgreSQL. The
public URLs, deployed commit, health responses, terminal SSE investigation,
workspace reload and replay after redeploy, and green CI run have not yet been
recorded in [A5_LAUNCH_EVIDENCE.md](A5_LAUNCH_EVIDENCE.md), so A5 stays In
Progress.

---

## Track B — Production Architecture

This track implements the larger distributed architecture described in [ARCHITECTURE.md](ARCHITECTURE.md), [KAFKA.md](KAFKA.md), [SERVICES.md](SERVICES.md), and [INFRASTRUCTURE.md](INFRASTRUCTURE.md). B1 has an opt-in implementation in progress; later phases remain planned until their runtime implementations and completion checks exist.

### B1. PostgreSQL and pgvector Migration

**Status: In Progress (initial PostgreSQL persistence is delivered as part of A5)**

**Estimate: 2–3 weeks**

#### Goals

- Define migrations for investigations, documents, artifacts, claims, receipts, sources, trending snapshots, and research checkpoints.
- Move durable workspace persistence from SQLite to PostgreSQL as an A5 deployment prerequisite.
- Move production vector retrieval to pgvector after the initial PostgreSQL migration, while preserving the current repository and service boundaries.
- Provide a repeatable development migration and seed workflow.

#### Dependencies

- Stable MVP schemas.
- A5 PostgreSQL persistence baseline.

#### Completion condition

The full backend test suite runs against PostgreSQL, persisted workspaces survive restarts, and retrieval queries return validated pgvector results. The A5 launch completion condition covers the first two requirements; pgvector remains the follow-on B1 milestone.

#### Implementation record (2026-09-14)

An additive Neon migration defines a canonical corpus keyed by the existing
`Document.id`, preserves serialized documents and source provenance, and stores
384-dimensional MiniLM embeddings with model and input-hash metadata. Research,
retrieval, and discovery documents are indexed through their repository
boundaries; discovery records remain uncitable leads. A resumable backfill,
post-backfill cosine index command, and opt-in `ENABLE_POSTGRES_VECTOR_SEARCH`
path are implemented. The existing internal search remains the default and is
the visible fallback when embeddings or the corpus are unavailable. CI is
configured for a pgvector-capable PostgreSQL service. The targeted pgvector
integration suite passes against the connected Neon target (8 passed), and its
schema migration, resumable backfill, HNSW cosine index, direct semantic query,
and feature-flagged retrieval path were verified. The legacy
`all-MiniLM-L6-v2` setting is normalized to the canonical model identifier
stored with corpus embeddings. This target currently contains validation
fixtures rather than a live investigation corpus, so the production feature
flag remains off pending a production data backfill and comparison. CockroachDB
migration remains a separate compatibility and data-migration milestone.

---

### B2. Agent-Led Web Research and Primary Sources

**Status: Completed (2026-09-16)**

**Estimate: 3–5 weeks**

#### Goals

- Make broad, live web discovery available to the investigator through the existing `SearchProvider` boundary.
- Let the planner choose approved search, canonical-page retrieval, internal-corpus, and one structured primary-source tool according to the evidence gap and remaining budget.
- Start with a first-party public-record API such as Federal Register or Congress.gov; use it as a queryable research tool, not a background crawler.
- Persist normalized documents, discovery receipts, canonical-fetch receipts, source timestamps, canonical URLs, collection timestamps, and provenance for reuse by later investigations.
- Preserve bounded retries, per-source rate limits, deduplication, partial-failure reporting, and visible retrieval limitations.
- Keep evidence source types separate from acquisition transports; discovery results remain uncitable leads until canonical evidence is retrieved and receipted.
- Defer scheduled RSS/Atom polling, social/public event streams, Reddit, and the broader connector-worker fleet to a later monitoring milestone after their policy and retention requirements are approved.

#### Dependencies

- B1 durable document storage.
- A working self-operated broad-search deployment or an explicitly documented fallback.
- Reviewed data-source and legal/terms-of-use constraints for the selected primary-source API.

#### Completion condition

An investigation can answer a new user question by selecting permitted live-web and primary-source tools, fetching canonical evidence where permitted, and persisting an auditable, schema-valid evidence trail. Tests cover tool selection, source-policy enforcement, malformed/provider-failure handling, deduplication, and the visible fallback when live search is unavailable. Scheduled monitoring connectors are explicitly out of scope.

#### Implementation record (2026-09-16)

The broad-search boundary now has an explicit SearXNG implementation and an explicit `SearchProviderUnavailable` result with internal-corpus fallback diagnostics. Canonical retrieval and the Federal Register first-party client preserve provider/native IDs, queries and cursors, canonical URLs, publication and collection timestamps, policy decisions, retrieval outcomes, evidence status, receipts, and limitations. Provider HTTP behavior includes bounded retries, jittered backoff, pagination, deduplication, and rate limiting. Retrieval tools produce transport-neutral acquisition records; Kafka normalization owns durable document persistence. Fixture coverage includes malformed responses, retries, pagination, policy enforcement, canonical provenance, and visible fallback behavior. A live Federal Register canary returned two schema-valid primary-source receipts on 2026-09-16.

---

### B3. Kafka Contracts and Replayable Ingestion

**Status: Implemented (2026-09-16; local Compose evidence pending Windows restart)**

**Estimate: 2–3 weeks**

#### Goals

- Implement versioned Kafka schemas and topic creation for raw documents, processed documents, detected signals, investigation requests, stage events, and completed reports.
- Define partition keys, consumer groups, retries, dead-letter topics, and idempotency rules.
- Adapt the B2 retrieval-tool contract and later scheduled connectors to event-driven operation.

#### Dependencies

- B2 normalized retrieval and primary-source tool contract.
- Local Docker-based Kafka environment.

#### Completion condition

A source event can be produced, consumed, replayed, and processed idempotently; incompatible schemas are rejected; and failed messages reach a dead-letter topic with diagnostic context.

#### Implementation record (2026-09-16)

The repository now implements the six versioned JSON Schema contracts and one `.dlq.v1` topic per consumed topic, committed Pydantic-generated schemas, Apicurio Confluent-compatible registration with `BACKWARD_TRANSITIVE` compatibility, Apache Kafka KRaft Compose services, transactional outbox and consumer ledgers, idempotent producers and consumers, bounded retry/DLQ behavior, audited controlled replay, Kafka-only investigation dispatch, ordered stage events, and exactly-one completion-event IDs. Ingestion and investigation run APIs return HTTP 202 after atomically queuing durable outbox work; no synchronous production fallback remains. Health reports broker/registry state, outbox backlog, retry counters, consumer-group state and lag, and DLQ depth.

The implementation and non-container verification gates are complete. On this workstation, `wsl --install --no-distribution` successfully enabled the required Windows component, but Docker Desktop still reports `HCS_E_HYPERV_NOT_INSTALLED` until Windows is restarted (or firmware virtualization is enabled if the error persists). The Compose end-to-end evidence and local SearXNG canary must be captured after that host restart; this is an environment verification item, not an application fallback.

---

### B4. Flink Processing and Anomaly Detection

**Status: In Progress**

One milestone-gated sprint; no separate calendar estimate. The approved scope,
contracts, operational requirements, and acceptance gates are recorded in
[B4_SPRINT.md](B4_SPRINT.md). Verification evidence is tracked in
[B4_ACCEPTANCE.md](B4_ACCEPTANCE.md).

#### Goals

- Replace the raw-document consumer with a pinned Flink 2.3.0 application
  cluster using Python 3.12, transactional Kafka sinks, and durable checkpoints.
- Normalize documents before independently scalable hosted enrichment;
  persist immutable Gemini/Groq extraction artifacts and normalized
  384-dimensional Gemini embeddings, with bounded retries and explicit budgets.
- Evaluate emerging six-hour and sustained 24-hour event-time horizons against
  seven aligned daily baseline buckets; deduplicate exact content and revise
  windows for allowed late events without duplicate topic cards.
- Project document lineage, signal revisions, horizon metrics, and evaluation
  heartbeats to PostgreSQL. Make fresh, healthy Flink projections the primary
  Narrative Radar feed with visible legacy fallback and manual investigation.

#### Dependencies

- B3 Kafka contracts.
- Stable embedding and normalization behavior.

#### Completion condition

All five sprint gates pass: contracts/cluster, deterministic enrichment,
stream intelligence, product cutover, and final stabilization. Repeated clean
replays must preserve payloads, event IDs, revisions, and semantic hashes;
checkpoint recovery, failure injection, sustained/burst load, a bounded live
provider canary, and complete regression checks must be recorded before this
phase is marked implemented. Docker Desktop availability is a prerequisite
for the Compose and recovery evidence.

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
| 2 | A2 Autonomous internet research | Completed |
| 3 | A3 Investigation quality and evaluation | Completed |
| 4 | A4 Frontend completion | Completed |
| 5 | A5 Deployable MVP foundation, including PostgreSQL persistence | In Progress |
| 6 | B1 pgvector migration and PostgreSQL hardening | In Progress |
| 7 | B2 Agent-led web research and primary sources | Completed |
| 8 | B3 Kafka contracts and replayable ingestion | Implemented; Compose evidence pending host restart |
| 9 | B4 Flink processing and anomaly detection | In Progress; milestone-gated sprint |
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
