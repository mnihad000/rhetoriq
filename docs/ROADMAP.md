# RhetoriQ Roadmap

RhetoriQ has a working evidence-first product plus implemented Kafka, Flink, and
B5 projection code. This roadmap distinguishes four different states:

- **Complete:** implementation and required acceptance evidence are recorded.
- **Implemented:** code and offline checks exist, but required actual-stack
  evidence is incomplete.
- **In progress:** active work or an incomplete release gate.
- **Planned:** design direction without the required repository artifacts.

## Current baseline

The repository contains:

- a React/TypeScript investigation interface with dashboard, live research
  progress, persisted workspaces, reports, evidence, timelines, and provenance;
- a FastAPI backend with SQLite development persistence and PostgreSQL/pgvector
  production persistence;
- bounded LangGraph research using SearXNG, Federal Register, GDELT, Hacker News,
  canonical fetching, optional browser rendering, and internal retrieval;
- versioned claim/evidence verification, receipts, publication gates, and
  explicit insufficient-evidence outcomes;
- a Kafka/Apicurio event backbone, transactional outbox, role-scoped consumers,
  retries/DLQs, replay, and Kafka-only accepted investigation execution;
- a Flink/enrichment implementation for stateful document processing and
  narrative signals;
- B5 Elasticsearch, Neo4j, MiniLM/pgvector, and Redis projections with drift,
  repair, rebuild, generation cutover, and rollback tooling.

Actual B3–B5 runtime qualification remains separate from implementation.
Feature flags keep Flink-primary trending and B5 retrieval disabled until their
gates pass. B6 Helm and ephemeral-EKS Terraform artifacts are implemented, but
no kind/EKS runtime evidence has been recorded. GitOps and production
observability are not implemented.

## Milestone status

| Milestone | Status | Durable result or remaining gate |
| --- | --- | --- |
| A1 Documentation baseline | Complete | Current/planned behavior is separated and documentation checks are established. |
| A2 Autonomous research | Complete | Bounded LangGraph workflow, durable state, approved tools, receipts, replay, and deterministic benchmark. |
| A3 Claim/evidence verification | Complete by product decision | Fail-closed verifier and UI exist; the curated 30-case real-source quality corpus remains future quality work. |
| A4 Frontend completion | Complete | Progressive investigation workspace, live updates, filtering, accessibility, and frontend tests/build. |
| A5 Public MVP | In progress | Public URLs, health, persisted investigation, redeploy/reload, replay, and CI evidence remain unrecorded. |
| B1 PostgreSQL/pgvector | Implemented, opt-in | Migration, repository, backfill, and vector path exist; production enablement requires live backfill and comparison. |
| B2 Agent-led sources | Complete | SearXNG, canonical fetch, Federal Register, policy, receipts, rate limits, and visible fallbacks. |
| B3 Kafka contracts | Implemented | Contracts, registry, outbox, consumers, DLQs, replay, and health exist; actual delivery/recovery evidence remains. |
| B4 Flink signals | Implemented | Flink topology, enrichment artifacts, event-time windows, projections, and product fallback exist; runtime recovery/load/live-canary gates remain. |
| B5 Recoverable projections | Implemented | Immutable authority, ES/Neo4j/MiniLM projections, cache, product UI, drift/repair/rebuild/cutover tooling exist; actual-stack acceptance remains. |
| B6 Local Kubernetes | Implemented, unqualified | Full-topology Helm profiles, deployment contracts, PKI, policies, storage, smoke/evidence Jobs, and guarded scripts exist; kind runtime evidence remains open. |
| B7 Terraform and GitOps | Partially implemented | Three-state ephemeral EKS Terraform and guarded plan/apply/teardown exist; no cloud run or GitOps reconciler is claimed. |
| B8 Observability | Planned | Prometheus/OpenTelemetry metrics, Grafana dashboards, alerts, and operational objectives. |
| B9 Managed-cloud demonstration | Planned | Short-lived AWS portfolio demonstration with measured evidence and verified teardown. |

## B4 delivered boundary

B4 adds Flink 2.3/PyFlink packaging, schema-versioned enrichment events,
recorded hosted-model artifacts, event-time windows, bounded lateness,
deduplication, stable signal revisions, PostgreSQL projections, health gates,
and a visible legacy fallback. Hosted inference runs outside Flink and replays
reuse immutable artifacts.

