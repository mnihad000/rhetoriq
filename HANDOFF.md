# RhetoriQ Handoff - B2/B3 Completion

## Current status

- **B2 is complete.** SearXNG broad discovery, canonical retrieval, Federal Register primary-source research, provenance receipts, provider policy, retries, pagination, deduplication, rate limiting, and explicit fallback diagnostics are implemented.
- **B3 is implemented with an immediate Kafka cutover.** Six primary topics and six DLQs, committed JSON Schemas, Apicurio compatibility, KRaft Compose, transactional outbox, consumer ledger, retries/DLQs, replay, Kafka-only investigation dispatch, health/lag metrics, ordered stage events, and stable completion events are present. There is no synchronous production fallback.
- **A1, A2, A3, and A4 remain complete by product decision.** A5 public launch evidence is still pending. B1 pgvector remains opt-in until a live investigation corpus is backfilled and compared before production enablement.
- Do not re-open A3 unless requested. The project owner explicitly accepted the remaining 30-case curated-real-source corpus as future quality work, not a blocker.
- The working tree was already dirty before A3. Preserve unrelated changes and do not reset/revert them.

## B2/B3 verification and host note

- Backend verification: 221 passing tests and 17 optional-runtime skips before the final documentation-only closeout; the focused B3 contract suite later passed 10/10.
- Frontend: 4 tests passed and the production build completed successfully.
- Compose configuration renders successfully with required local secrets supplied.
- Live Federal Register canary: two current primary-source records, valid receipts, one page, no warning.
- Docker Desktop cannot start its Linux engine until the workstation is restarted. Docker reports `HCS_E_HYPERV_NOT_INSTALLED`; `wsl --install --no-distribution` completed successfully, so restart Windows and rerun the Compose integration/canary sequence in `docs/TESTING.md`. If the error persists, enable firmware virtualization.
- `backend/test_redis_connection.py` no longer contains a credential and reads `REDIS_URL` only from the environment. The previously exposed external Redis credential must still be rotated at its provider; source-code removal cannot revoke it.

## A3 decisions and implementation

- A3 uses a versioned claim-evidence verifier (`a3-v1`), with semantic similarity, exact evidence spans/offsets, NLI entailment/contradiction/neutral decisions, source role, independence group, date confidence, reason codes, and final disposition.
- Local NLI is authoritative only alongside deterministic rules. Gemini/Groq may provide an optional second opinion for ambiguous pairs, but cannot bypass deterministic publication policy.
- Fail closed: missing semantic model, weak/missing span, invalid model response, or insufficient independent evidence yields `withheld`/`unresolved`, not an affirmative claim.
- Mixed evidence policy: show a **qualified competing claim** only when both sides are independently verified; it cannot become the headline or executive-summary conclusion.
- New runs first: `CLAIM_VERIFIER_ENABLED=true` enables A3. Existing reports remain readable and can be explicitly re-verified through the UI or `POST /api/investigations/{id}/verification`.
- The Investigation page now includes a claim-level audit panel and `Create A3 audit` / `Re-verify evidence` action.

## Local runtime notes

- The user has already activated `.venv`, installed `sentence-transformers==5.6.0`, and preloaded `cross-encoder/nli-deberta-v3-base`; their terminal printed `NLI model ready`.
- A3 is enabled only in that PowerShell session until exported through deployment configuration:

  ```powershell
  $env:CLAIM_VERIFIER_ENABLED="true"
  ```

- For A2 live web research, use the research Docker stack from repository root:

  ```powershell
  docker compose -f infra/research/docker-compose.yml up -d
  ```

- Start the backend from `backend/` with `python -m uvicorn main:app --reload`.

## Verification performed

- Backend suite: **188 passed, 10 skipped**.
- Frontend production build: passed.
- Public USGS live canary reached the source successfully. Before NLI weights were available, its claim was correctly withheld; rerun it if fresh model-path confirmation is useful:

  ```powershell
  cd backend
  python -m evaluation.a3_live_canary
  ```

- `python -m evaluation.a3_scorecard` intentionally fails until a real 30-case reviewed capture corpus is curated. This is explicitly deferred.

## A4 decisions and implementation

- The investigation page is now a balanced, progressive-disclosure workspace. The default `Report` view leads with the conclusion, confidence, key claims, limitations, and recommended human checks; `Evidence`, `Narrative`, and `Method & audit` hold the deeper material.
- Workspace view, selected source, and evidence filters are URL query state, so report views and source detail can be refreshed, shared, and navigated with browser history.
- Evidence view provides keyword, source-type, and supporting/counter stance filters using existing workspace data. Source details open in an accessible drawer with Escape-to-close, focus return, and labelled external links.
- Narrative groups the provenance trace and timeline. Method & audit preserves the A2 runtime console, gaps, provenance, diversity context, research history, agent debate, and the A3 span-level audit/re-verification interface.
- Page-level updates now subscribe to the existing SSE event stream and debounce workspace refreshes. Native stream reconnection is retained; 8-second polling is used only after stream errors.
- Recent Investigations now loads the existing maximum of 12 records and supports client-side text and status filtering. No backend API contracts changed.
- The quiet editorial light theme remains the visual direction. Focus-visible styles, semantic view tabs, status labels, reduced-motion support, responsive layouts, empty states, and partial-artifact states were added.

## A4 verification and deployment notes

- Frontend production build passes: `cd frontend; npm run build`.
- Vitest/Testing Library has been added; the recent-investigation search and status-filter flow passes with `cd frontend; npm test`.
- Browser end-to-end and visual regression coverage were intentionally deferred by product decision to begin deployment immediately.
- Local Vite development may set `VITE_API_BASE_URL`; the production frontend container uses `PUBLIC_API_BASE_URL` for the API origin and SSE endpoint.

## A5 deployment closeout

- The intended public topology is Railway `frontend` plus Railway `api`, private SearXNG, and managed Neon PostgreSQL. Do not add a Railway Postgres service or a browser-renderer service for the initial release.
- The frontend container reads `PUBLIC_API_BASE_URL` at startup and exposes it to the client runtime. `VITE_API_BASE_URL` remains the local Vite development variable.
- The API container runs committed PostgreSQL migrations before starting FastAPI. The Railway API service owns migration execution; do not run migrations concurrently from a second release process.
- Neon supplies `DATABASE_URL`; preserve the provider's TLS setting, normally `sslmode=require`. Keep the value in Railway secret/reference configuration.
- A5 remains **In Progress** until the public frontend/API URLs, health responses, a live SSE investigation, workspace reload, replay after redeploy, and CI evidence are recorded in [docs/A5_LAUNCH_EVIDENCE.md](docs/A5_LAUNCH_EVIDENCE.md). No public URLs were available when this handoff was updated.
- Rollback is an application release rollback followed by a forward database fix or Neon restore when required. The migration runner has no destructive down-migration path; take a Neon backup/branch before schema changes.
