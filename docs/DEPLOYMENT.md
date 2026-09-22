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

## Ephemeral AWS portfolio demonstration

After B6 and the Terraform/GitOps roadmap work, a short-lived AWS EKS
environment may demonstrate the production-style architecture. It is evidence
of engineering capability, not a permanent production service or proof of
continuous 100K-document operation.

The demonstration should use seeded or appropriately licensed data and record:

- immutable images, manifests/charts, ConfigMaps, Secrets, resource settings,
  probes, autoscaling, and deployment flow;
- one end-to-end investigation plus Kafka/Flink, persistence, search, graph,
  and observability evidence for the components actually deployed;
- screenshots, deployment logs, measured workload results, and teardown proof.

Before provisioning, use a dedicated account or isolated Terraform workspace,
set a low AWS Budget alert, restrict regions and instance sizes, tag resources
with `project=rhetoriq`, `environment=portfolio-demo`, and an expiry timestamp,
and avoid NAT gateways or managed services unless the demonstration requires
them.

After recording evidence, run `terraform destroy` from the same workspace and
confirm removal of the EKS cluster, node groups, load balancers, volumes, public
IPs, NAT gateways, and managed databases. Review Cost Explorer and active
resources. The affordable public demo remains a separate deployment.
