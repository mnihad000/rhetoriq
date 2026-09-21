# RhetoriQ Operations Runbook

This runbook covers the implemented local Compose topology, Kafka/Flink
recovery, and B5 projection administration. Use a disposable project for
acceptance work and never point test commands at production Neon or reused data
volumes. [Pre-B6](PRE_B6_GUIDE.md) records workstation readiness and the order
of work before Kubernetes.

## Base stack

Create an untracked environment file from `.env.production.example` and replace
all placeholder secrets with fresh local-only values. Then run from the
repository root:

```powershell
docker compose config --quiet
docker compose up --build -d
docker compose ps
```

The root stack includes PostgreSQL/pgvector, Kafka, Apicurio, SearXNG, the API,
frontend, topic initialization, the outbox publisher, role-scoped event
workers, enrichment, and Flink. `topic-init` must finish successfully before
the event path is ready.

Check:

- `GET http://localhost:8000/health` for process liveness;
- `GET http://localhost:8000/api/research/health` for Kafka, registry, outbox,
  consumer, checkpoint, SearXNG, lag, and DLQ state;
- `GET http://localhost:8000/health/b5` when B5 retrieval is enabled;
- `GET http://localhost:8000/api/trending/status` for Flink and signal freshness.

A running process or heartbeat does not prove delivery or projection coverage.
Lag, pending revisions, freshness, failures, and degradation must agree with
the expected workload.

## Inspection and ordinary recovery

```powershell
docker compose logs topic-init
docker compose logs outbox-publisher
docker compose logs document-worker signal-worker investigation-worker projection-worker enrichment-worker
docker compose logs flink-jobmanager flink-taskmanager
docker compose exec broker /opt/kafka/bin/kafka-topics.sh --bootstrap-server broker:19092 --list
```

Failure behavior is deliberate:

- accepted domain writes and their outbox records commit atomically;
- the publisher uses idempotent Kafka delivery with `acks=all`;
- consumers use stable IDs, durable ledgers, and manual offset commits;
- transient failures retry with bounded backoff and jitter;
- schema, policy, validation, and permanent parsing failures go to the relevant
  DLQ;
- failure to publish a DLQ record leaves the source offset uncommitted;
- Kafka or registry failure grows the outbox and degrades readiness rather than
  activating synchronous execution.

Recovery order:

1. Restore PostgreSQL, Kafka, and Apicurio without editing outbox or ledger rows.
2. Confirm topic/schema initialization and compatibility.
3. Restore Flink and its checkpoint/savepoint volumes.
4. Restart the outbox publisher, persistence consumers, projection consumers,
   enrichment worker, and investigation worker.
5. Watch backlog and lag drain; inspect DLQs before replaying.
6. Verify the correlated workspace, trail, artifact hashes, projection state,
   and completion-event count.

Never repair a stuck run by deleting outbox, consumer-ledger, or lease records.

## Controlled Kafka replay

Every replay names a primary topic, partition, offset range, and audited job ID.
Use a dry run first:

```powershell
cd backend
..\.venv\Scripts\python.exe -m events.replay --topic raw.documents.v1 --partition 0 --start-offset 0 --end-offset 100 --dry-run
..\.venv\Scripts\python.exe -m events.replay --topic raw.documents.v1 --partition 0 --start-offset 0 --end-offset 100
```

Use `--correlation-id` when possible. Replay invokes normal idempotent handlers
without committing ordinary consumer offsets. It must not duplicate documents,
investigations, notifications, projections, or terminal events.

## Flink and enrichment operations

Keep `ENABLE_FLINK_TRENDING=false` until the B4 runtime gates in
[Testing](TESTING.md) pass. Automatic investigation remains off by default. The
local cluster uses Flink 2.3.0 with Python 3.12 workers, checkpoint storage, and
the UI at `http://localhost:8082`.

Use the recorded-provider acceptance override in an isolated project:

```powershell
$B4ComposeArgs = @('--project-name', 'rhetoriq-b4-acceptance', '--env-file', '.env.production.example', '-f', 'compose.yml', '-f', 'infra/flink/compose.acceptance.yml')
docker compose @B4ComposeArgs build flink-jobmanager enrichment-worker
docker compose @B4ComposeArgs up -d flink-jobmanager flink-taskmanager enrichment-worker document-worker signal-worker projection-worker
docker compose @B4ComposeArgs run --rm --no-deps enrichment-worker python -m events.b4_acceptance --disposable-stack
```

The paced load mode is intentionally separate:

```powershell
docker compose @B4ComposeArgs run --rm --no-deps enrichment-worker python -m events.b4_acceptance --disposable-stack --mode load --drain-seconds 360
```

During recovery qualification, kill and restart JobManager and TaskManager one
at a time and verify recovery from completed checkpoints without changed
semantic outputs. Preserve checkpoint volumes throughout the exercise.

Production enrichment requires positive request, spending, input-price,
output-price, and embedding-price budgets. `python -m
events.enrichment_worker --replay` uses recorded artifacts only and fails closed
when the configured model/version artifact is missing. `--reenrich` requires a
new reviewed pipeline identity and must preserve the original receipt. Hosted
Gemini embeddings and local MiniLM embeddings are never interchangeable.

After passing acceptance, enable `ENABLE_FLINK_TRENDING=true`. A failed
readiness gate serves the last valid legacy snapshot with a visible warning;
setting the flag back to `false` is the application rollback.

## B5 specialized-store stack

The full B5 topology needs Linux containers, Docker Compose 2.24.4 or later,
eight CPUs, and a qualification environment with 16 GB available to Docker.
Elasticsearch also requires the documented Linux/WSL `vm.max_map_count`
setting. The current workstation is suitable for development and reduced smoke
checks but does not meet the full memory qualification budget; see
[Pre-B6](PRE_B6_GUIDE.md).

