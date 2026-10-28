# RhetoriQ: Production Deployment Simplification

Status: Agreed architectural direction; detailed implementation planning pending.
Target branch: `deployment`.
Deployment target: Vercel frontend, Railway API and worker, managed PostgreSQL.

## Goal

Create an affordable, maintainable permanent deployment for an open public
product with user-started investigations and a periodically refreshed narrative
radar.

Preserve the evidence-first research experience while reducing infrastructure,
operating cost, and recovery complexity.

Preserve the existing broad architecture in its current branch or a reference
tag before making changes. The user will create the `deployment` branch.

## Target Architecture

- Vercel: existing React/Vite frontend.
- Railway API service: FastAPI, admission, workspace reads, and SSE.
- Railway worker service: durable investigation execution and document processing.
- Managed PostgreSQL: canonical evidence, investigations, jobs, checkpoints,
  progress events, search, relationships, and radar snapshots.
- Scheduled jobs: bounded narrative discovery and radar refresh.
- Search acquisition: retain SearXNG or choose an approved managed provider.

LangGraph remains the research orchestrator.

## Add or Strengthen

### Public access and cost control

- Accounts and investigation ownership.
- Private investigations by default, with explicit sharing.
- Authorization checks on workspaces, evidence, research trails, and replay.
- Per-user quotas, global spending ceilings, and concurrency limits.
- Bounded research time, requests, retries, tokens, and model spending.

### Durable execution

- PostgreSQL-backed job acceptance and dispatch.
- Atomic persistence of accepted work.
- Worker leases, heartbeats, bounded retries, and visible terminal failures.
- Generation fencing to prevent stale workers from writing or publishing.
- Checkpoint recovery and idempotent action handling.
- Graceful shutdown and restart recovery.

### Processing and retrieval

- Replace the investigation's Kafka/Flink document round trip with processing
  inside the worker.
- Preserve normalization, deduplication, enrichment, receipts, and canonical
  eligibility checks.
- Use PostgreSQL full-text and phrase search.
- Store relationships for bounded application-level graph traversal.
- Refresh the radar through scheduled batches and persist its latest snapshot.

### Deployment and operations

- Vercel SPA routing and frontend API configuration.
- Railway API, worker, scheduled-job, and secret configuration.
- Explicit migration ownership and startup readiness checks.
- Structured logs, queue age, worker health, failures, and spending visibility.
- Database backups and a tested restore procedure.
- CI and documentation matching the smaller architecture.

## Remove from the Production Branch

| Remove | Replacement |
| --- | --- |
| Kafka and Apicurio | PostgreSQL job queue |
| Outbox publisher and Kafka consumer services | Research worker |
| Flink JobManager, TaskManager, checkpoints, and savepoints | Worker processing and scheduled batches |
| Separate streaming enrichment service | Enrichment within bounded jobs |
| Elasticsearch | PostgreSQL full-text and phrase search |
| Neo4j | Stored relationships and bounded application traversal |
| Redis service | Database reads and persisted radar snapshots initially |
| Specialized projection workers | Direct canonical updates |
| Projection generation, repair, rebuild, and cutover machinery | Database-backed retrieval |
| Kubernetes, Helm, EKS, Terraform, and cluster PKI | Platform deployment configuration |
| ECR publishing and cluster-specific CI | Vercel/Railway release workflows |

Remove obsolete dependencies, configuration, tests, and documentation after
their retained responsibilities have replacements.

Preserve canonical revisions, withdrawals, and evidence eligibility when
removing projection-specific machinery.

## Preserve

- LangGraph planning and bounded research.
- Approved source connectors and canonical-page retrieval.
- Acquisition receipts and visible retrieval failures.
- Evidence checks and deterministic publication gates.
- Document revisions, withdrawals, and citation eligibility.
- Durable checkpoints, progress events, and recorded replay.
- Reports, timelines, counterpoints, evidence inspection, and graph visualization.
- Explicit uncertainty and insufficient-evidence outcomes.

## Deferred Features and Open Decisions

Resolve before implementing the affected subsystem:

- Self-hosted SearXNG versus an approved managed search provider.
- Local versus hosted embeddings and NLI.
- Whether semantic search ships initially.
- Database provider and connection configuration.
- Authentication provider and sharing behavior.
- Initial quotas, concurrency, retention, and operating budget.

Defer browser rendering, continuous monitoring, elaborate caching, and
additional model services until demonstrated requirements justify them.

Model placement decisions must preserve evidence requirements.

## Acceptance Criteria

- A user can start an investigation and receive an accepted durable job.
- A worker restart preserves progress and produces one durable logical outcome.
- A stale worker cannot overwrite progress or publish a result.
- Evidence retains receipts, canonical identity, revisions, and eligibility.
- Reports pass publication checks or expose insufficient evidence.
- SSE reconnect and browser reload recover persisted progress and results.
- Users cannot access another user's private investigation.
- Quotas and global spending limits are enforced.
- Scheduled radar refresh preserves the last valid snapshot on failure.
- Search and graph views retain inspectable evidence relationships.
- The deployed workspace survives API redeployment.
- Backup restoration and release rollback are exercised.
- Tests and documentation describe the architecture actually deployed.

## Product Validation

Recruit a small initial user group and observe real investigations.

Record completion, evidence inspection, usefulness, repeat usage, processing
time, failures, and cost. Use these observations to guide further architecture
and feature decisions.

Keep implemented, tested, deployed, and measured claims distinct.

## Working Sequence

1. User creates the `deployment` branch.
2. Resolve the open decisions and prepare the implementation plan.
3. Replace execution and document processing dependencies.
4. Consolidate retrieval and remove obsolete infrastructure.
5. Add public access controls and operating safeguards.
6. Deploy and record acceptance evidence.
7. Gather user feedback and iterate.
