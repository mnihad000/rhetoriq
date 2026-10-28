# Deployment Branch Readiness

This file records the transition of the `deployment` branch toward the smaller
Vercel/Railway/managed-PostgreSQL topology. It is not evidence that the target
deployment is complete.

## Current state

- The branch is `deployment`.
- Kubernetes, Helm, EKS Terraform, cluster PKI, and ECR publishing artifacts
  were removed here and remain on `further_dev`.
- The current runtime still requires Kafka, Apicurio, Flink, SearXNG, the
  outbox publisher, and role-scoped workers.
- No Vercel or Railway deployment has been qualified from this branch.

## Historical evidence

Earlier recorded checks belong to the broad architecture and must not be used
as proof of the smaller deployment. They remain useful regression context:

- Backend, frontend, Compose, schema, and offline infrastructure checks were
  recorded in the previous release candidate.
- A recorded-provider Kafka/Flink smoke delivered 20 of 20 documents with
  completed checkpoints.

These facts do not prove a Vercel/Railway deployment, PostgreSQL queue, public
authorization, or the replacement search path.

## Branch verification

The working tree on `deployment` passed the following checks after infrastructure
removal and retirement of the Kubernetes acceptance option:

- Offline backend suite: 370 passed, 10 skipped, and 18 integration tests deselected.
- Focused deployment and B5 acceptance tests: 18 passed.
- Base, B5, and disposable B5 acceptance Compose profiles rendered.
- Event-schema reproduction and CI YAML parsing passed.
- All 39 local Markdown file links resolved.

The offline suite used a fresh isolated temporary directory because the existing
Windows pytest temporary-directory location was inaccessible. No live deployment
or real-service integration qualification was performed for this pass.

## Required gates for this branch

- [ ] PostgreSQL-backed job queue with retry, lease, and stale-worker fencing.
- [ ] Direct worker document processing with receipts and canonical eligibility.
- [ ] PostgreSQL search and bounded relationship traversal.
- [ ] Account ownership, private workspaces, sharing, quotas, and spending caps.
- [ ] Vercel frontend and Railway API/worker configuration.
- [ ] Scheduled radar refresh with last-valid-snapshot behavior.
- [ ] Backup, restore, migration, and rollback evidence.
- [ ] Restart recovery, SSE reconnect, reload, and authorization evidence.
- [ ] A real deployed investigation with recorded commit and runtime metrics.

Until these gates pass, the branch is an implementation transition, not a
permanent production release.
