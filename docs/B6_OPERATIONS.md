# B6 Full-Architecture Kubernetes and Ephemeral EKS Runbook

B6 now has one reusable Helm chart with `kind` and `eks-demo` profiles, plus
three isolated Terraform states for the short-lived AWS showcase. Repository
implementation and static validation are not runtime acceptance. No local
Kubernetes or AWS run is recorded by this document.

## Safety boundary and claim language

- Never read a secret from the root `.env` into an operator transcript. Secret
  creation reads specifically named process environment variables and prints
  no values.
- Every cluster mutation, secret upload, ECR push, Terraform apply, and
  Terraform destroy has a separate explicit switch. Planning and applying are
  separate commands using saved Terraform plans.
- A B6 pass means one-replica, demo-scale, full-topology integration with small
  seeded data, recovery, persistence, and retained evidence.
- It does not close formal B3-B5 runtime qualification, the B5 10,000-document
  qualification, or the future 100,000-documents/day capacity experiment.

## Implemented topology

The chart at `deploy/helm/rhetoriq` renders PostgreSQL/pgvector, Kafka KRaft,
Apicurio, Elasticsearch, Neo4j, Redis, SearXNG, Flink JobManager/TaskManager,
the API and frontend, outbox publisher, ordinary workers, enrichment worker,
three B5 workers, migrations, topic/schema initialization, B5 initialization,
bounded smoke, and evidence Jobs. All long-running application components have
one replica and `Recreate` rollout behavior.

Only the frontend and `/api` Ingress paths are public. Store, Kafka, registry,
Flink, and health endpoints remain private. Persistent claims cover PostgreSQL,
Kafka, Elasticsearch, Neo4j, Flink checkpoints/savepoints, and evidence. Redis
is disposable. The two Flink RWO claims are a deliberate one-node demo design,
not a multi-node production design.

Startup is dependency-gated:

1. cert-manager issues the internal root and four service leaf certificates;
   trust-manager publishes only the public root.
2. Stores, Kafka, Apicurio, and SearXNG start.
3. The migration Job exclusively applies advisory-locked migrations. Every
   long-running Kubernetes process uses check-only migration mode.
4. Topic/schema initialization waits for Kafka and Apicurio; B5 initialization
   waits for schema, Elasticsearch, and Neo4j readiness.
5. Flink, workers, API, frontend, then smoke/evidence Jobs become ready.

PostgreSQL uses `verify-full`; Elasticsearch uses authenticated HTTPS and TLS
transport; Neo4j requires encrypted Bolt; Redis is TLS-only. Each server mounts
only its leaf keypair. Clients mount the trust-manager ConfigMap. The CA private
key is never mounted into application pods.

The backend and MiniLM images deliberately use digest-pinned CPU-only PyTorch.
CUDA, NVIDIA device plugins, and GPU nodes are not part of either B6 profile.
Platform controller images are also pinned by digest; EKS-managed add-on
versions must be recorded from the reviewed foundation plan.

## Progress record

As of the latest repository verification, implementation is complete through
the non-mutating boundary. Nothing in this section is runtime acceptance.

Completed and verified locally:

- migration `apply`/`verify` separation, read-only schema verification, and
  advisory-lock behavior;
- sanitized `/health/ready`, check-only topic/schema and B5 initialization,
  durable worker heartbeats, probe commands, interruptible polling, and
  graceful shutdown;
- the full chart, both profiles, PKI, policies, storage, ingress, smoke,
  evidence, recovery, and teardown artifacts;
- three isolated Terraform states and guarded stage/application/smoke/teardown
  scripts;
- exact connector-source commit and archive-checksum verification in the Flink
  image build;
- the release-candidate regression exercised source `91a95e6` and was recorded
  with the Terraform formatting fix in commit `4efcab2`;
- with disposable PostgreSQL configured, 386 backend tests passed with 12
  optional environment-dependent skips and zero failures; all 16 disposable
  PostgreSQL integration tests also passed separately;
- Python compilation and event-schema reproduction passed without a generated
  diff; 27 frontend tests, the production build, and the frontend high-severity
  audit passed with zero vulnerabilities;
