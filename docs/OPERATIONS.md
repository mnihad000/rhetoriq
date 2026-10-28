# RhetoriQ Operations Runbook

This runbook covers the PostgreSQL-backed local topology and optional B5
stores. Kafka, Apicurio, Flink, Kubernetes, and AWS execution are historical
reference architecture on `further_dev`, not current deployment dependencies.

## Base stack

Create an untracked environment file from `.env.production.example`, replace
all placeholder secrets with fresh local-only values, and run from the root:

```powershell
docker compose --env-file .env.local config --quiet
docker compose --env-file .env.local up --build -d
docker compose --env-file .env.local ps
```

The root stack contains PostgreSQL/pgvector, SearXNG, API, one `worker`, and
the frontend. The worker claims pending `event_outbox` jobs using a lease,
renews its lease while running, and records completion or bounded retry state.
The database transaction fence rejects writes from expired or superseded jobs.

Check:

- `GET http://localhost:8000/health` for process liveness;
- `GET http://localhost:8000/api/research/health` for database, queue, worker,
  checkpoint, and provider state;
- `GET http://localhost:8000/health/b5` only when B5 retrieval is enabled.

A running process or heartbeat does not prove useful work completed. Inspect
queue age, attempts, failures, lease ownership, receipt state, and terminal
investigation status.

## Inspection and recovery

```powershell
docker compose --env-file .env.local logs api
docker compose --env-file .env.local logs worker
docker compose --env-file .env.local restart worker
```

Accepted domain writes and their journal records commit atomically. A worker
crash leaves the leased job available after lease expiry. Retryable failures
use bounded backoff and attempts; permanent failures remain visible on the job
and investigation. Never repair a stuck run by deleting journal, ledger,
checkpoint, or lease records.

When restarting a worker, verify that the old lease has expired or that its
generation has been fenced before inspecting the new worker's result. A stale
worker must not be able to commit application state.

## B5 specialized-store overlay

The B5 topology is optional and intended for measured product demand, not the
base Railway deployment. It needs Linux containers, Docker Compose 2.24.4 or
later, eight CPUs, 16 GB available to Docker, and the documented Linux/WSL
`vm.max_map_count` setting for Elasticsearch.

Use fresh credentials in a private environment file:

```powershell
docker compose --env-file .env.b5.local -f compose.yml -f infra/b5/compose.b5.yml --profile b5 config --quiet
docker compose --env-file .env.b5.local -f compose.yml -f infra/b5/compose.b5.yml --profile b5 build
docker compose --env-file .env.b5.local -f compose.yml -f infra/b5/compose.b5.yml --profile b5 up -d
```

The B5 image preloads the pinned MiniLM model. TLS bootstrap creates one local
CA and service certificates; PostgreSQL, Elasticsearch, Neo4j, and Redis
verify that CA. Store/admin ports remain unpublished by default. The database
worker remains the execution boundary; B5 stores are derived projections and
must not be treated as the source of truth.

Keep B5 data and certificate volumes together. Do not delete a certificate
volume independently of its CA or data volume. A B5 outage must degrade to
canonical PostgreSQL behavior or an explicit unavailable response, never an
unlabeled successful result.

## Backups and secrets

Take a managed PostgreSQL backup or branch before migrations. Exercise restore
and application rollback before public release; the migration runner has no
destructive down-migration path. Keep database, store, model, and provider
credentials only in platform secret configuration. If a credential is exposed,
rotate it at the provider; removing it from Git is not revocation.

## Host troubleshooting

Docker Desktop's Linux engine requires WSL 2, Virtual Machine Platform, and
firmware virtualization. If Docker reports `HCS_E_HYPERV_NOT_INSTALLED`, verify
those Windows features and require `docker version` to show both client and
server before running Compose. Machine-specific status belongs in local notes,
not in this durable runbook.
