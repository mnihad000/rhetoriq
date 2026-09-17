# B5 operations and B6 handoff

Status: implementation available; actual startup, failure and load qualification pending.

## Local deployment

Allocate 16 GB RAM and 8 CPUs to Docker Desktop/WSL. Use Linux containers and Docker Compose supporting `!override` (2.24.4 or later). Elasticsearch's WSL/Linux `vm.max_map_count` prerequisite must be checked before startup; use the current [Elastic Docker instructions](https://www.elastic.co/docs/deploy-manage/deploy/self-managed/install-elasticsearch-docker-prod). Set `vm.max_map_count=1048576` in the Docker/WSL Linux VM, then verify it persists across restarts. The Compose overlay uses [Elastic self-managed packaging](https://www.elastic.co/docs/deploy-manage/deploy/self-managed/installing-elasticsearch) and the documented [Neo4j Community Docker image](https://neo4j.com/docs/operations-manual/current/docker/introduction/).

Create a private local environment file from `.env.production.example`; supply PostgreSQL, Elasticsearch, Neo4j, Redis and SearXNG secrets. Set `ENABLE_B5_RETRIEVAL=true` only for the intended B5 deployment. URL-encode special characters in the PostgreSQL URL password. Keep the environment file untracked. Provider keys are needed only for authorized live enrichment/canaries; recorded-provider acceptance supplies none.

From repository root (replace `.env.b5.local` with the private file):

```powershell
docker compose --env-file .env.b5.local -f compose.yml -f infra/b5/compose.b5.yml --profile b5 config --quiet
docker compose --env-file .env.b5.local -f compose.yml -f infra/b5/compose.b5.yml --profile b5 build
docker compose --env-file .env.b5.local -f compose.yml -f infra/b5/compose.b5.yml --profile b5 up -d
```

The B5 image preloads the pinned Hugging Face model during build. Workers use local-only loading at runtime. An unavailable model is a startup failure, not a zero-vector success. The init job creates the durable schema, ES mapping, Neo4j constraints and the shared generation manifest before projection consumers start. PostgreSQL migrations use a transaction advisory lock. Flink still requires its B4 checkpoint/savepoint volumes and recorded acceptance prerequisites.

TLS bootstrap creates a local CA and separate service certificates. Each service gets only its own private key; clients mount only the public CA. PostgreSQL clients use `sslmode=verify-full`; ES verifies HTTPS with the CA; Neo4j uses encrypted Bolt with a custom trusted CA; Redis uses verified TLS. Do not delete a certificate volume independently of its CA or corresponding data volumes. TLS bootstrap refuses inconsistent keys/certificates. Certificate renewal and existing Neo4j password rotation require a planned maintenance procedure; `NEO4J_AUTH` initializes new data volumes rather than resetting existing credentials.

## Ports, resources and probes

| Service | Internal port | Initial memory cap | Readiness/liveness |
| --- | --- | --- | --- |
| Elasticsearch | HTTPS 9200; internal transport 9300 | 2 GiB; heap 1 GiB | Verified authenticated HTTPS |
| Neo4j Community | encrypted Bolt 7687; HTTP disabled | 2 GiB; heap/page cache 768 MiB each | Authenticated encrypted local Bolt |
| Redis | TLS 6379; plaintext disabled | 256 MiB; cache 128 MiB; allkeys LRU | Verified authenticated TLS PING |
| MiniLM worker | None | 1.5 GiB | Model preload, Kafka loop and durable heartbeat |
| ES/Neo4j workers | None | 384 MiB each | Durable heartbeat, delivery/lag health |
| API | 8000 | 1 GiB | `/health` process liveness; `/health/b5` dependency/projection readiness |
| Investigation worker | None | 640 MiB | Existing leased scheduler/events health |
| Flink JobManager/TaskManager | 8081 / RPC internal | 1 / 2 GiB | Existing B4 REST/job/worker probes |
| PostgreSQL | 5432 | 448 MiB | `pg_isready`; application verified TLS readiness |

Steady-state Compose memory caps total **13 GiB**, under 14 GB in decimal units. Transient init jobs run before projection workers; the acceptance reporter adds 512 MiB. These are configured limits, not measured working-set evidence. The 8-CPU allocation is shared by the whole stack; individual service caps may be oversubscribed. Qualification must prove the stack remains viable at this allocation.

New store/admin ports are unpublished by default. Kafka 9092, registry 8081 and Flink UI 8082 are bound to localhost by the overlay. API/frontend use existing 8000/4173 application bindings. An optional administrative override may publish store ports to `127.0.0.1` only. Keep stores off public ingress.

`/health` does not test databases and remains live during a database outage. `/health/b5` reports store/cache availability, canonical counts, pending revisions/age, worker heartbeats, recorded embeddings, failures and generation validation. Existing event health supplies consumer offsets, retries and DLQs. A heartbeat does not prove projection coverage: inspect both delivery lag and query degradation. Graceful shutdown stops polling, finishes the current bounded handler, closes the consumer/target and records stopped state.

## Operator commands

Commands below run inside the API image through `docker compose ... exec api`; use the full environment/file/profile prefix above. Alternatively run from `backend` with an explicitly injected database URL and verified target credentials/CA paths.

```text
python -m events.b5_ops status
python -m events.b5_ops withdraw --document-id DOCUMENT --operation-id UNIQUE_ID --reason "reason"
python -m events.b5_ops restore --document-id DOCUMENT --operation-id NEW_UNIQUE_ID --reason "reason"
python -m events.b5_ops check
python -m events.b5_ops repair --repair-limit 100 --operation-id UNIQUE_ID
```

