# RhetoriQ Handoff - Start A5 Next

## Current status

- **A1, A2, A3, and A4 are complete by product decision.** Start **A5 Deployable MVP** next.
- Do not re-open A3 unless requested. The project owner explicitly accepted the remaining 30-case curated-real-source corpus as future quality work, not a blocker.
- The working tree was already dirty before A3. Preserve unrelated changes and do not reset/revert them.

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
- The production frontend must set `VITE_API_BASE_URL` to the public backend origin. The SSE endpoint uses that same base URL.
