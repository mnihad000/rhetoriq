# RhetoriQ Deployment

RhetoriQ’s accepted work is Kafka-backed, so a live deployment must provide the
API/frontend, PostgreSQL, Kafka, Apicurio, topic initialization, the outbox
publisher, role-scoped workers, and SearXNG. Deploying only an API, frontend,
and database does not satisfy the current investigation execution contract.
Browser rendering and B5 specialized retrieval remain opt-in.

## Local production rehearsal

Copy `.env.production.example` to an untracked environment file, replace every
placeholder secret, and run:

```powershell
docker compose config --quiet
docker compose up --build -d
docker compose ps
```

Open `http://localhost:4173`, check `http://localhost:8000/health` and
`http://localhost:8000/api/research/health`, then run a live investigation.
PostgreSQL, Kafka, and Flink state use named volumes. Do not run `docker compose
down -v` unless that state is intentionally disposable. Detailed recovery and
B5 overlay commands are in [Operations](OPERATIONS.md).

## Public deployment boundary

The documented public-demo topology uses public frontend and API services,
private execution dependencies, and durable PostgreSQL:

| Component | Exposure | Responsibility |
| --- | --- | --- |
| Frontend | Public | Nginx-served React application using `PUBLIC_API_BASE_URL`. |
| API | Public | FastAPI, migrations, durable reads, accepted writes, health, and SSE. |
| PostgreSQL/pgvector | Private managed service | Canonical application state, checkpoints, corpus, outbox, and projection authority. |
| Kafka and Apicurio | Private | Event delivery and compatible schema registry. |
| Topic initializer | Private one-shot job | Topics, retention, schemas, and compatibility. |
| Outbox and role workers | Private | Publish and consume accepted ingestion/investigation work. |
| SearXNG | Private | Broad web discovery. |
| Browser renderer | Not deployed initially | JavaScript-only pages remain a visible retrieval limitation. |

Railway plus Neon is the currently documented provider combination, but any
host is acceptable if it supplies equivalent private networking, durable
services, health checks, and restart behavior. Do not create a second
PostgreSQL service when Neon is authoritative.

Compose keeps its historical API-owned, advisory-locked forward migration
behavior. Kubernetes instead gives exclusive apply ownership to a migration
Job; API and worker startup is check-only and never changes schema. Preserve
the managed database TLS option, normally `sslmode=require`, and keep the
complete URL in platform secret configuration.

Set public frontend/API origins explicitly:

```text
PUBLIC_API_BASE_URL=https://api.example.invalid
CORS_ALLOW_ORIGINS=https://app.example.invalid
DEPLOYMENT_ENV=production
DEMO_MODE=false
```

`VITE_API_BASE_URL` is for local Vite development. Provider credentials,
database URLs, SASL credentials, and service passwords must be platform secrets,
not copied from a local `.env` file.

## Feature rollout

- Keep `ENABLE_POSTGRES_VECTOR_SEARCH=false` until migrations, resumable corpus
  backfill, index creation, and result comparisons pass.
- Keep `ENABLE_FLINK_TRENDING=false` until checkpoint, replay, recovery, and
  signal gates pass. The legacy snapshot remains the explicit fallback.
- Keep `ENABLE_B5_RETRIEVAL=false` until store initialization, projection
  coverage, failure behavior, rebuild/rollback, and product acceptance pass.
- Keep `BROWSER_RENDERING_ENABLED=false` unless an isolated renderer is
  deliberately deployed and secured.
- Keep automatic investigation off unless its separate policy and operating
  gates are approved.

Enabling a flag is not evidence that the dependency is healthy. Health and
workspace responses must expose unavailable, pending, stale, and fallback
states accurately.

## Launch evidence

A public launch is complete only after one deployment records all fields below.
Do not mix evidence from different commits or environments.
Local candidate evidence and the exact gates remaining before this launch are
tracked in [AWS Release Readiness](RELEASE_READINESS.md).

### Release identity

| Field | Recorded value |
| --- | --- |
| Frontend URL | `<pending>` |
| API URL | `<pending>` |
| PostgreSQL project/branch | `<pending>` |
| Deployed commit and image digests | `<pending>` |
| Verification timestamp (UTC) | `<pending>` |
| Verifier | `<pending>` |

### Topology and health

- [ ] Public ingress exposes only the intended frontend/API endpoints.
- [ ] PostgreSQL is durable, private, TLS-enabled, and used by the migration
  owner, API, workers, and checkpointer.
- [ ] Kafka, registry, SearXNG, outbox publisher, and all required workers are
  private and ready.
- [ ] `GET /health` returns HTTP 200 and reports `demo_mode: false`.
- [ ] `GET /api/research/health` reports numeric lag/DLQ counts and ready
  execution dependencies.
- [ ] Optional browser/B5 dependencies report their intentional disabled or
  degraded state truthfully.

