# Deployment Branch Readiness

This file records the transition of the `deployment` branch toward the smaller
Vercel/Railway/managed-PostgreSQL topology. It is not evidence that the target
deployment is complete.

## Current state

- The branch is `deployment`.
- Kubernetes, Helm, EKS Terraform, cluster PKI, and ECR publishing artifacts
  were removed here and remain on `further_dev`.
- The current code path uses a PostgreSQL-backed job queue, direct worker
  processing, and SearXNG for local search. Optional B5 stores remain an
  unqualified overlay.
- No Vercel or Railway deployment has been qualified from this branch.

## Historical evidence

Earlier recorded checks belong to the broad architecture and must not be used
as proof of the smaller deployment. They remain useful regression context:

- Backend, frontend, Compose, schema, and offline infrastructure checks were
  recorded in the previous release candidate.
- Earlier broker/stream smoke results are historical and are not evidence for
  the smaller deployment.

These facts do not prove a Vercel/Railway deployment, PostgreSQL queue, public
authorization, or the replacement search path.

## Branch verification

Do not copy historical test counts into a new release record. The next release
record must name the exact commit, commands, environment, and results after the
database-job migration settles. No live deployment or real-service integration
qualification has been performed for this transition.

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