B4 is not qualified until real Kafka/Flink delivery, checkpoint recovery,
duplicate and late-event behavior, failure injection, the paced load window,
and the bounded provider canary have recorded passing evidence. Until then,
`ENABLE_FLINK_TRENDING=false` remains the default.

## B5 delivered boundary

B5 makes PostgreSQL authoritative for document eligibility, artifact identity,
revisions, withdrawals, investigation snapshots, and active projection
generation. Independent consumers maintain:

- Elasticsearch lexical and verified literal-phrase retrieval;
- pinned MiniLM semantic projections in pgvector;
- Neo4j typed reference/provenance paths;
- Redis caching of complete validated responses.

Search rehydrates candidates through PostgreSQL before returning them. Graph
context cannot increase claim support. Operators can withdraw/restore records,
check and repair drift, bootstrap history, build isolated generations, activate
at a shared high-water mark, and roll back to retained data.

B5 remains unqualified until the complete disposable-stack scenario matrix,
10,000-document load test, resource evidence, browser flow, recovery drills,
and bounded live canary pass. See [Testing](TESTING.md) and
[Operations](OPERATIONS.md).

## B6 local Kubernetes

The B6 entry condition is accepted B3–B5 behavior under Compose. The current
workstation and sequence are recorded in [Pre-B6](PRE_B6_GUIDE.md).

B6 implementation adds:

- namespaces, Services, ConfigMaps, Secrets, workloads, initialization Jobs,
  persistent volumes, and ingress;
- startup, readiness, and liveness probes that preserve current health meaning;
- resource requests/limits, scheduling, worker shutdown grace, and rollout
  policy;
- immutable application images available to kind nodes or a local registry;
- seeded end-to-end verification of event delivery, persistence, search, graph
  paths, reload, replay, recovery, and degradation.

Kubernetes must not be used to hide an unqualified underlying application
topology. The full B5 load gate requires a higher-memory environment than the
current workstation; reduced local smoke tests do not replace that evidence.
See [B6 Operations](B6_OPERATIONS.md) for the exact blocked-by-default runtime
sequence. B6 remains unqualified until that evidence is retained.

## Later platform work

### B7 — Terraform and GitOps

- define a short-lived, cost-bounded AWS environment;
- add image build/publish and infrastructure validation in CI;
- use versioned deployment configuration and explicit promotion/rollback;
- preserve secret boundaries and record immutable release identities.

### B8 — Observability and operations

- collect request, investigation, event, checkpoint, projection, query,
  dependency, and model-budget metrics;
- add traces without exposing chain-of-thought, credentials, or prohibited
  source content;
- define alerts and service objectives for lag, freshness, latency, failures,
  and publication outcomes;
- test backup, restore, replay, and incident runbooks.

### B9 — Ephemeral AWS demonstration

- deploy only components that have corresponding repository artifacts and
  acceptance evidence;
- record one end-to-end investigation and measured workload;
- export screenshots, logs, dashboards, image/deployment identities, and cost
  evidence;
- destroy the environment and verify that billable resources are gone.

## Engineering backlog

These open findings remain after completed database batching, SSE reader, unused
API-export, and display-adapter work:

1. Select latest signal revisions and apply result limits in SQL; batch related
   signal records and inspect representative query plans.
2. Make the audit console paginate beyond 500 events, deduplicate reconnects,
   and stop SSE/polling after terminal state.
3. Lazy-load route components, graph-heavy views, and demo data; compare the
   initial production bundle after the change.
4. Stop or simplify decorative animation for reduced-motion and hidden-page
   states; measure lower-powered-device cost.
5. Complete real PostgreSQL qualification for the batching and stream-reader
   improvements already covered by SQLite/offline tests.

The standalone [100K stress-test plan](100k%20stress%20test%20plan.md) is a
future capacity experiment, not a B5 completion gate or a production-scale
claim.

## Roadmap rules

- Do not mark a milestone complete from code presence, a smoke test, or stale
  historical counts.
- Preserve normalized evidence, receipts, event compatibility, idempotency, and
  visible degradation through every phase.
- Record exact commit/image/model identities with runtime evidence.
- Keep optional providers and specialized stores behind explicit configuration.
- Prefer measured claims over architecture claims in public and résumé material.
