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
- 368 backend tests passed with 27 environment-dependent skips, 9 final B6
  deployment-contract tests passed, and 27 frontend tests plus the production
  build passed;
- all 16 disposable PostgreSQL integration tests passed, the frontend
  high-severity audit reported zero vulnerabilities, and focused post-hardening
  tests passed after the database and Flink 2.3 compatibility fixes;
- both Helm profiles linted and rendered (61 kind and 58 EKS resources), their
  built-in Kubernetes objects passed strict schema validation, all B6
  PowerShell scripts parsed, Compose rendered from the example environment,
  and all Terraform states validated without a backend or AWS credentials;
- the final Flink runtime passed non-root UID 9999, Python/PyFlink import,
  required connector/schema JAR, and no-runtime-compiler checks.
- the disposable B4 recorded-provider smoke published 20 documents and
  received 20 unique processed documents with zero failures in 135.31 seconds;
  the running job completed checkpoints. This is partial B4 runtime evidence,
  not recovery or load qualification.

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
| kind context/node | `kind-rhetoriq-b6`, Kubernetes v1.37.0, Ready | Preserve the exact context check. |
| Docker/node memory | about 6.66 GiB / 6,987,968 KiB allocatable | Treat the full smoke as measured and stop on Pending/OOM. |
| `vm.max_map_count` | `262144` | Raise deliberately to `1048576` before deployment. |
| Workspace free disk | about 15.43 GiB | Free at least 20 GiB; 25 GiB is recommended. |
| Host Helm | 3.19.0 installed on `PATH` | Reverify before runtime scripts. |
| Host Terraform | 1.13.3 installed on `PATH` | Reverify before AWS planning. |
| AWS CLI | not on `PATH` | Install and verify AWS CLI v2 before provider identity or ECR/EKS operations. |

The ignored root `.env` was not read, printed, or changed. The canonical
candidate identity, completed evidence, and pre-AWS gates are recorded in
[AWS Release Readiness](RELEASE_READINESS.md).

## Local kind sequence

The workstation currently has too little configured Docker memory for a
guaranteed run, and the last measured `vm.max_map_count` was below the required
value. Treat this as a measured experiment and never remove a component to make
it pass.

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

Copy each `demo.tfvars.example` to an ignored `.tfvars` file and replace every
placeholder. Required foundation inputs are the AWS operator principal, CIDRs,
notification email, owner, commit, run ID, and an RFC3339 deadline no more than
eight hours away. DNS/ALB stays disabled unless hosted-zone, subdomain, email,
and CIDR inputs are all supplied.

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

After separate approvals:

1. Push four immutable amd64 images with `push-images.ps1
   -ApproveImagePush`.
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
