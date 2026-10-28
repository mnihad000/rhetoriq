# RhetoriQ Deployment

The `deployment` branch is the smaller permanent-deployment path. Kubernetes,
Helm, EKS, Terraform, cluster PKI, ECR publishing, Kafka, Apicurio, and Flink
are not required by this branch. Their historical implementations remain on
`further_dev`.

## Intended topology

| Component | Responsibility |
| --- | --- |
| Vercel | Static React/Vite frontend and SPA routing |
| Railway API | FastAPI admission, reads, and SSE |
| Railway worker | Durable job execution and direct document processing |
| Managed PostgreSQL | Canonical evidence, investigations, jobs, checkpoints, progress, search, relationships, and radar snapshots |
| Scheduled Railway job | Bounded periodic radar refresh |

The search provider remains open. SearXNG is the current local discovery
provider; a managed provider may replace it after source-policy, cost, and
retention review.

## Current local rehearsal

Create an ignored `.env.local` from the example, replace placeholders with
fresh local-only values, and run from the repository root:

```powershell
docker compose --env-file .env.production.example config --quiet
Copy-Item .env.production.example .env.local
# Replace placeholder secrets in .env.local before starting services.
docker compose --env-file .env.local up --build -d
docker compose --env-file .env.local ps
```

The base stack contains PostgreSQL/pgvector, SearXNG, API, one `worker`, and
the frontend. The worker uses the PostgreSQL event journal as a durable queue,
claims leases, renews ownership, processes fetched documents directly, and
retries bounded failures. No Kafka broker, schema registry, Flink cluster,
publisher, topic initializer, or role-scoped worker is needed.

The optional B5 overlay adds encrypted Elasticsearch, Neo4j, Redis, TLS
bootstrap, and the pinned MiniLM image. It is not required for the base
deployment and must be qualified separately before enabling retrieval.

Check `GET /health`, `GET /api/research/health`, and a disposable investigation
flow. Compose rendering is configuration evidence only; it does not prove
that a permanent deployment is ready.

## Environment and migration ownership

For local Vite development, set `VITE_API_BASE_URL`. The Nginx image uses
`PUBLIC_API_BASE_URL` at container startup; Vercel uses build-time frontend
configuration.

Production should set `DEPLOYMENT_ENV=production`, `DEMO_MODE=false`, and an
explicit `CORS_ALLOW_ORIGINS`. Keep database URLs, provider credentials, and
service secrets in Railway or the managed database provider. Do not commit
them. `JOB_*` settings control polling, lease duration, renewal, retry count,
and retry backoff.

The API image applies committed migrations before startup. Give migration
application one explicit owner and make API and worker startup verify-only in
the platform release configuration. The migration runner has no destructive
down-migration path.

## Completed on this branch

- Removed Kubernetes, Helm, EKS Terraform, cluster PKI, ECR publishing, and
  infrastructure-static CI artifacts.
- Replaced the local multi-service Kafka/Flink topology with PostgreSQL,
  SearXNG, API, one durable worker, and frontend.
- Retained domain event envelopes and the transactional journal as durable
  database records rather than network transport.
- Kept the optional B5 stores isolated behind a separate Compose overlay.

## Remaining release work

- Finish and verify job leasing, retry, and stale-worker fencing across API and
  worker restarts.
- Complete public accounts, ownership, sharing, quotas, concurrency, and
  spending controls before open public use.
- Decide the initial search and model providers and measure their cost/latency.
- Add Vercel/Railway configuration, scheduled radar execution, structured logs,
  backups, restore testing, and queue/worker health dashboards.
- Requalify completion, restart recovery, SSE reconnect, reload, authorization,
  and cost limits on the smaller topology.

## Release checklist

- [ ] A durable job is accepted without an HTTP request running research.
- [ ] Worker restart and stale-worker fencing preserve one logical outcome.
- [ ] Receipts, canonical identity, revisions, withdrawals, and eligibility survive.
- [ ] Reports publish only after evidence checks or return `insufficient_evidence`.
- [ ] SSE reconnect and browser reload recover persisted state.
- [ ] Private workspaces cannot be read by another user.
- [ ] Quotas and model spending limits are enforced.
- [ ] Radar refresh preserves the last valid snapshot after failure.
- [ ] Database backup, restore, migration, and rollback are exercised.
- [ ] The deployed commit and measured runtime evidence are recorded.

## Rollback

Before schema changes, create a managed PostgreSQL backup or branch. Roll back
application code only against a backward-compatible schema; use a tested
forward fix or database restore for incompatible changes. Record the release
identity, backup identifier, rollback action, and post-rollback workspace
reload.
