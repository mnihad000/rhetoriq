# RhetoriQ Roadmap

The `deployment` branch reduces the permanent deployment surface. The older
broker/stream implementation remains available on `further_dev` and in Git
history for comparison. Status labels distinguish code, tests, deployment, and
measured runtime evidence.

## Current direction

The intended permanent topology is a Vercel frontend, Railway API, Railway
worker and scheduled job, and managed PostgreSQL. The current application path
already uses the PostgreSQL queue and direct worker processing in code, with
SearXNG retained as the current local search provider. It has not yet been
live-qualified on the target platforms.

## Milestones

| Area | Status | Notes |
| --- | --- | --- |
| Evidence-first investigation product | Implemented | Reports, receipts, timelines, graph UI, limitations, and publication checks remain in scope. |
| Bounded research runtime | Implemented, current path | LangGraph, leases, checkpoints, replay, and budgets remain; stale-worker fencing is still required. |
| Broker/stream/B5 pipeline | Historical reference | Removed from the deployment Compose path; the reference implementation remains on `further_dev`. Optional B5 stores remain an unqualified overlay. |
| Deployment simplification | Implemented in code, not qualified | Kubernetes/AWS and broker/stream runtime artifacts removed from this branch; queue leasing, direct processing, and fencing still need live acceptance. |
| Public access controls | Not started | Ownership, sharing, quotas, concurrency, and spending limits are required before open public use. |
| Vercel/Railway release | Not started | No deployment or runtime qualification has been recorded from this branch. |

## Next implementation sequence

1. Finish live qualification of job acceptance, claiming, retries, leases, and
   generation fencing.
2. Add PostgreSQL search and bounded relationship storage/traversal; decide
   whether Redis or other B5 projections are justified.
3. Add accounts, ownership, sharing, quotas, and spending controls.
4. Add Vercel/Railway configuration, scheduled radar refresh, backups, and
   restore checks.
5. Run deployment, restart, SSE, reload, authorization, and cost acceptance
   tests.

Do not describe the target as deployed or production-qualified until those
checks have recorded a specific commit and environment.