Use a private environment file containing fresh PostgreSQL, Elasticsearch,
Neo4j, Redis, and SearXNG credentials:

```powershell
docker compose --env-file .env.b5.local -f compose.yml -f infra/b5/compose.b5.yml --profile b5 config --quiet
docker compose --env-file .env.b5.local -f compose.yml -f infra/b5/compose.b5.yml --profile b5 build
docker compose --env-file .env.b5.local -f compose.yml -f infra/b5/compose.b5.yml --profile b5 up -d
```

The B5 image preloads the pinned MiniLM model. Runtime loading is local-only and
an unavailable model is a startup failure. Initialization creates the schema,
Elasticsearch mapping, Neo4j constraints, and shared projection-generation
manifest before consumers start.

TLS bootstrap creates one local CA plus service-specific certificates.
PostgreSQL uses `sslmode=verify-full`; Elasticsearch, Neo4j, and Redis verify
the CA. Clients receive no unrelated private keys. Do not delete a certificate
volume independently of its CA or data volume. Store/admin ports remain
unpublished by default; optional administration may bind them to `127.0.0.1`
only.

## B5 administration

Run these inside the API image with the same Compose file/profile prefix, or
from `backend` with explicitly injected disposable connection settings:

```text
python -m events.b5_ops status
python -m events.b5_ops withdraw --document-id DOCUMENT --operation-id UNIQUE_ID --reason "reason"
python -m events.b5_ops restore --document-id DOCUMENT --operation-id NEW_UNIQUE_ID --reason "reason"
python -m events.b5_ops check
python -m events.b5_ops repair --repair-limit 100 --operation-id UNIQUE_ID
```

Withdrawal writes a newer tombstone, audit row, and projection event; current
canonical checks suppress the document immediately even if projection workers
are stopped. Restore creates a new revision and cannot turn a discovery-only
record into citable evidence. Check-only exits nonzero for drift or a racing
watermark. Repair is bounded and uses immutable recorded inputs; never advance a
ledger to hide a target conflict.

Bootstrap and rebuild commands:

```text
python -m events.b5_ops bootstrap --after-id "" --batch-size 100
python -m events.b5_ops bootstrap-investigations --after-id "" --batch-size 100
python -m events.b5_ops rebuild --generation rebuild-YYYYMMDD --batch-size 100
python -m events.b5_ops check --generation rebuild-YYYYMMDD
python -m events.b5_ops activate --generation rebuild-YYYYMMDD --operation-id UNIQUE_ID
python -m events.b5_ops rollback --operation-id NEW_UNIQUE_ID
python -m events.b5_ops semantic-replay --after-id "" --batch-size 100
```

Repeat bounded bootstrap/rebuild calls with the reported cursor. Activation
validates Elasticsearch and Neo4j at one unchanged PostgreSQL high-water mark
and switches the shared manifest atomically. Keep the previous generation until
the rollback window is explicitly closed. Rollback revalidates it against
current canonical inputs and never restores withdrawn evidence.

Redis caches only complete validated responses. A cache hit is checked against
the current scope, generation, canonical clocks, and delivery state. Redis
failure falls through. Elasticsearch failure allows semantic recall plus an
explicitly limited canonical lexical fallback; Neo4j failure exposes canonical
relationships with fallback labeling. No fallback may masquerade as a
successful specialized-store query.

## Disposable B5 acceptance

Use a fresh project, fresh volumes, a database name containing `test`,
disposable passwords, B5 enabled, and no provider credentials:

```powershell
docker compose --project-name rhetoriq_b5_test --env-file .env.b5.test -f compose.yml -f infra/b5/compose.b5.yml -f infra/b5/compose.acceptance.yml --profile b5 up -d
docker compose --project-name rhetoriq_b5_test --env-file .env.b5.test -f compose.yml -f infra/b5/compose.b5.yml -f infra/b5/compose.acceptance.yml --profile b5 --profile acceptance run --rm b5-acceptance python -m events.b5_acceptance --mode smoke --disposable-stack --output /reports/b5-smoke.json
```

Run B3/B4 acceptance first. Use a separate fresh project for load
qualification, start it with the same three Compose files, and then run:

```powershell
python infra/b5/qualify.py --env-file .env.b5.test --project rhetoriq_b5_load_test --output artifacts/b5-qualification.json
```

The host supervisor records memory, restarts, OOM events, backlog, visibility,
freshness, and query/cache measurements. Missing evidence keeps qualification
false. Smoke, load, recovery, browser, and live-provider checks are distinct;
none alone closes the gate. The complete matrix is in [Testing](TESTING.md).

## Retention and secrets

Topic defaults are 14 days for raw documents, 30 days for enriched/processed
documents, 90 days for signals and investigation work, 180 days for completed
investigations, and 14 days for DLQs. Provider policy may shorten retention.

Kafka, database, store, model, and provider credentials belong only in
environment or platform secret configuration. Never include them in events,
DLQs, logs, fixtures, reports, or committed commands. If a credential is
exposed, rotate it at the provider; removing it from Git is not revocation.

## Host troubleshooting

Docker Desktop’s Linux engine requires WSL 2, Virtual Machine Platform, and
firmware virtualization. If Docker reports `HCS_E_HYPERV_NOT_INSTALLED`, verify
those Windows features, restart the host, and require `docker version` to show
both client and server before running Compose. Current machine-specific status
belongs in [Pre-B6](PRE_B6_GUIDE.md), not in this durable runbook.
