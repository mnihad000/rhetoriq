# AWS Release Readiness

Last updated: 2026-09-26

This is the canonical handoff record between local release preparation and the
guarded AWS/EKS procedure in [B6 Operations](B6_OPERATIONS.md). It records only
evidence actually produced; an implemented component is not treated as runtime
qualified or deployed.

## Candidate identity

| Field | Recorded value |
| --- | --- |
| Current candidate commit | `269440d8d35e6b9ecac7b2ed337c1bac941fd38b` |
| Release-hardening commit | `6e8b99e91d7dd112fe00e990a040320633249a77` |
| Helm chart | `rhetoriq` `0.1.0`, app version `b6` |
| MiniLM model | `sentence-transformers/all-MiniLM-L6-v2` |
| MiniLM revision | `c9745ed1d9f207416be6d2e6f8de32d1f16199bf` |
| Final application-image digests | Pending rebuild from the candidate commit |

The developer's existing edits to `.codex/config.toml`, `docs/ROADMAP.md`, and
`docs/PRE_B6_GUIDE.md` were preserved during release hardening. Secrets and the
ignored root `.env` were not printed or committed.

## Evidence completed

- Installed and verified Docker Server 29.6.2, Helm 3.19.0, Terraform 1.13.3,
  kubectl 1.36.1, kind 0.33.0, Node 22.23.2, npm 11.12.1, and Python 3.13.3.
  AWS CLI is still absent.
- The last complete backend baseline produced 368 passes and 27 documented
  environment-dependent skips. A disposable PostgreSQL run subsequently
  passed all 16 PostgreSQL integration tests. Post-fix focused suites passed
  18 tests, and the final Flink deployment-contract suite passed 9 tests.
- The frontend produced 27 passing tests and a production build. Dependency
  remediation upgraded React Router and transitive PostCSS/nanoid packages;
  `npm audit --audit-level=high` reported zero vulnerabilities.
- Event schemas reproduced without diffs. Compose rendered from the example
  environment. Both Helm profiles linted/rendered (61 kind resources and 58
  EKS resources), built-in Kubernetes objects passed schema validation, all B6
  PowerShell scripts parsed, and all three Terraform states validated without
  AWS credentials or a remote backend.
- CI now contains infrastructure-static validation, Terraform formatting and
  validation, Linux/AMD64 production-image builds with immutable SHA-256
  identity checks, and the frontend high-severity dependency audit. The remote
  CI workflow has not yet been observed green for the candidate commit.
- Flink 2.3 runtime incompatibilities were corrected for checkpoint storage,
  externalized checkpoint retention, and state TTL construction. The recorded
  disposable Kafka/Flink smoke passed with 20 published, 20 uniquely processed,
  zero failures, and completed checkpoints in 135.31 seconds.
- Four production images have built locally during preparation, but source
  changed afterward. Those earlier identities are obsolete and are not release
  digests.

## Remaining before AWS provisioning

1. Rerun the complete backend/PostgreSQL/schema/frontend/Compose/Helm/
   Kubernetes-schema/Terraform regression suite at the candidate commit.
2. Build the backend, B5, Flink, and frontend Linux/AMD64 images from that
   commit and record their definitive SHA-256 identities.
3. Observe equivalent CI gates passing for the commit intended for deployment.
4. Finish the formal B3 gates for DLQs, controlled replay, broker/registry
   interruption, and recovery of every consumer role.
5. Finish the B4 duplicate/late/out-of-order, failure-injection, checkpoint
   recovery, paced-load, retry/budget, and optional bounded-provider gates.
6. Run the B5 specialized-store, degradation, repair, rebuild, rollback,
   browser/product, and separate 10,000-document qualification in a
   resource-feasible environment.
7. Either complete the full kind smoke with the required kernel, disk, and
   memory prerequisites or retain the local resource boundary and run the
   complete topology smoke on EKS. A reduced topology does not count.
8. Install and verify AWS CLI v2, then supply the approved AWS identity, region,
   CIDRs, owner/run/deadline tags, budget notification email, and optional DNS
   inputs without committing credentials.

## AWS execution and closure

After the prerequisites above, follow [B6 Operations](B6_OPERATIONS.md) rather
than duplicating commands here: plan/review/apply the three Terraform states,
register the deadline teardown, push digest-addressed images, upload the
Kubernetes Secret, deploy the Helm release, and run smoke/evidence and recovery
jobs. Complete the public investigation, SSE, reload/redeploy, replay, health,
and persistence checks in [Deployment](DEPLOYMENT.md), export sanitized
evidence, then destroy `platform -> foundation -> bootstrap`. Slice closure
requires an empty residual AWS inventory.
