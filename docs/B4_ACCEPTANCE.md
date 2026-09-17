# B4 verification record

Status: **In Progress**. B4 is not implemented until every gate in
[B4_SPRINT.md](B4_SPRINT.md) has recorded passing evidence.

Code and offline product integration are complete for this delivery; final
runtime acceptance remains pending. Flink cutover and automatic investigation
are disabled by default.

The approved sprint includes implementation, tests, integration checks, and
operational hardening within each slice, followed by final stabilization.
There is no separate calendar estimate.

| Gate | Required evidence | Current result |
|---|---|---|
| 1. Contracts and cluster | Compatible registration, cluster health, framed raw event reaches normalization | Contracts, snapshots, Compose validation pass; actual registry/cluster qualification pending |
| 2. Enrichment | Recorded Gemini/Groq fixtures, immutable artifacts, retries, budgets, DLQs | Offline fixtures and worker delivery checks pass; live canary pending |
| 3. Stream intelligence | Event-time horizons, deduplication, late revisions, stable replay outputs | Offline engine and recorded-artifact replay pass; real Flink qualification pending |
| 4. Product cutover | PostgreSQL projections, health gate, visible fallback, dashboard, manual action | Offline projections, health gate, frontend tests/build pass; PostgreSQL/runtime gates pending |
| 5. Stabilization | Full regression, actual Flink replay, restart recovery, failure injection, paced load, bounded live canary | Pending |

## Environment evidence

On 2026-09-16, `docker info --format '{{.ServerVersion}}'` could not connect
to the Docker daemon: the `docker_engine` named pipe was absent. The Docker
CLI exists, but this does not establish a working Docker Desktop runtime.
No Compose cluster, Kafka/Apicurio integration, TaskManager/JobManager recovery,
or paced 35-minute load result may be claimed from this workstation yet.

Docker Desktop was found installed but stopped. A background start was
attempted; a subsequent daemon probe still reported the absent named pipe.
See [B4_OPERATIONS.md](B4_OPERATIONS.md) for the disposable acceptance stack,
recorded-provider smoke/load probes, worker budgets/replay, and cutover steps.

## Local verification

- Backend regression suite: **269 passed, 19 skipped**. Skips include optional
  integrations without a disposable database or running services.
- Frontend: four test files, six tests pass; TypeScript/Vite production build passes.
- Python compilation and production/acceptance Compose configurations pass.
- The clean replay fixture forbids provider calls on its second run and compares
  processed/signal payloads, event IDs, revisions, and hashes from recorded artifacts.
- PostgreSQL tests are explicitly skipped locally without a disposable
  `POSTGRES_TEST_DATABASE_URL`; CI provisions a separate pgvector test service.
- CI now includes candidate image build, real Kafka/Flink delivery, and
  TaskManager/JobManager failure probes. Their results are not yet available.
- The full 35-minute load run and bounded live-provider canary remain pending.

Only Python 3.13 is installed on the host. The isolated Flink image must
provide Python 3.12; the API retains its Python 3.13 image. Apache documents
Python 3.9 through 3.12 support for the pinned
[Flink 2.3.0 runtime](https://nightlies.apache.org/flink/flink-docs-release-2.3/docs/getting-started/local_installation/).

## Model identity

`gemini-embedding-001` is pinned at 384 dimensions with explicit vector
normalization. These fit the existing pgvector dimension, but Gemini vectors
must not be compared with MiniLM vectors merely because both have 384 values.
Model identity must remain attached to stored embeddings; the legacy MiniLM
retrieval corpus is a separate embedding space. Google's
[embedding documentation](https://ai.google.dev/gemini-api/docs/embeddings)
documents configurable dimensionality and manual normalization for reduced
`gemini-embedding-001` vectors.

## Test isolation

Backend test collection disables automatic loading of developer dotenv files,
clears hosted credentials and `DATABASE_URL`, and assigns temporary SQLite
state. PostgreSQL tests require a separate explicit
`POSTGRES_TEST_DATABASE_URL` test target. Do not point this variable at the
developer or production Neon database.

## Closeout rule

Add exact commands, results, fixture hashes, deployed image/connector versions,
checkpoint identifiers, recovery observations, throughput/lag measurements,
and canary receipt identities below as gates pass. An offline fixture engine
or Compose configuration validation does not establish actual Flink recovery
or runtime delivery guarantees.
