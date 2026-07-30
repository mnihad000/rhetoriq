# RhetoriQ Testing

The current MVP verification baseline is backend tests, Python compilation, and the frontend production build. Kafka, Flink, PostgreSQL, Elasticsearch, Neo4j, container integration, and browser end-to-end tests are planned and are not local prerequisites.

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

## Frontend production build

```powershell
cd frontend
npm run build
```

## Documentation checks

Before merging documentation changes, verify that project-authored Markdown has no conflict markers or mojibake and that all relative Markdown links resolve within the repository. The check may be run with the PowerShell command in the A1 completion record in [ROADMAP.md](ROADMAP.md).

## Future test layers

As Track B is implemented, add tests with the feature rather than documenting them as existing coverage: migration tests for PostgreSQL, fixture tests for source connectors, contract/replay tests for Kafka and Flink, and integration tests for specialized stores.
