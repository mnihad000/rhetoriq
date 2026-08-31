# RhetoriQ Handoff - Start A4 Next

## Current status

- **A1, A2, and A3 are complete by product decision.** Start **A4 Frontend Completion** next.
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

## A4 recommended starting point

1. Inspect the existing investigation page, research console, live workspace types, and A2 SSE event flow.
2. Replace polling with the existing event-stream interface where safe, keeping graceful fallback/reconnect behavior.
3. Improve final-report navigation between claims, receipts, sources, timeline events, gaps, provenance, and the new A3 audit evidence.
4. Add source filtering, history/search, source-detail views, keyboard/focus management, contrast/reduced-motion behavior, component tests, and end-to-end dashboard-to-report coverage.
5. Keep API contracts backward-compatible and preserve the A3 audit UI.
