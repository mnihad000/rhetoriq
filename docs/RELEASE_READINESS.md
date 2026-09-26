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
| Local regression tested source | `91a95e60a511c8e740f0d0436039ff39830076e7` |
| Regression evidence and Terraform formatting commit | `4efcab20de05fb62d6ce83c7b3e2816afa2eb1e0` |
| Initial remote image source | `7d1c4f2ab91bb5a557050b9c591ea621fd6c1652` |
| Final deployment commit | Pending EKS qualification, post-deployment remote CI, and definitive digest rebuild |
| Helm chart | `rhetoriq` `0.1.0`, app version `b6` |
| MiniLM model | `sentence-transformers/all-MiniLM-L6-v2` |
| MiniLM revision | `c9745ed1d9f207416be6d2e6f8de32d1f16199bf` |
| Initial ECR manifest digests | Pending foundation, approved GitHub Actions run, and registry verification |
| Final application-image digests | Pending definitive rebuild after EKS qualification |

The local regression began with only the developer's `.codex/config.toml` edit
visible as a tracked change. That edit was preserved and excluded from this
release-candidate commit. Secrets and developer `.env` files were not read or
committed.

## Evidence completed

- The release-candidate regression exercised source `91a95e6`; its evidence
  and the formatting-only Terraform correction were committed as `4efcab2`.
  Docker Server 29.6.2, Helm 3.19.0, Terraform 1.13.3,
  kubectl 1.36.1, kind 0.33.0, Node 22.23.2, npm 11.12.1, and Python 3.13.3
  were verified locally. Docker's existing `rhetoriq` services were left running.
- Python compilation and `events.export_schemas --check` passed with no
  generated schema diff. The complete backend suite passed with 371 passes and
  27 skips without PostgreSQL configured. A fresh, disposable pgvector/PostgreSQL
  container passed all 16 required integration tests. With that container
  configured, the complete backend suite passed with **386 passes, 12 skips,
  and zero failures**. The remaining optional skips are one disposable browser
  gate, one full B5 runtime gate, one real MiniLM test, and nine Redis tests;
  none were changed or weakened.
- `npm ci`, all 27 frontend tests across nine files, the production build, and
  `npm audit --audit-level=high` passed; the audit found zero vulnerabilities.
- Base Compose, B5, and B5 acceptance profiles rendered from
  `.env.production.example`. Both Helm profiles linted and rendered: 61 kind
  resources and 58 EKS resources. Kubeconform reported 52 valid/0 invalid/9
  without schemas for kind, and 50 valid/0 invalid/8 without schemas for EKS;
  the repository render-contract validator passed both profiles. All 19 B6
  PowerShell scripts parsed. Terraform formatting, offline initialization, and
  validation passed for bootstrap, foundation, and platform after correcting
  formatting in `infra/terraform/eks-demo/platform/main.tf`. No AWS plan or
  remote backend operation was run.
- CI now contains infrastructure-static validation, Terraform formatting and
  validation, Linux/AMD64 production-image builds with immutable SHA-256
  identity checks, and the frontend high-severity dependency audit. The remote
  CI workflow has not yet been observed green for the candidate commit. By
  project decision, that observation is deferred until after the initial
  private EKS deployment and remains mandatory before final/public sign-off.
- Flink 2.3 runtime incompatibilities were corrected for checkpoint storage,
  externalized checkpoint retention, and state TTL construction. The recorded
  disposable Kafka/Flink smoke passed with 20 published, 20 uniquely processed,
  zero failures, and completed checkpoints in 135.31 seconds.
- Four production images have built locally during preparation, but source
  changed afterward. Those earlier identities are obsolete and are not release
  digests.
- The AWS CLI v2 preflight completed on 2026-09-26. `winget` installed
  `aws-cli/2.37.4 Python/3.14.6 Windows/11 exe/AMD64` at
  `C:\Program Files\Amazon\AWSCLIV2\aws.exe`; the installer hash was checked,
  no elevation prompt appeared, and no reboot was pending. The system `PATH`
  update is available to newly opened shells. Terraform 1.13.3, kubectl 1.36.1
  (Docker Desktop), Helm 3.19.0, and kind 0.33.0 were also reverified directly.
- `aws configure list-profiles` returned no profiles. No standard local AWS
  profile directory or process-, user-, or machine-level `AWS_*` environment
  credentials were found, so `aws sts get-caller-identity` was deliberately
  skipped. No AWS API call or resource mutation was made; AWS identity remains
  unverified.