Withdrawal creates a newer tombstone, audit row and projection outbox event. Current canonical checks suppress retrieval/cache/path use immediately, even with stopped workers. Restore creates another revision and preserves receipt requirements; it cannot make a discovery lead citable. Repeating an identical operation is safe; reusing its identity with changed action/document/reason fails. Historical citations remain recorded. Withdrawal is not physical deletion or retention erasure.

Check-only returns nonzero when drift or a racing canonical watermark is detected. Repair is bounded and uses immutable recorded inputs; re-run check after repair. Missing, stale, extra or invalid records are visible drift, rather than a successful empty query. Do not paper over a hash conflict by advancing a delivery ledger. Inspect the canonical source operation and target before repair.

## Historical bootstrap and isolated rebuild

```text
python -m events.b5_ops bootstrap --after-id "" --batch-size 100
python -m events.b5_ops bootstrap-investigations --after-id "" --batch-size 100
python -m events.b5_ops rebuild --generation rebuild-20260917 --batch-size 100
python -m events.b5_ops check --generation rebuild-20260917
python -m events.b5_ops activate --generation rebuild-20260917 --operation-id UNIQUE_ID
python -m events.b5_ops rollback --operation-id NEW_UNIQUE_ID
python -m events.b5_ops semantic-replay --batch-size 100
```

Bootstrap reports `next_after_id`; repeat with that cursor until `complete=true`. It reads processed lineage, canonical corpus, research/retrieved/discovery artifacts and persisted investigations. Receipt-bearing content has precedence. Active investigations are skipped until terminal. Historical documents lacking retained HTML remain unqualified for source-reference extraction; no links or offsets are invented.

Rebuild creates an isolated generation and records a durable starting watermark. Projection consumers also apply intervening mutations to building generations. Re-run bounded rebuild using `--after`/reported `next_after` for catch-up, then check. Activation validates both targets and atomically changes one PostgreSQL manifest at an unchanged high-water mark. Requests capture that manifest once; ES and Neo4j use the same generation. Physical ES indexes remain individually addressable for administration. Administrative aliases must not replace the shared manifest.

Keep previous target data for rollback. Rollback validates the retained generation against current canonical inputs; if mutations have intervened, repair/catch it up and revalidate first. Rollback never makes an old withdrawn document eligible. Retired data must not be pruned until rollback retention has been explicitly decided. MiniLM replay uses recorded model/input artifacts and never hosted enrichment.

## Cache and fallback

Redis defaults: TTL 30 seconds, maximum response 256 KiB, LRU within 128 MiB. Cache only complete validated responses. Scope, normalized query/filters, generation, canonical document/investigation clocks and target delivery state are key inputs. Current authority is checked on hits. Redis failure falls through; it does not block the API.

ES failure retains semantic recall and an explicitly limited canonical lexical scan. Neo4j failure exposes immutable canonical relationship data with fallback origin and limitations. Semantic inference failure never silently switches embedding spaces. Pending projection, stale target and service unavailability are visible to the workspace. Never describe a canonical fallback as a successful Neo4j/ES query.

## Disposable actual-stack acceptance

Use a fresh project name and an untracked environment file with `POSTGRES_DB=rhetoriq_b5_test`, disposable passwords, B5 enabled, and no provider credentials:

```powershell
docker compose --project-name rhetoriq_b5_test --env-file .env.b5.test -f compose.yml -f infra/b5/compose.b5.yml -f infra/b5/compose.acceptance.yml --profile b5 --profile acceptance up -d
docker compose --project-name rhetoriq_b5_test --env-file .env.b5.test -f compose.yml -f infra/b5/compose.b5.yml -f infra/b5/compose.acceptance.yml --profile b5 --profile acceptance run --rm b5-acceptance python -m events.b5_acceptance --mode smoke --disposable-stack --output /reports/b5-smoke.json
```

Run B3/B4 actual Kafka/Flink acceptance and recovery first. B5 load mode executes the prescribed measured windows with ten query clients. Preserve report volume contents and container memory/OOM evidence. Browser acceptance uses the persisted seeded investigation and explicit local opt-in. The [acceptance record](B5_ACCEPTANCE.md) defines every additional failure drill and the provider-budgeted live canary. Do not mark the milestone complete from a smoke report alone.

## B6 handoff

The authoritative topology is `compose.yml` + `infra/b5/compose.b5.yml`; immutable new store/model references are in `infra/b5/runtime-lock.json`. Build/push the B5, existing backend/frontend and pinned Flink images to the local cluster registry before creating B6 manifests. Record the built application-image digest; `rhetoriq-b5:local` is a local build tag, not an immutable deployment reference.

Persistent volumes: PostgreSQL canonical data; Kafka log; Flink checkpoints/savepoints; ES data; Neo4j data; CA private/public material and each service keypair; acceptance reports. Redis is disposable cache and does not need durable application data. Bootstrap jobs: TLS/secret provisioning, advisory-locked PostgreSQL migrations, topic/schema initialization, ES mappings and Neo4j constraints/model verification. Dependency order: TLS → PostgreSQL/stores; PostgreSQL + broker + registry → topics; topics/stores → B5 init; B5 init → projection consumers; topics → Flink; API can remain live while readiness is degraded.

Secrets must become cluster Secrets or an equivalent local secret source; public CA trust can be a ConfigMap. Keep CA/private keys service-specific and read-only to clients. Provide startup/readiness/liveness separately, preserve worker 45-second shutdown grace, and carry these resource caps into B6 only after measured qualification. The B6 seeded acceptance procedure must reproduce the same persisted-investigation search/path/reload evidence. Kubernetes manifests, Helm, cluster ingress, storage classes, scheduling and rollout policies are intentionally B6 work.
