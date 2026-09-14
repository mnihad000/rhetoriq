# A5 Launch Evidence

A5 is complete only when the public deployment satisfies the checklist below
and the evidence is recorded. This file is a small operational record, not a
claim that a deployment is live. Replace each placeholder with the value from
the same deployment and commit the completed record with the release notes.

## Release identity

| Field | Value |
|---|---|
| Railway frontend URL | `<pending public frontend URL>` |
| Railway API URL | `<pending public API URL>` |
| Neon project/branch | `<pending Neon project and branch>` |
| Deployed commit | `<pending commit SHA>` |
| Verification timestamp (UTC) | `<pending timestamp>` |
| Verifier | `<pending operator>` |

No public URLs or live deployment results were available when this checklist
was created.

## Topology evidence

- [ ] Railway contains public `frontend` and `api` services.
- [ ] Neon is the managed PostgreSQL database configured through the API's
  secret `DATABASE_URL`; the connection string retains TLS, normally
  `sslmode=require`.
- [ ] SearXNG is private and reachable by the API through its Railway private
  address.
- [ ] No Railway Postgres service exists for this release.
- [ ] No browser-renderer service is deployed; `BROWSER_RENDERING_ENABLED` is
  `false`.
- [ ] The API service owns startup migration execution and no second migration
  process is configured.

Record the Railway service names and private SearXNG address here:

```text
frontend: <pending>
api: <pending>
searxng private address: <pending>
```

## Public health checks

Run these against the same API URL and record the HTTP status plus a sanitized
response excerpt. Do not commit secrets, tokens, full database URLs, or
document contents.

```powershell
$api = "https://<api-public-domain>"
Invoke-WebRequest "$api/health" -UseBasicParsing
Invoke-WebRequest "$api/api/research/health" -UseBasicParsing
```

- [ ] `GET /health` returns HTTP 200 and reports `demo_mode: false`.
- [ ] `GET /api/research/health` returns HTTP 200 with SearXNG,
  checkpointer, and embedded worker ready. The checkpointer probe confirms
  connectivity to the configured Neon database.
- [ ] The research health response confirms browser rendering is unavailable by
  configuration, rather than depending on a deployed browser service.

Evidence:

```text
/health status and sanitized excerpt: <pending>
/api/research/health status and sanitized excerpt: <pending>
```

## Live investigation, reload, and replay

- [ ] From the public frontend, start a short investigation with a question
  that can be checked against public sources.
- [ ] Record the investigation ID and run ID.
- [ ] Observe the SSE progress rail advance through research events and reach a
  terminal state without manual database repair.
- [ ] Refresh or reopen the workspace and confirm the report, evidence trail,
  event history, and run identifiers persist.
- [ ] Redeploy the API from the recorded commit (or perform the release restart
  procedure), reload the same workspace, and confirm its data remains present.
- [ ] Trigger replay for the recorded run and confirm a replay comparison or a
  documented terminal limitation is persisted and visible.

```text
investigation ID: <pending>
run ID: <pending>
terminal status: <pending>
SSE observed at: <pending>
reload verified at: <pending>
redeploy/reload verified at: <pending>
replay result: <pending>
```

## CI and release checks

- [ ] GitHub CI is green for the deployed commit.
- [ ] Backend tests pass, excluding only documented optional skips.
- [ ] Python compilation passes.
- [ ] PostgreSQL migration integration coverage passes against the CI
  PostgreSQL service.
- [ ] Frontend production build and frontend tests pass.
- [ ] CI's Markdown conflict-marker check passes; local encoding and
  relative-link checks pass.
- [ ] Railway is configured to wait for the required GitHub checks before
  deployment.

CI run URL and result:

```text
<pending GitHub Actions run URL and summary>
```

## Rollback record

Before a schema-changing release, create a Neon backup or branch and record
its identifier. The migration runner is forward-only: application rollback can
reuse a backward-compatible schema, while an incompatible schema requires a
tested forward fix or the documented Neon restore decision.

```text
pre-release Neon backup/branch: <pending>
last known-good commit: <pending>
rollback exercise/result: <pending>
post-rollback health and reload checks: <pending>
```
