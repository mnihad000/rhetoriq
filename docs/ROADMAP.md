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
| B3 Kafka contracts | Implemented, partial runtime smoke | Contracts, registry, outbox, consumers, DLQs, replay, and health exist. Local evidence covers topic/schema initialization, PostgreSQL outbox-to-Kafka delivery, document consumption, duplicate guarding, and worker-restart catch-up; DLQ, controlled replay, broker/registry interruption, and remaining consumer-role recovery evidence remain. |
| B4 Flink signals | Implemented, partial runtime smoke | The recorded-provider path passed 20/20 real Kafka/Flink deliveries with completed checkpoints; failure recovery, duplicate/late/out-of-order behavior, paced load, retry/budget evidence, and the optional live canary remain. |
| B5 Recoverable projections | Implemented | Immutable authority, ES/Neo4j/MiniLM projections, cache, product UI, drift/repair/rebuild/cutover tooling exist; actual-stack acceptance remains. |
| B6 Local Kubernetes | Implemented, unqualified | Full-topology Helm profiles, deployment contracts, PKI, policies, storage, smoke/evidence Jobs, and guarded scripts exist; kind runtime evidence remains open. |
| B7 Ephemeral EKS Terraform | Implemented, unqualified | Three-state Terraform, immutable ECR flow, IAM/network/storage controls, guarded plan/apply, deadline fallback, and verified teardown logic exist; no AWS apply or destroy evidence exists. GitOps is outside the portfolio-demo scope. |
| B8 Observability | Planned | Prometheus/OpenTelemetry metrics, Grafana dashboards, alerts, and operational objectives. |
| B9 Managed-cloud demonstration | Implemented, not executed | The complete short-lived EKS workflow and evidence tooling exist; provisioning, recording, evidence export, and zero-residual teardown remain open. |

## B4 delivered boundary

B4 adds Flink 2.3/PyFlink packaging, schema-versioned enrichment events,
recorded hosted-model artifacts, event-time windows, bounded lateness,
deduplication, stable signal revisions, PostgreSQL projections, health gates,
and a visible legacy fallback. Hosted inference runs outside Flink and replays
reuse immutable artifacts.

Basic real Kafka/Flink delivery is now recorded: the disposable
recorded-provider smoke published 20 documents and received 20 unique processed
documents with zero failures in 135.31 seconds, while checkpoints completed.
B4 is not qualified until checkpoint recovery under failure, duplicate and
late/out-of-order behavior, failure injection, the paced load window,
retry/budget behavior, and the bounded provider canary have recorded passing
evidence. Until then, `ENABLE_FLINK_TRENDING=false` remains the default.

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

The preferred B6 entry condition is accepted B3–B5 behavior under Compose.
This workstation cannot satisfy the full B5 memory budget, so the approved
alternative is a trustworthy local regression and provisional immutable-image
baseline followed by private EKS qualification. The current workstation and
sequence are recorded in [Pre-B6](PRE_B6_GUIDE.md).

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
Formal B3–B5 scenarios may run on the private EKS environment when they follow
the same acceptance contracts and retain the required evidence; an ordinary
EKS smoke alone does not close those gates. See
[B6 Operations](B6_OPERATIONS.md) for the exact blocked-by-default runtime
sequence. B6 remains unqualified until that evidence is retained.

### B6 implementation and verification snapshot

The repository now contains:

- one reusable chart at `deploy/helm/rhetoriq`, with shared defaults and
  `kind` and `eks-demo` profiles;
- the full one-replica topology: frontend, API, PostgreSQL/pgvector, Kafka
  KRaft, Apicurio, Flink, SearXNG, Elasticsearch, Neo4j, Redis, outbox,
  document/signal/investigation/projection/enrichment workers, three B5
  workers, initialization Jobs, smoke/evidence Jobs, and durable claims;
- cert-manager/trust-manager PKI preserving service-specific PostgreSQL,
  Elasticsearch, Neo4j, and Redis TLS contracts;
- check-only Kubernetes application startup with an advisory-locked migration
  Job as the exclusive schema writer;
- process liveness, operational readiness, worker heartbeat probes, bounded
  dependency waits, and graceful consumer shutdown;
- digest-only application images, pinned platform images, CPU-only MiniLM,
  restricted security contexts, explicit resources, and `Recreate` rollouts;
- default-deny NetworkPolicies plus functional allow/deny test tooling;
- complete kind preflight, platform, image, Secret, deployment, smoke,
  recovery, browser-access, and evidence scripts;
- three isolated EKS Terraform states (`bootstrap`, `foundation`, `platform`),
  ECR/IAM/EKS/network/storage/optional HTTPS resources, an eight-hour teardown
  deadline, explicit ECR cleanup, and residual-resource auditing;
- exact Flink 2.3.0 Kafka connector source pinning to commit
  `9e4bd3c04f57e5c2189b5d5ddae521518c52cffa`, with archive checksum
  verification and the compatibility patch recorded in the runtime lock.

Recorded release-candidate regression against source `91a95e6`, with its
evidence and the Terraform formatting correction committed as `4efcab2`:

- with disposable PostgreSQL configured, 386 backend tests passed, 12 optional
  environment-dependent tests skipped, and zero tests failed;
- all 16 disposable PostgreSQL integration tests passed;
- Python compilation and event-schema reproduction passed without a generated
  diff;
