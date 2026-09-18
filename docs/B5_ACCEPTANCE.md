# B5 acceptance evidence

Status: **Pending actual runtime acceptance**. No real Kafka/Flink, Elasticsearch, Neo4j, Redis, MiniLM load or browser acceptance result is claimed from offline tests. Runtime execution is deferred for this closeout at the user's request; no further Docker availability checks are required now.

## Safety and isolation

Tests do not load developer dotenv or provider credentials. PostgreSQL integration uses only explicit `POSTGRES_TEST_DATABASE_URL`, with a local disposable database whose name contains `test`. Actual-stack tooling requires both `B5_RUNTIME_TESTS=true` and `--disposable-stack`. Use a separate Compose project, fresh named volumes, disposable credentials, recorded Gemini enrichment and preloaded real MiniLM. Never point this fixture at production Neon or an existing deployment.

## Offline evidence, 2026-09-17

| Check | Recorded result |
| --- | --- |
| Complete backend suite (`pytest backend/tests -q`) | **325 passed, 23 optional checks skipped**; disposable PostgreSQL, actual-stack and browser checks remain opt-in |
| Covered B5 boundaries | Atomic outbox failure; write/ledger crash; recorded semantic replay; withdrawal with stopped workers and cache hits; missing/stale/extra/invalid drift and bounded repair; historical pinned rebuild; literal Unicode offsets; revision conflict; partial bulk failure; bounded API validation |
| Frontend product suite (`npm test -- --run`) | **17 passed across 7 files**, including observed-only path defaults, accessible edge expansion and pending-to-current graph refresh |
| Frontend production build | Passed; Vite retains a bundle-size advisory for the main chunk |
| Committed event schemas (`events.export_schemas --check`) | Passed, including projection topics/DLQs and additive reference contract |
| Merged B5 and acceptance Compose configuration using template environment | Passed |
| Python source compilation (`compileall -q backend infra/b5`) | Passed |
| Installed Python dependencies (`pip check`) | No broken requirements |
| Runtime image digests | Resolved from public registry manifests; committed in `infra/b5/runtime-lock.json` |

These rows describe offline verification. Disposable PostgreSQL, service startup, encrypted connections, actual model inference and runtime failure drills are separate requirements.

The acceptance runner has a durable seeded investigation with recorded document hyperlinks for an observed path, exact paced windows, batch revision/hash delivery checks, excluded warm-up, refreshed ES visibility samples, concurrent uncached lexical/hybrid/graph/path probes, classified cache measurements, and minute backlog traces. The host supervisor at `infra/b5/qualify.py` adds aggregate memory/restart/OOM evidence without a Docker socket in the application containers. None of that tooling is itself passing runtime evidence.

## Executable checks

From `backend`, using the workspace virtual environment:

```powershell
../.venv/Scripts/python.exe -m pytest tests -m "not integration" -q
../.venv/Scripts/python.exe -m events.export_schemas --check
../.venv/Scripts/python.exe -m compileall -q .
```

For disposable PostgreSQL, explicitly set the local test URL, then run:

```powershell
../.venv/Scripts/python.exe -m pytest tests/test_postgres_migration.py tests/test_b4_postgres.py tests/test_b5_postgres.py -q
```

From `frontend`:

```powershell
npm test -- --run
npm run build
```

Validate topology without loading developer dotenv:

```powershell
docker compose --env-file .env.production.example -f compose.yml -f infra/b5/compose.b5.yml --profile b5 config --quiet
```

Actual-stack commands and prerequisites are in [B5 operations](B5_OPERATIONS.md). The harness produces machine-readable evidence, never substitutes recorded MiniLM output for actual inference, and must leave qualification false whenever required measurements are unavailable.

## Required scenario matrix

| Area | Required cases | Actual-stack evidence |
| --- | --- | --- |
| Normalization | Unicode, whitespace literals, invalid offsets, missing/fallback dates, reference truncation | Pending |
| Eligibility | Lead promotion, withdrawal with stopped workers, restore without invented receipts, changed evidence hashes | Pending |
| Embeddings | MiniLM/Gemini separation, wrong dimension/model/input hash, finite values, recorded replay | Pending |
| Atomicity | Failure before snapshot/outbox commit; crash after target write before ledger/ack | Pending |
| Ordering | Duplicate delivery, equal-revision conflict, stale revision, matching-input re-enrichment | Pending |
| Graph | Investigation before document projection, multiple edge types, cycles, unresolved references, changed claims, A3 revision, withheld report | Pending |
| Availability | ES/Neo4j/Redis outages, legitimate empty response, pending/stale/partial coverage, cache fallthrough | Pending |
| Repair | Inject missing/stale/extra/invalid records; check-only detects; bounded repair restores | Pending |
| Rebuild | Two isolated rebuilds equivalent by semantic payload/edge identity/hash; concurrent mutation catch-up | Pending |
| Cutover | One manifest maps both targets; high-watermark CAS rejects races; rollback retains previous data | Pending |
| Browser | Dashboard → investigation → literal phrase → highlighted source span → graph path → reload; keyboard/reduced motion | Pending |

Offline tests exercise the domain boundaries. All actual-stack rows require passing recorded evidence before their gate closes.

## Performance qualification

Use 10,000 distinct seeded documents with recorded hosted enrichment, real Kafka/Flink, real stores, and actual pinned MiniLM. Preload/warm the model and initial corpus before measured windows. Sustain 100 documents/minute for 30 minutes, then 500/minute for five minutes; run ten concurrent query clients during ingestion. Track rates at persistence and projection, rather than only raw publishing.

Pass conditions: no lost canonical records; no duplicate semantic relationships; no memory-limit kills; no sustained backlog growth; drain within ten minutes after the measured burst. Target p95 processed-persistence-to-projection freshness ≤30 seconds, uncached lexical/graph queries ≤1 second, hybrid/path queries ≤2 seconds. Record raw-to-search separately, including Flink checkpoints. Separate cache hit/miss distributions. Missing query, OOM, drift, backlog or embedding coverage measurements cannot qualify the run.

Retain the JSON report, container inspect/stats output, broker offsets/DLQs, Flink checkpoint/job evidence, canonical count/hash checks, repair/rebuild reports, and browser screenshots/traces. Record machine allocation, image/model identities and generation manifest with each run. A load result is a measured capacity experiment, not a live daily-document claim.

## Live canary

Acquire at most 20 actual documents under explicit provider request/spend limits. Capture actual source references and acquisition receipts, compute MiniLM embeddings, complete or withhold the investigation through the existing publication gate, then restart and reload it. Record provider budget use and errors without secrets. Never invoke hosted enrichment during backfill/repair.

## Gate sign-off

All five gates currently await actual runtime evidence. B3/B4 runtime prerequisites remain open unless their own acceptance records show passing actual delivery, recovery and load checks. B5 is complete only after this record contains passing evidence for every scenario and gate, the live canary, backend/disposable-PG/schema/compile/frontend/browser/Compose checks, and the reviewed B6 handoff.