- both Helm profiles linted and rendered (61 kind and 58 EKS resources), their
  schema checks reported 52 valid/0 invalid/9 unavailable for kind and 50
  valid/0 invalid/8 unavailable for EKS, and the render-contract checks passed;
  all 19 B6 PowerShell scripts parsed, all three Compose profiles rendered from
  the example environment, and all Terraform states passed formatting and
  validation without a backend or AWS credentials;
- the final Flink runtime passed non-root UID 9999, Python/PyFlink import,
  required connector/schema JAR, and no-runtime-compiler checks.
- the disposable B4 recorded-provider smoke published 20 documents and
  received 20 unique processed documents with zero failures in 135.31 seconds;
  the running job completed checkpoints. This is partial B4 runtime evidence,
  not recovery or load qualification.
- commit `823e305` is on `further_dev`, the GitHub default branch, and on the
  historical `main`, with the guarded `Publish ECR Images` workflow,
  foundation OIDC publisher role, explicit-source reachability check, and
  artifact verifier. The ECR release path (workflow branch guard,
  source-ancestry check, and publisher-role `ref` trust) was then migrated from
  `main` to `further_dev`, and region validation plus all-zero placeholder
  digest rejection were added to the workflow and verifier. Workflow lint,
  foundation Terraform validation, verifier tests, and `git diff --check`
  passed without AWS access. The protected `ecr-release` environment still
  permits only `main` and must be switched to `further_dev` before dispatch.

Not performed:

- no Kubernetes platform release, application release, Secret upload, smoke,
  recovery drill, persistence drill, or evidence capture;
- no AWS identity/provider call, Terraform account plan, apply, image push,
  DNS change, Secret upload, billable resource, or destroy;
- no complete formal B3-B5, B5 10,000-document, or
  100,000-documents/day qualification.

Last recorded local prerequisites:

| Check | Recorded state | Required action |
| --- | --- | --- |
| kind context/node | Last verified `kind-rhetoriq-b6`, Kubernetes v1.37.0; currently unreachable because Docker is stopped | Local kind execution is deferred; do not claim the historical Ready state is current. |
| Docker/node memory | Historical measurement about 6.66 GiB / 6,987,968 KiB allocatable; Docker currently stopped | Do not restart for local image builds on this constrained host. |
| `vm.max_map_count` | `262144` | Raise deliberately to `1048576` before deployment. |
| Workspace free disk | about 2.3 GiB | Hard local boundary; do not run the four-image build, kind load, or destructive cleanup to chase the former minimum. |
| Host Helm | 3.19.0 installed on `PATH` | Reverify before runtime scripts. |
| Host Terraform | 1.13.3 installed on `PATH` | Reverify before AWS planning. |
| AWS CLI | v2.37.4 installed at `C:\Program Files\Amazon\AWSCLIV2\aws.exe` | Open a new shell and verify an approved AWS identity; no local profile or credentials were present at the recorded preflight. |

The ignored root `.env` was not read, printed, or changed. The canonical
candidate identity, completed evidence, and pre-AWS gates are recorded in
[AWS Release Readiness](RELEASE_READINESS.md).

## Local kind sequence

The workstation currently has about 2.3 GiB free, Docker is stopped, kind is
unreachable, and the last measured `vm.max_map_count` was below the required
value. The local kind sequence is deferred. Do not delete user data, alter
Docker storage, restart the local build path, or remove a component to force a
pass; use the guarded GitHub/ECR path and private EKS qualification instead.

Before any cluster mutation, run the pinned static gate. It lints and renders
both Helm profiles, checks the full topology contract, validates built-in
Kubernetes objects against the target API schemas, parses every B6 PowerShell
script, renders Compose with the example environment, and validates all three
Terraform states without configuring a backend:

```powershell
./infra/b6/validate.ps1
```

Custom-resource schemas are skipped by kubeconform because cert-manager and
trust-manager install their version-pinned CRDs as platform prerequisites; the
Helm topology validator still requires the B6 certificate resources.