Record only sanitized excerpts—never connection strings, tokens, event bodies,
or source documents.

### Investigation durability

- [ ] Start a short public-source investigation from the deployed frontend.
- [ ] Record the investigation ID, run ID, terminal status, and deployment
  identity.
- [ ] Observe SSE progress to a terminal state without manual database repair.
- [ ] Reload the workspace and verify its report, receipts, event trail, and IDs.
- [ ] Restart or redeploy the API and reload the same workspace.
- [ ] Trigger recorded replay and preserve its comparison or terminal
  limitation.

### Release checks

- [ ] CI is green for the deployed commit.
- [ ] Backend, schema, compile, PostgreSQL migration, frontend test/build, and
  Markdown checks pass with only documented optional skips.
- [ ] Runtime event delivery and recovery evidence is attached.
- [ ] Any enabled Flink or B5 capability has its corresponding acceptance
  evidence from [Testing](TESTING.md).

The local 20-document Kafka/Flink recorded-provider smoke passed 20/20 with
completed checkpoints on 2026-09-26. It is prerequisite evidence only and does
not replace the deployed recovery proof, remaining formal B4 scenarios, B5
qualification, or the public investigation checks above.

## Rollback

Before schema-changing releases, create and record a PostgreSQL backup or Neon
branch. The migration runner has no destructive down-migration path. Application
rollback may reuse a backward-compatible schema; incompatible database changes
require a tested forward fix or an explicit restore decision.

Record the last-known-good commit/images, backup identifier, rollback action,
and post-rollback health plus workspace reload. B5 generation rollback is a
separate atomic manifest operation described in [Operations](OPERATIONS.md); it
does not replace application or database rollback.

## Local Kubernetes (B6)

B6 translates the accepted Compose topology into local Kubernetes workloads,
Services, initialization Jobs, ConfigMaps, Secrets, persistent volumes, probes,
resource limits, shutdown grace, ingress, and rollout policy. It must preserve:

- PostgreSQL data, Kafka logs, Flink checkpoints/savepoints, Elasticsearch and
  Neo4j data, plus certificate material;
- dependency initialization order and service-specific trust;
- explicit liveness versus dependency readiness;
- stable worker identities, shutdown behavior, and replay semantics;
- the same seeded search, path, reload, recovery, and degradation evidence.

Redis remains disposable cache. Public CA material may use a ConfigMap; private
keys require Secrets or equivalent secure provisioning. Build or load immutable
application images into the local cluster rather than relying on a mutable
`local` tag. The detailed workstation status and execution order remain in
[Pre-B6](PRE_B6_GUIDE.md).
The implemented chart, guarded execution sequence, evidence gates, and EKS
teardown are documented in [B6 Operations](B6_OPERATIONS.md).

Implementation and static verification are complete, but no Kubernetes B6
runtime has been accepted. Run the repository gate before any cluster change:

```powershell
./infra/b6/validate.ps1
```

At the last recorded check, the existing kind node was Ready, but
`vm.max_map_count` was `262144` instead of `1048576`, the workspace drive had
about 15.43 GiB free instead of the 20 GiB minimum, and host Helm was absent.
The local full-stack run must stop until those prerequisites are corrected. Its
6.66 GiB Docker-memory allocation makes the smoke an experiment: Pending pods
or OOM kills are evidence to retain, not permission to remove components.

## Ephemeral AWS portfolio demonstration

The repository now implements a short-lived AWS EKS environment for the
production-style architecture. It has not been provisioned. A future execution
is evidence of engineering integration only, not a permanent production
service, formal B3-B5 qualification, B5 10,000-document qualification, or proof
of continuous 100K-document operation.

The demonstration should use seeded or appropriately licensed data and record:

- immutable images, manifests/charts, ConfigMaps, externally supplied Secrets,
  resource settings, probes, and the guarded deployment flow;
- one end-to-end investigation plus Kafka/Flink, persistence, search, graph,
  and observability evidence for the components actually deployed;
- screenshots, deployment logs, measured workload results, and teardown proof.

Before provisioning, review the three isolated Terraform states and saved
plans, verify the intended AWS identity, register the eight-hour Windows
teardown task, confirm the $25 Budget notification, restrict endpoint access to
the supplied CIDRs, and apply the required project/environment/owner/run/
commit/expiry tags. The implemented profile uses one on-demand x86
`m7i.2xlarge`, two public subnets, no NAT Gateway, encrypted gp3 volumes,
immutable ECR repositories, and no managed application databases.

After recording evidence, use the guarded teardown from the same states. It
exports evidence, requests a final Flink savepoint, stops consumers, destroys
platform resources, deletes ECR image manifests, destroys foundation
resources, audits AWS for residual resources, and destroys the bootstrap state
bucket only after that inventory is empty. A Budget alert and `expires-at` tag
are advisory; neither performs cleanup. The affordable public demo remains a
separate deployment.
