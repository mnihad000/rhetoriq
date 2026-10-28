# RhetoriQ Testing and Acceptance

Routine tests establish code behavior. Runtime qualification separately proves
that real databases, models, providers, restart recovery, and platform
deployment work together. Do not promote offline passes or Compose rendering
into a public-deployment claim.

On `deployment`, Kubernetes, Helm, EKS, Terraform, ECR, Kafka, Apicurio, and
Flink checks are retired. The deployment contract tests live in
`backend/tests/test_deployment_contracts.py`.

## Routine checks

From the repository root:

```powershell
pytest backend/tests -q
python -m compileall -q backend infra/b5
cd frontend
npm test -- --run
npm run build
```

Optional integration tests may skip when their explicitly named disposable
dependency is unavailable. A skip must never connect to a developer database
or be reported as runtime evidence.

## PostgreSQL and Compose

PostgreSQL integration uses only an explicit `POSTGRES_TEST_DATABASE_URL`
whose database is disposable and contains `test` in its name. Never use a
production database or the ordinary development database.

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest tests/test_postgres_migration.py tests/test_b5_postgres.py -q
```

Validate the base and optional B5 Compose graphs without loading developer
credentials:

```powershell
docker compose --env-file .env.production.example -f compose.yml config --quiet
docker compose --env-file .env.production.example -f compose.yml -f infra/b5/compose.b5.yml --profile b5 config --quiet
```

Compose rendering proves configuration shape only. It does not prove images
build, services start, encrypted connections work, or jobs are processed.

## Database-job acceptance

Use a fresh disposable database and verify:

1. An API request commits its accepted job and domain plan atomically.
2. A worker claims one pending job, renews its lease, and records completion.
3. A crash or lease expiry makes unfinished work claimable again.
4. Retryable failures use bounded attempts and backoff; permanent failures stay
   visible without losing the original request.
5. A stale worker cannot commit after lease generation changes.
6. Direct document processing preserves receipts, canonical identity,
   revisions, withdrawals, and evidence eligibility.
7. Duplicate delivery produces one logical document, report, and terminal
   outcome.
8. Checkpoint recovery, SSE reconnect, and browser reload preserve progress.

## Research and product acceptance

The A2 deterministic benchmark covers sparse, conflicting, syndicated, and
unsupported evidence; chronology; prompt injection; private URLs; provider
failure; crash recovery; budgets; replay; and publication withholding.

```powershell
cd backend
..\.venv\Scripts\python.exe -m evaluation.a2_benchmark --check
```

A3 remains a reviewed-corpus quality gate. Its live canary is non-blocking and
records reachability, final URL, hash, latency, disposition, and model
provenance without secrets.

## Optional B5 qualification

B5 acceptance requires explicit disposable opt-in, fresh volumes, disposable
passwords, a test database, and pinned MiniLM inference. Qualify Elasticsearch,
Neo4j, Redis, TLS, projection freshness, cache fallthrough, repair, rebuild,
and rollback separately. B5 stores are derived and their availability cannot
override canonical PostgreSQL evidence eligibility.

## Documentation checks

Before merging documentation changes:

- resolve every relative Markdown link and anchor;
- search for references to removed or renamed files;
- reject unresolved merge-conflict markers and malformed replacement chars;
- distinguish current, implemented-but-unqualified, deployed, and planned;
- inspect examples for credentials, connection strings, tokens, or private data.

Documentation can describe required evidence, but it cannot substitute for a
recorded runtime result.
