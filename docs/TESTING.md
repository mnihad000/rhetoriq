# RhetoriQ Testing and Acceptance

Routine tests establish code behavior. Runtime qualification separately proves
that real Kafka, Flink, databases, models, and failure recovery work together.
Do not promote offline passes or a smoke report into an actual-stack claim.

## Routine checks

From the repository root:

```powershell
pytest backend/tests -q
python -m compileall -q backend infra/b5
cd backend
..\.venv\Scripts\python.exe -m events.export_schemas --check
cd ..\frontend
npm test -- --run
npm run build
```

Optional integration tests may skip when their explicitly named disposable
dependency is unavailable. A skip must never connect to a developer database or
be reported as runtime evidence.

## PostgreSQL and Compose

PostgreSQL integration uses only an explicit `POSTGRES_TEST_DATABASE_URL` whose
database is disposable and contains `test` in its name. Never use production
Neon or the ordinary development database.

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest tests/test_postgres_migration.py tests/test_b4_postgres.py tests/test_b5_postgres.py -q
```

Validate the base and B5 Compose graphs without loading developer credentials:

```powershell
docker compose --env-file .env.production.example -f compose.yml config --quiet
docker compose --env-file .env.production.example -f compose.yml -f infra/b5/compose.b5.yml --profile b5 config --quiet
```

Compose rendering proves configuration shape only. It does not prove images
build, services start, encrypted connections work, or events are processed.

## A2 deterministic benchmark

The committed 14-case scorecard covers sparse, conflicting, and syndicated
evidence; chronology; browser evidence; access controls; prompt injection;
private URLs; provider failure; crash recovery; budgets; replay; and unsupported
claim withholding.

```powershell
cd backend
..\.venv\Scripts\python.exe -m evaluation.a2_benchmark --check
```

Use `--write` only after an intentional fixture or evaluator change. The
generated [A2 scorecard](evaluation/A2_SCORECARD.md) must remain at its current
path because the benchmark checks it.

## A3 claim/evidence verification

The A3 evaluator requires a reviewed corpus of exactly 30 real public-source
captures: five each for origin uncertainty, direct conflict, syndicated
reporting, sparse evidence, unavailable sources, and misleading chronology.
Each capture needs a content hash, capture date, public-access confirmation,
and reviewer provenance; generated documents are not substitutes.

```powershell
cd backend
..\.venv\Scripts\python.exe -m evaluation.a3_scorecard
..\.venv\Scripts\python.exe -m evaluation.a3_live_canary
```

The release targets are at least 95% affirmed-claim precision, zero unsupported
or contradicted leakage, 100% citation-span validity, zero duplicates counted as
independent support, and exact expected dispositions. The live canary is
non-blocking and records reachability, final URL, hash, latency, disposition,
and model provenance without secrets.

## B3 event acceptance

With a fresh local environment and working Docker engine, verify:

1. topics and schemas initialize with the expected compatibility;
2. a raw document reaches normalized/persisted output;
3. an accepted investigation produces ordered stage and one stable completion
   event through Kafka-only execution;
4. duplicate delivery does not duplicate domain state;
5. retryable failures recover and permanent failures reach each DLQ;
6. broker/registry interruption grows the outbox without losing accepted work;
7. worker restart and controlled replay preserve IDs, artifacts, and completion
   counts;
8. `/api/research/health` reports numeric lag, DLQ, and outbox state.

## B4 Flink acceptance

Use the recorded-provider stack and commands in [Operations](OPERATIONS.md).
Keep `ENABLE_FLINK_TRENDING=false` until every gate passes.

| Gate | Required evidence |
| --- | --- |
| Contracts and cluster | Compatible schemas, healthy JobManager/TaskManager, and framed raw event delivery. |
| Enrichment | Immutable recorded artifacts, validation, retry/budget behavior, DLQs, and a separately bounded live canary. |
| Stream intelligence | Ordered, duplicate, out-of-order, and late fixtures produce stable documents and signal revisions. |
| Product cutover | PostgreSQL projections, freshness health, visible fallback, dashboard metrics, and manual investigation action. |
| Stabilization | Deterministic replay, checkpoint recovery, failure injection, paced load, security review, and complete regressions. |

The recorded-provider load sends 100 documents per minute for 30 minutes and
500 per minute for five minutes, then allows a six-minute drain. Record probe
JSON, application/connector identities, checkpoints, lag over time, projection
counts, recovery actions, and signal latency. Kill and restore JobManager and
TaskManager separately without losing checkpoint state or changing semantic
outputs.

Gemini and MiniLM model identities must remain distinct. A matching vector
dimension is not permission to compare or replay vectors across models.

## B5 actual-stack acceptance

Actual-stack tooling requires explicit disposable opt-in, fresh volumes,
disposable passwords, a test database, recorded hosted enrichment, and real
pinned MiniLM inference. Do not load developer dotenv files or provider keys.
Use the commands in [Operations](OPERATIONS.md).

### Scenario matrix

| Area | Required cases |
| --- | --- |
| Normalization | Unicode and whitespace literals, invalid offsets, missing/fallback dates, and reference truncation. |
| Eligibility | Lead promotion, withdrawal with stopped workers, restore without invented receipts, and changed evidence hashes. |
| Embeddings | MiniLM/Gemini separation, wrong dimension/model/input hash, finite values, and recorded replay. |
| Atomicity | Failure before snapshot/outbox commit and crash after target write but before ledger/acknowledgement. |
| Ordering | Duplicate delivery, equal-revision conflict, stale revision, and matching-input re-enrichment. |
| Graph | Investigation before document projection, multiple edge types, cycles, unresolved references, changed claims, A3 revision, and withheld reports. |
| Availability | Elasticsearch, Neo4j, and Redis outages; legitimate empty results; pending/stale/partial coverage; cache fallthrough. |
| Repair | Inject missing, stale, extra, and invalid records; detect them in check-only mode; restore them with bounded repair. |
| Rebuild | Two isolated rebuilds agree by semantic payload/edge identity/hash and catch up concurrent mutation. |
| Cutover | One manifest maps both targets, high-watermark compare-and-swap rejects races, and rollback retains previous data. |
| Browser | Dashboard → investigation → literal phrase → highlighted span → graph path → reload, including keyboard and reduced-motion behavior. |

### Load qualification

Use 10,000 distinct seeded documents, recorded hosted enrichment, real
Kafka/Flink/stores, and actual pinned MiniLM. Warm the model and initial corpus
before measurement. Sustain 100 documents per minute for 30 minutes, then 500
per minute for five minutes while ten clients issue representative queries.

Required outcomes:

- no lost canonical records or duplicate semantic relationships;
- no OOM kills or unexplained restarts;
- no sustained backlog growth and drain within ten minutes after the burst;
- target p95 persistence-to-projection freshness at most 30 seconds;
- target uncached lexical/graph p95 at most one second;
- target hybrid/path p95 at most two seconds;
- separate cache-hit and cache-miss distributions;
- recorded drift, embedding coverage, Flink checkpoint, visibility, and resource
  measurements.

Missing memory, OOM, query, freshness, backlog, drift, or embedding evidence
keeps qualification false. Retain the JSON report, Docker stats/events, offsets,
DLQs, checkpoints, canonical hashes/counts, rebuild/repair output, browser
screenshots/traces, machine allocation, and image/model/generation identities.

This 10,000-document B5 gate is separate from the longer-running
[100K documents-per-day experiment](100k%20stress%20test%20plan.md).

### B5 live canary

Acquire at most 20 real documents under explicit request and spending limits,
preserve source references and receipts, compute actual MiniLM embeddings, and
complete or withhold the investigation through the normal publication gate.
Restart services and reload the same investigation. Never invoke hosted
enrichment during repair or historical projection backfill.

## Documentation checks

Before merging documentation changes:

- resolve every relative Markdown link and anchor;
- search for references to removed or renamed files;
- reject unresolved Git merge-conflict marker lines;
- reject malformed replacement characters or known encoding artifacts;
- confirm current, implemented-but-unqualified, deployed, and planned states
  are not conflated;
- inspect examples and output for credentials, connection strings, tokens, or
  private source material.

Documentation can describe required evidence, but it cannot substitute for the
recorded runtime result.
