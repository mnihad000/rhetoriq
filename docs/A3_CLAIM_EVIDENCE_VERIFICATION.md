# A3 Claim–Evidence Verification

The A3 verifier is a versioned, fail-closed artifact. Enable it only for new investigations with `CLAIM_VERIFIER_ENABLED=true`; existing reports are not changed. A historical report can be explicitly re-verified through `POST /api/investigations/{investigation_id}/verification`.

Each claim/evidence decision records an exact persisted evidence span, semantic similarity, local NLI decision, optional hosted second opinion, source role, independence group, date confidence, reason codes, and final disposition. Hosted Gemini/Groq output is optional and never overrides deterministic source, span, and disposition policy.

The local runtime requires the declared `sentence-transformers` package and the pinned `CLAIM_VERIFIER_NLI_MODEL` weights. Set `CLAIM_VERIFIER_LOCAL_ONLY=true` in an offline deployment after preloading those weights; without a usable local model, A3 fails closed and withholds affirmative claims.

The release scorecard intentionally starts empty. It refuses to run until curators add exactly 30 real, public-source captures: five each for origin uncertainty, direct conflict, syndicated reporting, sparse evidence, unavailable sources, and misleading chronology. Every capture must include a content hash, capture date, public-access confirmation, and reviewer provenance. Do not substitute generated or synthetic documents for these captures.

Run the scorecard after the corpus is curated:

```powershell
cd backend
..\.venv\Scripts\python.exe -m evaluation.a3_scorecard
```

The scorecard is designed to gate release quality at 95% affirmed-claim precision, 0% unsupported/contradicted leakage, 100% citation-span validity, 0% duplicate-as-independent errors, and exact expected disposition outcomes.

Live canaries are non-blocking and use the same verifier on approved public pages. They surface reachability, final URL, content hash, latency, verifier disposition, and semantic-model provenance; schedule them separately from the offline release scorecard:

```powershell
cd backend
..\.venv\Scripts\python.exe -m evaluation.a3_live_canary
```
