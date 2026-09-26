# AWS Release Readiness

Last updated: 2026-09-26

This is the canonical handoff record between local release preparation and the
guarded AWS/EKS procedure in [B6 Operations](B6_OPERATIONS.md). It records only
evidence actually produced; an implemented component is not treated as runtime
qualified or deployed.

## Candidate identity

| Field | Recorded value |
| --- | --- |
| Application code baseline | `269440d8d35e6b9ecac7b2ed337c1bac941fd38b` |
| Release-hardening commit | `6e8b99e91d7dd112fe00e990a040320633249a77` |
| Final deployment commit | Pending final regression, fixes, and digest rebuild |
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

## Before initial private EKS provisioning

1. Rerun the complete backend/PostgreSQL/schema/frontend/Compose/Helm/
   Kubernetes-schema/Terraform regression suite at the candidate commit.
2. Build the backend, B5, Flink, and frontend Linux/AMD64 images from that
   commit and record provisional SHA-256 identities suitable for the first EKS
   deployment. They are not final release digests if later fixes change code.
3. Observe equivalent CI gates passing for that initial deployment commit.
4. Install and verify AWS CLI v2, then supply the approved AWS identity, region,
   CIDRs, owner/run/deadline tags, budget notification email, and optional DNS
   inputs without committing credentials.
5. Complete the practical local B3/B4 checks. Record the workstation's memory,
   disk, and kernel boundary rather than weakening the topology or forcing the
   B5 qualification onto this host.

## Private EKS qualification and iteration

1. Provision the guarded EKS environment with public application ingress
   disabled, register the eight-hour teardown, push the provisional images,
   upload the Secret, and deploy the complete topology.
2. Run the remaining formal B3 DLQ/replay/interruption/recovery scenarios and
   B4 duplicate/late/out-of-order, failure-injection, checkpoint-recovery,
   paced-load, and retry/budget scenarios on EKS.
3. Run the B5 specialized-store, degradation, repair, rebuild, rollback,
   browser/product, and separate 10,000-document qualification on EKS. A basic
   EKS smoke does not substitute for these named formal scenarios.
4. For each defect, fix and test locally, create a new commit, build and push
   new immutable digests, update the reviewed Helm/Terraform application plan,
   redeploy, and rerun the affected gates. Never reuse evidence from an older
   digest as evidence for the replacement.
5. Once stable, rerun the complete regression suite and CI, rebuild the four
   definitive images, and ensure the final commit, digests, chart revision,
   migrations, and evidence all identify the same release.
6. Only then enable restricted public HTTPS, perform the public investigation,
   SSE, reload/redeploy, replay, health, persistence, and rollback proof, and
   export sanitized evidence.

## AWS execution and closure

Follow [B6 Operations](B6_OPERATIONS.md) rather than duplicating commands here
for every plan/apply, image push, Secret upload, redeployment, smoke/evidence
run, and recovery drill. Complete the final public proof in
[Deployment](DEPLOYMENT.md), then destroy `platform -> foundation -> bootstrap`.
Slice closure requires an empty residual AWS inventory.