1. Install Helm, then run the read-only preflight:

   ```powershell
   ./infra/b6/kind/preflight.ps1
   ```

   It refuses any context other than `kind-rhetoriq-b6`, stops below
   `vm.max_map_count=1048576`, and requires at least 20 GiB free on the
   workspace drive for image builds and demo storage. It alters neither.

2. After separately approving cluster mutation, install pinned cert-manager,
   trust-manager, and ingress-nginx releases and functionally test policy
   enforcement:

   ```powershell
   ./infra/b6/kind/install-platform.ps1 -ApproveClusterMutation
   ./infra/b6/kind/test-network-policy.ps1 -ApproveClusterMutation
   ```

   If the allowed path fails or denied path succeeds, stop. Recreating kind
   with an approved Calico/Cilium configuration is a separate decision.

3. Build the four application images, load them into kind, and record the
   digests resolved by containerd:

   ```powershell
   ./infra/b6/kind/build-load-images.ps1
   ```

4. In a private shell session, populate the seven documented
   `RHETORIQ_*` process environment variables. Use a disposable PostgreSQL
   database name containing `test`. After separately approving the write:

   ```powershell
   ./infra/b6/kind/new-secret.ps1 -ApproveSecretUpload
   ```

5. Deploy without smoke, then run the bounded 20-document smoke and evidence
   Jobs:

   ```powershell
   ./infra/b6/kind/deploy.ps1 -ApproveClusterMutation
   ./infra/b6/kind/run-smoke.ps1 -ApproveClusterMutation
   ```

   For the browser recording, run `export-public-ca.ps1`, map `rhetoriq.local`
   to `127.0.0.1` in the local hosts file, trust only that public demo CA for
   the recording session, and run `port-forward.ps1`. This exposes ingress-nginx only
   on loopback at `https://rhetoriq.local:8443`; it does not expose store or
   administration ports. Remove the hosts entry and temporary trust afterward.

6. Record a completed Flink savepoint, restart an ordinary worker, a B5 worker,
   TaskManager, JobManager, and every durable store one at a time:

   ```powershell
   ./infra/b6/kind/exercise-recovery.ps1 -ApproveClusterMutation
   ```

7. Capture at least 15 minutes of kubelet working-set samples, events, pod
   identities, sanitized logs, and the evidence PVC:

   ```powershell
   ./infra/b6/kind/collect-evidence.ps1 -Minutes 15 -ApproveClusterMutation
   ```

Stop on Pending pods, OOMKills, failed policies, failed checkpoints, drift, or
count mismatches. Preserve the evidence. The only next choices are an approved
Docker memory increase or moving the full smoke to EKS.

## EKS sequence and stop points

Use the EKS environment first as a private, time-bounded qualification
environment. A passing local regression and four verified ECR manifest digests
are required before application deployment, but the ordinary full CI run is
deferred until after the initial private deployment. It remains mandatory for
the frozen final commit before public exposure or release sign-off. The
resource-heavy formal B3–B5 scenarios may run on EKS. Public application
ingress is a final proof step, not the initial deployment mode.

The 2026-09-26 preflight found no standard local AWS profiles or environment
credentials. AWS CLI v2.37.4 is installed, but no identity has been verified.
In a private operator shell, configure an approved SSO profile or IAM
credentials and require `aws sts get-caller-identity` to succeed before any
provider plan or mutation. For SSO, resolve the underlying role ARN, including
the `aws-reserved/sso.amazonaws.com/<region>/` path where applicable; an
`arn:aws:sts::...:assumed-role/...` session ARN is not a valid substitute for
`operator_principal_arn`.

Copy each `demo.tfvars.example` to an ignored `.tfvars` file and replace every
placeholder. The private deployment input checklist is:

- confirm the AWS region; repository examples and defaults use `us-east-2`;
- choose a globally unique bootstrap `state_bucket_name`, and use the resulting
  bucket in the foundation and platform `backend.hcl` files and as platform
  `state_bucket`;
- supply `owner`, `run-id`, `commit`, and an RFC3339 `expires-at` no more than
  eight hours after deployment starts;