- The ECR delivery workflow and foundation OIDC publisher role are committed to
  `main`. GitHub reports the workflow active and the `ecr-release` environment
  protected by reviewer `mnihad000`, a `main`-only branch rule, and disabled
  administrator bypass. Actionlint 1.7.12, foundation Terraform formatting and
  validation, and the artifact verifier's valid, missing-image, YAML-mismatch,
  and ECR-mismatch cases passed. The initial source SHA is reachable from
  `main`; the workflow checks that before assuming its publisher role. No ECR
  image run has started, and the role ARN environment variable awaits foundation
  output.

## Evidence limits and remaining gates

The 12 backend skips above require separately opted-in browser, full B5 runtime,
real MiniLM, or Redis environments. Kubeconform also skipped schema checks for
9 kind and 8 EKS resources whose schemas were unavailable; the rendered
resource contracts passed. Neither result is an EKS runtime qualification.

The local test workstation denied removal of two isolated pytest scratch
directories created for this run (`.tmp-rc-pytest-20260926-1` and
`.tmp-rc-all-pytest-20260926-2`), including an ownership-recovery attempt. This
is a local cleanup limitation, not a test failure. The disposable PostgreSQL
container was removed, and the four pre-existing Docker services remained
running. The workstation now has about 3.3 GiB free, Docker is stopped, and the
kind API is unavailable. No local release-image build, kind load, Docker
cleanup, or Docker storage change is planned. Remote CI has not been observed
green and is explicitly deferred until after the initial private deployment;
it is not being treated as evidence or silently marked complete. Private EKS
qualification and the four release image builds/digests remain pending.

The AWS CLI preflight began with `.codex/config.toml` and
`frontend/tsconfig.app.tsbuildinfo` modified. While it ran, concurrent sessions
rewrote `infra/terraform/eks-demo/platform/main.tf` with formatting-only changes
and this readiness document, and returned the TypeScript build-info file to an
unmodified state. The preflight itself made no repository writes. Those
concurrent edits require normal review and must not be attributed to, or used
as evidence from, the AWS preflight.

## Before initial private EKS provisioning

1. Preserve the committed `4efcab2` regression evidence for the tested source
   state. Rerun affected gates if executable, dependency, build, chart, script,
   or Terraform source changes, and rerun the complete suite before the
   definitive image rebuild after EKS iteration.
2. Commit the ECR delivery workflow and foundation OIDC publisher role to the
   default branch. Configure the protected `ecr-release` GitHub environment;
   the role ARN is copied there from foundation output after foundation apply.
   On 2026-09-26, GitHub confirmed the workflow on `main` and the environment
   with reviewer `mnihad000`, a `main`-only branch policy, and administrator
   bypass disabled. The role ARN remains pending foundation apply.
3. Configure an approved AWS SSO profile or IAM credentials in a private
   operator shell, then require `aws sts get-caller-identity` to succeed. Confirm
   the region (`us-east-2` is the repository default) and resolve the underlying
   IAM role ARN for `operator_principal_arn`; never use the returned STS
   `assumed-role` session ARN.
4. Supply a globally unique state-bucket name, `owner`, `run-id`, `commit`, an
   RFC3339 `expires-at` no more than eight hours away, the operator's public
   `/32` CIDR, and a Budget notification email whose AWS subscription will be
   confirmed. Keep credentials and Secret values out of tracked files.
5. Complete the practical local B3/B4 checks. Record the workstation's memory,
   disk, and kernel boundary rather than weakening the topology or forcing the
   B5 qualification onto this host.

Foundation may be provisioned before images exist. Before application
deployment, manually approve the GitHub Actions `Publish ECR Images` run for
one explicit source SHA. It must build and push all four Linux/AMD64 images to
the foundation-created immutable repositories, verify their ECR manifest
SHA-256 digests, and publish `values-images-eks.yaml`. Download and validate
the complete artifact against foundation outputs and live ECR before passing
it to the deployment wrapper. Build image IDs are not ECR deployment digests.
The four ECR identities remain pending until this run succeeds.

The ordinary full CI run is not an initial-provisioning gate for this run.
Trigger and observe the complete workflow after the first private deployment,
incorporate any
repository-owned fixes through the immutable redeployment loop, and require it
to pass for the frozen final commit before public exposure or release sign-off.

## Private EKS qualification and iteration

1. Provision the guarded EKS foundation with public application ingress
   disabled and register the eight-hour teardown. After the approved GitHub
   Actions ECR delivery artifact is verified, upload the Secret and deploy the
   complete topology.
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
