# RhetoriQ Testing

The verification baseline is backend tests, Python compilation, schema snapshots, secret scanning, PostgreSQL migration tests, Kafka/Apicurio integration, frontend tests/build, Compose validation, and live B2 provider canaries. Flink, Elasticsearch, Neo4j, Kubernetes, and browser rendering remain outside B2/B3.

## Backend tests

From the repository root, with the backend dependencies installed:

```powershell
pytest backend/tests
```

The suite uses local fixtures and supports optional dependencies being absent. Tests that require an unavailable optional runtime may be skipped.

## Python compilation

```powershell
python -m compileall backend
```

## PostgreSQL migration check

The CI backend job starts a pgvector-enabled PostgreSQL 17 service and runs the migration and corpus integration tests
with `POSTGRES_TEST_DATABASE_URL`. To run it locally, provide a reachable
PostgreSQL URL and execute:

```powershell
$env:POSTGRES_TEST_DATABASE_URL="postgresql://user:password@localhost:5432/rhetoriq_test"
cd backend
pytest tests/test_postgres_migration.py
```

The production API runs the same committed migrations against Neon during
container startup. Reapplying a migration must be a no-op because applied
versions are tracked in `schema_migrations`.

## Frontend production build

```powershell
cd frontend
npm run build
```

Frontend component tests:

```powershell
cd frontend
npm test
```

## B2 provider verification

`backend/tests/test_b2_research_tools.py` covers provider fixtures, malformed responses, retries, pagination, deduplication, source policy, canonical provenance, and visible fallback behavior. A live Federal Register probe can be run with:

```powershell
cd backend
..\.venv\Scripts\python.exe -c "from services.federal_register import FederalRegisterClient; from models.investigation import InvestigationPlanTimeWindow; print(FederalRegisterClient().search('public records', InvestigationPlanTimeWindow(label='recent'), limit=1).diagnostics)"
```

The SearXNG live canary requires the local Compose stack or a configured private SearXNG URL. A provider outage is a passing degradation case only when the returned diagnostics visibly identify the internal-corpus fallback.

## B3 contract and integration verification

Unit/contract coverage is in `backend/tests/test_b3_event_backbone.py`. It validates all schemas, outbox durability, raw-to-processed handling, payload-hash mismatch rejection, stage/completion event behavior, signal replay idempotency, compatibility configuration, permanent-failure classification, and redaction.

Generate committed schema snapshots:

```powershell
cd backend
..\.venv\Scripts\python.exe -m events.export_schemas
```

Validate and start the integration stack:

```powershell
$env:POSTGRES_PASSWORD="<local-secret>"
$env:SEARXNG_SECRET="<local-secret>"
docker compose config
docker compose up --build -d
docker compose ps
```

Then verify topic registration/compatibility, a raw-to-processed document, a queued investigation through ordered stage/completion events, duplicate delivery, every DLQ, restart recovery, and controlled replay. `GET /api/research/health` must show ready Kafka/registry/consumer components with numeric lag and DLQ counts.

## Documentation checks

Before merging documentation changes, verify that project-authored Markdown has no conflict markers or mojibake and that all relative Markdown links resolve within the repository. The check may be run with the PowerShell command in the A1 completion record in [ROADMAP.md](ROADMAP.md).

For A5, also complete the public evidence checklist in
[A5_LAUNCH_EVIDENCE.md](A5_LAUNCH_EVIDENCE.md). Documentation alone does not
prove a live deployment.

## A2 deterministic benchmark

The 14-case offline suite covers sparse/conflicting/duplicate evidence, chronology, browser evidence, access controls, prompt injection, private URLs, provider failure, crash recovery, budgets, replay, and unsupported-claim withholding:

```powershell
cd backend
..\.venv\Scripts\python.exe -m evaluation.a2_benchmark --check
```

Use `--write` after an intentional fixture or evaluator change. The committed [A2 scorecard](evaluation/A2_SCORECARD.md) must meet every contract threshold. It measures orchestration and publication discipline; A3 remains responsible for broader semantic investigation quality.

## Remaining future test layers

B4 and later add deterministic Flink replay, specialized-store consistency, Kubernetes deployment, load, failure-injection, and restore tests with those features.