- supply the actual IAM role ARN as `operator_principal_arn` and the operator's
  current public IP as a `/32` in `operator_cidrs`; optional `ci_cidrs` defaults
  to an empty list;
- supply the AWS Budget `notification_email` and confirm the subscription email
  before the application stage; and
- if this AWS account already has a shared GitHub Actions OIDC provider, set
  `github_oidc_provider_arn` to its ARN so foundation reuses it without owning
  its lifecycle; otherwise leave it empty so foundation creates one; and
- create the `rhetoriq-secrets` Secret in namespace `rhetoriq-demo` before the
  Helm release. Required keys are `database-url`, `postgres-user`,
  `postgres-password`, `postgres-db`, `elasticsearch-password`, `neo4j-auth`,
  `neo4j-password`, `redis-password`, and `searxng-secret`; `gemini-api-key` and
  `groq-api-key` are optional.

Keep DNS/ALB and public application ingress disabled for the initial
qualification deployment. `hosted_zone_id`, `subdomain`, and application
`allowed_cidrs` are therefore not initially required. `operator_cidrs` remains
required because it controls access to the EKS API endpoint.

Use `infra/b6/eks/invoke-stage.ps1` twice per stage: first `-Action Plan`, review
the saved plan, then `-Action Apply -ApproveAwsChanges`. The required order is:

1. `bootstrap`
2. `foundation-standard`
3. `platform-prerequisites`
4. `foundation-strict`

The foundation uses two public subnets, no NAT Gateway, one on-demand x86
`m7i.2xlarge`, encrypted gp3, EKS Pod Identity, immutable ECR repositories,
three-day control-plane log retention, and a $25 Budget. The standard-policy
stage avoids breaking system endpoints before broad availability policies are
installed in platform namespaces. The strict stage then enables VPC CNI
NetworkPolicy and must pass both a positive/negative network test and the node
sysctl check.

Before foundation apply, the wrapper must register a Windows scheduled task at
the eight-hour deadline. The task invokes the same reverse-order teardown. A
Budget notification is advisory; confirm its email subscription before the
application stage.

`further_dev` is the GitHub default branch and the only permitted ECR release
branch; `main` is retained as a secondary historical branch and is not part of
the release path. The `Publish ECR Images` workflow must already be committed
to `further_dev`. Before dispatching it, configure the `ecr-release` GitHub
environment with a required reviewer, no administrator bypass, and a
deployment-branch rule allowing only `further_dev` (no wildcards or tags).
After foundation apply, set its `ECR_PUBLISH_ROLE_ARN` environment variable
from `terraform -chdir=infra/terraform/eks-demo/foundation output -raw
ecr_publisher_role_arn`. The value is a role ARN, not an AWS credential. The
foundation role trusts only this repository, environment, workflow, and
`refs/heads/further_dev` ref, and can publish only to its four ECR
repositories. Foundation has never been applied, so the `main` to
`further_dev` trust change needs no in-place update of a deployed role.
GitHub currently reports this repository's OIDC subject prefix as
`repo:mnihad000@181536152/rhetoriq@1275473509`; the trust policy uses its
exact `:environment:ecr-release` subject. Recheck GitHub's OIDC configuration
before a future publisher-role change. Pinning the workflow file through the
`job_workflow_ref` claim is optional future hardening only; it has not been
observed for this non-reusable workflow and is not configured.

After separate approvals:

1. Dispatch `Publish ECR Images` on `further_dev` with the exact 40-character
   source SHA, the foundation `run_id`, and its region. For the initial
   deployment, use `7d1c4f2ab91bb5a557050b9c591ea621fd6c1652` if that is still
   the selected source. The workflow validates the inputs and verifies the
   source is reachable from `further_dev` before assuming the publisher role. Wait for all four Linux/AMD64 builds and
   ECR readbacks to pass. Download the run's `ecr-images-*` artifact and run
   `verify-image-artifact.ps1 -ArtifactDir <downloaded-directory> -SourceSha
   <full-sha> -RunId <run-id>` from the repository root. The verifier checks
   foundation outputs, the four tags and ECR manifest digests, then writes
   `infra/b6/generated/values-images-eks.yaml`. Supply that file as
   `-ImageValues` to the deployment wrapper. A partial or failed run is not
   deployment evidence; new source requires a new immutable run and digests.
