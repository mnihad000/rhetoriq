# RhetoriQ Roadmap

The `deployment` branch reduces the permanent deployment surface while keeping
the broad Kafka/Flink/B5 implementation available on `further_dev` as a
reference. Status labels distinguish code, tests, deployment, and measured
runtime evidence.

## Current direction

The intended permanent topology is a Vercel frontend, Railway API, Railway
worker and scheduled job, and managed PostgreSQL. The current application path
has not yet been migrated to that topology; it still depends on Kafka,
Apicurio, Flink, SearXNG, and role-scoped workers.

## Milestones

| Area | Status | Notes |
| --- | --- | --- |
| Evidence-first investigation product | Implemented | Reports, receipts, timelines, graph UI, limitations, and publication checks remain in scope. |
| Bounded research runtime | Implemented, current path | LangGraph, leases, checkpoints, replay, and budgets remain; stale-worker fencing is still required. |
| Kafka/Flink/B5 pipeline | Reference architecture | Retained for current runtime and available on `further_dev`; not yet removed from this branch's application code. |
| Deployment simplification | In progress | Kubernetes/AWS artifacts removed; PostgreSQL queue and direct processing remain. |
| Public access controls | Not started | Ownership, sharing, quotas, concurrency, and spending limits are required before open public use. |
| Vercel/Railway release | Not started | No deployment or runtime qualification has been recorded from this branch. |

## Next implementation sequence

1. Add PostgreSQL job acceptance, claiming, retries, leases, and generation fencing.
2. Move investigation document processing into the worker and preserve receipts.
3. Add PostgreSQL search and bounded relationship storage/traversal.
4. Add accounts, ownership, sharing, quotas, and spending controls.
5. Add Vercel/Railway configuration, scheduled radar refresh, backups, and restore checks.
6. Run deployment, restart, SSE, reload, authorization, and cost acceptance tests.

Do not describe the target as deployed or production-qualified until those
checks have recorded a specific commit and environment.