- 27 frontend tests and the production frontend build passed;
- the high-severity frontend dependency audit reported zero vulnerabilities;
- all three Compose profiles rendered from the example environment;
- both Helm profiles linted and rendered: 61 kind resources and 58 EKS
  resources;
- Kubernetes schema validation reported 52 valid/0 invalid/9 unavailable kind
  resources and 50 valid/0 invalid/8 unavailable EKS resources, while both
  render-contract checks passed;
- all 19 B6 PowerShell scripts parsed, and all three Terraform configurations
  passed formatting and validation without AWS credentials or a remote backend;
- the final Flink image built from checksum-verified connector source and
  passed non-root UID, runtime-import, required-JAR, and no-compiler checks.

Runtime evidence recorded separately: the B4 recorded-provider smoke passed
20/20 unique Kafka/Flink deliveries with zero failures and completed
checkpoints. See [AWS Release Readiness](RELEASE_READINESS.md) for the current
candidate identity and remaining gates.

Remote delivery preparation is also complete through the non-AWS boundary.
Commit `823e305` is on `further_dev` and the historical `main` with the
manually approved ECR workflow, foundation OIDC publisher role,
explicit-source reachability guard, and artifact verifier. `further_dev` is the
GitHub default branch, the only permitted ECR release branch, and the
source-ancestry boundary; the workflow guard, ancestry check, and publisher-role
`ref` trust now target it. The protected `ecr-release` environment is
configured but still permits only `main` until switched to `further_dev`.
Workflow lint, Terraform validation, verifier tests, and `git diff --check`
passed. Foundation has not been applied, the environment role ARN is unset, and
no ECR image or digest exists.

These checks establish implementation quality only. They are not kind, EKS,
B3-B5, 10,000-document, or capacity acceptance evidence.

### B6 remaining gates

The last recorded workstation snapshot was:

- historical context `kind-rhetoriq-b6`, last observed with a Ready Kubernetes
  v1.37.0 node, but currently unreachable because Docker is stopped;
- historical Docker memory of about 6.66 GiB and 6,987,968 KiB allocatable node
  memory;
- `vm.max_map_count=262144`, below the required `1048576`;
- about 2.3 GiB free on the workspace drive, which is a hard boundary for local
  image builds and kind loading;
- Helm 3.19.0, Terraform 1.13.3, kubectl 1.36.1, kind 0.33.0, and AWS CLI
  v2.37.4 are installed. No standard local AWS profile or environment
  credentials were found, so AWS identity remains unverified.

The local kind/image path is deferred rather than forcing cleanup or weakening
the topology. If a suitable workstation is provided later, its gates are:

1. install platform prerequisites and prove NetworkPolicy enforcement;
2. build/load all four images and record exact containerd digests;
3. create the ignored Kubernetes Secret without printing values;
4. deploy every component without silently disabling any service;
5. pass the 20-document flow through Kafka, Apicurio, Flink, PostgreSQL,
   Elasticsearch, Neo4j, MiniLM/pgvector, Redis, API, and frontend;
6. pass worker, Flink, and stateful restart/recovery checks without duplicate
   canonical records or lost durable data;
7. retain at least 15 minutes of working-set, restart, OOM, lag, DLQ,
   checkpoint, persistence, count, digest, and model-revision evidence.

The EKS gates are entirely unexecuted: obtain a short-lived AWS session and
Budget notification email; provide the required operator/network/expiry/owner/
run inputs and optional DNS inputs; review each saved plan; explicitly approve
AWS changes, the guarded GitHub image push, and Secret upload; restrict the
protected environment to `further_dev`; apply foundation,
set its publisher-role ARN in the protected environment, publish and verify all
four ECR digests, deploy privately, and execute the remaining formal B3–B5
scenarios. Then iterate with new commit/image identities for every fix, freeze
and revalidate the final release, enable restricted public HTTPS for final
proof, export evidence, and destroy in `platform -> foundation -> bootstrap`
order with an empty residual inventory. No AWS provisioning or billable action
has occurred.

## Later platform work

### B7 — Terraform and deployment automation

- execute and retain plan/apply/destroy evidence for the implemented
  short-lived, cost-bounded AWS environment;
- observe the added image, Helm, Kubernetes-schema, Terraform format/validate,
  and immutable-image identity gates passing in remote CI for the frozen final
  commit; this observation is deferred until after the initial private
  deployment but remains required before public exposure or release sign-off;
- retain explicit plan review, secret boundaries, and immutable release
  identities;
- evaluate GitOps only as a later production-platform decision; it is not a
  B6 demo requirement and is not currently implemented.

### B8 — Observability and operations

- collect request, investigation, event, checkpoint, projection, query,
  dependency, and model-budget metrics;
- add traces without exposing chain-of-thought, credentials, or prohibited
  source content;
- define alerts and service objectives for lag, freshness, latency, failures,
  and publication outcomes;
- test backup, restore, replay, and incident runbooks.

### B9 — Ephemeral AWS demonstration

- execute the implemented one-node EKS demo only after reviewing all plans and
  registering the teardown deadline;
- record one end-to-end investigation and measured workload;
- export screenshots, sanitized logs, resource measurements,
  image/deployment identities, and cost evidence;
- destroy the environment and retain zero-residual-resource evidence.

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