2. Upload the Kubernetes Secret with `new-secret.ps1
   -ApproveSecretUpload`; Terraform and Helm values never contain its data.
3. Use `deploy-application.ps1 -Action Plan`, review it, then run the same
   command with `-Action Apply -BudgetSubscriptionConfirmed
   -ApproveBillableAwsChanges`.
4. Use `run-smoke.ps1` in separate reviewed Plan/Apply pairs for `smoke` and
   then `evidence`.
5. Run the same recovery and evidence logic against the EKS context. A live
   provider canary is a different approval and is capped at 20 documents and
   $5; recorded enrichment is the default.

The GitHub artifact supplies the digest values consumed by the later wrappers.
Do not use local build image IDs as deployment digests or run the local
`push-images.ps1` path for this workstation.
`deploy-application.ps1` injects `deploy_application=true` and all four image
digests; `run-smoke.ps1` injects the smoke and evidence flags. Do not manually
duplicate those generated values in the base tfvars.

### Private qualification and redeployment loop

Run the remaining formal B3 DLQ/replay/interruption/recovery, B4 event-order/
failure/checkpoint-recovery/load, and B5 store/degradation/repair/rebuild/
rollback/10,000-document scenarios against the private EKS deployment using
the acceptance contracts in [Testing](TESTING.md) and operational procedures in
[Operations](OPERATIONS.md). The built-in EKS smoke is necessary but does not
replace those scenarios.

For every repository-owned failure:

1. preserve the failing evidence and identify its commit, digests, and Helm
   revision;
2. fix and test locally, commit the change, and rerun the affected local gate;
3. build and push new immutable image digests—never overwrite or reuse the old
   release identity;
4. create and review a new application plan, apply it, wait for the rollout,
   and rerun the affected EKS gates;
5. repeat until stable while preserving the eight-hour deadline and cost
   boundary.

Before public proof, freeze one final commit, rerun the complete regression and
remote CI, rebuild all four definitive digests, deploy that exact identity, and
rerun the final smoke/recovery/evidence set. Only then enable restricted HTTPS
ingress through a separately reviewed plan. Evidence from provisional commits
must not be presented as evidence for the final release.

## Video and evidence checklist

Record only sanitized material:

- commit, chart version, image digests, MiniLM revision, Kubernetes/EKS
  versions, node type, region, profile, run ID, and deadline;
- Pods, Jobs, PVCs, Certificates, and functional NetworkPolicy allow/deny;
- migration, topic/schema, and B5 initialization completion;
- the bounded end-to-end flow and canonical/ES/Neo4j/MiniLM counts;
- Kafka topics, lag and DLQs, Apicurio subjects, Flink running job and completed
  checkpoint/savepoint, and worker heartbeats;
- frontend HTTPS investigation, evidence/source span, graph/path, and reload;
- worker/Flink restart catch-up, stateful restart persistence, no duplicate
  canonical records, resource working sets, restarts, and OOM state;
- local evidence export, reverse-order destruction output, and zero-residual
  AWS checks.

## Teardown

Begin manual teardown when recording ends or at hour seven. First export the
evidence PVC. Then invoke `teardown.ps1` with `-EvidenceExported` and
`-ApproveAwsDestruction`. It requests a final savepoint, scales consumers down,
destroys platform state, explicitly deletes ECR image manifests, destroys
foundation, and writes an ignored local audit snapshot. It then explicitly
checks EKS, EBS volumes, Elastic IPs, load balancers, ECR, VPC, IAM, ACM,
Route 53, Budgets, SNS, CloudWatch log groups, and the Resource Groups Tagging
API. Only an empty inventory permits bootstrap-state destruction and scheduled
task removal. The scheduled deadline uses
`-DeadlineGuard` so cloud destruction is not blocked by a failed evidence copy.

Success requires an empty residual inventory or a separately documented,
pre-existing resource. An `expires-at` tag and Budget alert never count as
automatic teardown.
