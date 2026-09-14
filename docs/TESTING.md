# RhetoriQ Testing

The current MVP verification baseline is backend tests, Python compilation, the
frontend production build, and the PostgreSQL migration check used by CI.
Kafka, Flink, Elasticsearch, Neo4j, container integration, and browser
end-to-end tests are not local prerequisites. Browser rendering is not part of
the initial public deployment.

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

## Future test layers

As Track B is implemented, add tests with the feature rather than documenting them as existing coverage: migration tests for PostgreSQL, fixture tests for source connectors, contract/replay tests for Kafka and Flink, and integration tests for specialized stores.
