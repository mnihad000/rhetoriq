# Codebase optimization and complexity review

Reviewed: 2026-09-17  
Status: Findings 1, 2, and 7 have been implemented with targeted and regression checks; real PostgreSQL qualification for Findings 1 and 2 is pending CI. Other findings remain open.

Implementation details: [Research event streams](RESEARCH_STREAMS.md).

## Scope and evidence

This review inspected backend database access, live research updates, frontend loading, API exports, data transformations, and decorative animations. It is a targeted source review, not an exhaustive audit or production performance profile.

The repository contained substantial uncommitted changes at review time. Findings describe that working tree; existing changes were preserved.

The frontend production build (`npm run build`) passed. Its main JavaScript bundle measured **691.39 KB minified and 212.42 KB gzipped**, and Vite reported a chunk-size warning. Backend runtime behavior and production query latency were not measured.

Query counts below are derived from source paths, not database telemetry. Impact depends on traffic, data volume, database latency, and cache configuration.

## Priorities

| Priority | Finding | Main impact |
| --- | --- | --- |
| High | Excessive synchronous database work in live event streams | Event-loop blocking and repeated database reads |
| High | Workspace and recent-investigation query multiplication | Latency, connection churn, and repeated JSON parsing |
| High | Audit console event truncation and terminal stream handling | Missing later events and unnecessary reconnects |
| Medium | Unbounded signal-history retrieval | Work grows with revision history |
| Medium | Eager frontend page loading | Larger initial JavaScript download and execution |
| Low | Unused frontend API exports | Maintenance overhead |
| Low | Repeated investigation experience construction | Unnecessary transformations during rendering |
| Medium | Continuous decorative animation work | CPU/GPU use and reduced-motion accessibility |

## 1. Live updates perform excessive database work and block the async event loop

Relevant code:

- [Research event stream](../backend/api/research.py): `research_events` and its inner `stream` generator.
- [Research repository](../backend/services/research_repository.py): `get_trail`, `get_latest_run`, `get_run`, and `_row_to_run`.
- [Database adapter](../backend/services/database.py): `PostgresConnection`.

The async stream synchronously calls `get_trail` every second. When a run exists, the current trail path performs roughly **14 SQL queries across 8 connections** per tick. It loads the run summary twice, and each summary reads and validates every research document to calculate source counts. It also loads actions, evaluation, and replay comparison even though the stream primarily needs events and terminal status.

These synchronous operations execute on the async event loop. Slow database calls can delay other async work. PostgreSQL connections are created directly through `psycopg.connect` rather than borrowed from an application connection pool.

Suggested improvement:

- Give SSE a lightweight query path for new events and run status.
- Avoid rebuilding full summaries and loading unrelated artifacts on every tick.
- Move synchronous database calls off the async event loop, or adopt asynchronous database access for this path.
- Pool PostgreSQL connections and reuse a connection within related reads.
- Maintain source counts or query compact source metadata instead of repeatedly parsing document JSON.

Validate with existing stream tests, terminal-state behavior, concurrent connections, and measurements of queries per tick and event-loop responsiveness.

## 2. Workspace and dashboard loading multiply queries — Completed

**Ticket completed:** Workspace loading now reads every artifact through one joined query and retrieves documents with one additional query on the same consistent snapshot. Recent-investigation cards use a compact persisted summary projection maintained in the artifact write transactions.

Relevant code:

- [Investigation repository](../backend/services/investigation_repository.py): `get_investigation_workspace` and `get_recent_investigations`.
- [Workspace endpoint](../backend/api/narratives.py): `get_investigation_workspace`.

Previously, workspace construction loaded individual artifacts through separate repository calls. Recent-investigation loading performed one list query followed by five artifact queries per result: retrieval, report, analyst, timeline, and receipts. Returning 12 summaries therefore required **61 SQL queries** in that repository path.

The optimized repository path now uses one connection and two SELECTs for an uncached workspace, and one lightweight SELECT for up to 12 recent summaries. The recent query no longer transfers or parses retrieval, report, analyst, timeline, or receipts JSON.

Implemented solution:

- Batches workspace artifact reads through LEFT JOINs while preserving optional artifacts.
- Reuses one connection with a repeatable read snapshot for workspace metadata, artifacts, and documents.
- Persists compact title, summary, source-count, and receipt-count projections transactionally.
- Backfills legacy SQLite and PostgreSQL data idempotently without rewriting authoritative artifacts.

SQLite query-count and compatibility tests pass. PostgreSQL migration and response-parity coverage runs when POSTGRES_TEST_DATABASE_URL is available in CI.

## 3. Trending reads the entire signal history before applying its limit

Relevant code:

- [Signal repository](../backend/services/signal_repository.py): `latest_signals`.
- [Trending service](../backend/services/trending_service.py): callers of `latest_signals`.

`latest_signals` selects every signal revision, orders them, deduplicates them in Python, and only then applies the requested limit. It issues three additional queries per selected signal for metrics, supporting documents, and limitations.

The result may be small, but the initial database transfer and Python work grow with the entire revision history.

Suggested improvement:

- Select the latest revision per signal in SQL, preserving the intended ordering.
- Apply the result limit in SQL after selecting current revisions.
- Batch related records for the selected signal/revision pairs.
- Confirm index suitability using representative query plans.

Validate latest-revision selection, ordering, revision ties, result limits, and query behavior with a large history.

## 4. The audit console misses events after the first 500

Relevant code:

- [Research console](../frontend/src/components/research-console/ResearchConsole.tsx): refresh and EventSource effect.
- [API client](../frontend/src/lib/api.ts): `getResearchTrail`.
- [Research endpoints](../backend/api/research.py): trail pagination and event stream termination.

The console repeatedly calls `getResearchTrail` with the default `afterSequence` of zero and a limit of 500. A run with more than 500 events keeps returning the same first page, leaving later events absent from the console's event history.

The console also opens an EventSource without stopping it when the run reaches a terminal state. The server ends completed streams, while the client can reconnect and activate fallback polling.

Suggested improvement:

- Track the last received sequence and fetch subsequent pages.
- Append and deduplicate events rather than replacing history with the first page.
- Reset the cursor and history when the active run changes, including replay.
- Close the stream and clear fallback timers when the run becomes terminal.

Validate runs exceeding 500 events, reconnects, replay transitions, duplicate delivery, and cleanup after completion or failure.

## 5. Every frontend page loads upfront

Relevant code:

- [Routes](../frontend/src/app/routes.tsx).
- [Investigation page](../frontend/src/pages/InvestigationPage.tsx).
- [Mock investigation data](../frontend/src/lib/mockInvestigation.ts).
- [Narrative graph](../frontend/src/components/investigation-workspace/B5NarrativeGraph.tsx).

The route module eagerly imports the landing page, dashboard, and investigation page. The investigation page imports mock investigation data, and its workspace reaches graph dependencies. Consequently, visiting the landing page loads code for other pages as part of the initial bundle.

The production build measured **691.39 KB of main JavaScript, 212.42 KB gzipped**. The research console already uses lazy loading; other heavy views can follow that approach.

Suggested improvement:

- Lazy-load route components with appropriate loading states.
- Load heavy graph views when needed.
- Dynamically import mock investigation data only for mock requests.
- Rebuild and compare initial bundle size and navigation behavior.

## 6. Unused frontend API exports add maintenance overhead

Relevant code: [API client](../frontend/src/lib/api.ts).

**Status: resolved.** The unused frontend client exports below were removed after a full-project caller audit. The backend endpoints remain available for external consumers. `npm run build` and `npm test` passed after the change (25 frontend tests).

The following exports had no callers in the reviewed frontend source:

- `runRetrieval`
- `runTimeline`
- `runSourceDiversity`
- `runCounterNarratives`
- `runNarrativeFamily`
- `runAnalyst`
- `runClaimCounterpoints`
- `runReceipts`
- `runAgentDebate`
- `runReport`
- `getTrendingStatus`
- `getEvidenceSearch` (alias of `searchInvestigationEvidence`)

These exports expand the apparent client API without supporting current application flows. Bundling may already eliminate unused exports, so this is primarily a maintenance finding rather than a demonstrated bundle-size problem.

Suggested improvement: remove unused client exports unless a concrete consumer requires them. Their lack of frontend callers does not establish that the corresponding backend endpoints are unused externally.

## 7. Investigation display data is constructed repeatedly — Completed

**Ticket completed:** The display adapter is now split into lightweight header and flowchart builders. The page checks `workspace` directly, and the narrative fallback graph is memoized per workspace update. Frontend adapter tests cover the header and graph fallback behavior.

Relevant code:

- [Investigation page](../frontend/src/pages/InvestigationPage.tsx).
- [Workspace display adapter](../frontend/src/lib/liveInvestigation.ts): `buildInvestigationHeaderFromWorkspace` and `buildInvestigationFlowchartData`.
- [Investigation workspace](../frontend/src/components/investigation-workspace/InvestigationWorkspace.tsx): narrative trace fallback construction.

Previously, the page built the full investigation experience just to check whether it existed, and both the header and narrative fallback repeated that work. This has been resolved by deriving header fields and graph data independently.

Implemented solution:

- Checks `workspace` directly when only existence matters.
- Separates lightweight header derivation from flowchart construction.
- Memoizes fallback graph data only within the narrative view, per workspace update.
- Covers header values and graph fallback data with dedicated frontend adapter tests.

## 8. Decorative animations do continuous work

Relevant code:

- [Wave background](../frontend/src/components/ui/wave-background.tsx).
- [Landing-page shader](../frontend/src/components/ui/atc-shader.tsx).
- [Reduced-motion CSS](../frontend/src/index.css).

The wave background uses an 8-pixel grid, recalculates point motion, and rewrites SVG path strings every animation frame. The landing-page shader also continuously renders through `requestAnimationFrame`.

Neither JavaScript animation loop responds to reduced-motion preferences. The existing CSS reduced-motion rule shortens CSS animations and transitions but does not stop these loops. The components also have no explicit visibility-based suspension.

Suggested improvement:

- Provide a static background when reduced motion is requested.
- Reduce wave density or cap the animation frame rate.
- Suspend unnecessary rendering when the page or animated region is not visible.
- Assess CPU/GPU cost on mobile and lower-powered devices before choosing visual quality defaults.

Validate reduced-motion behavior, resize handling, cleanup, and visual appearance. Measure animation cost separately from application rendering.

## Complexity assessment

Source inspection cannot establish whether code was written by AI. The concrete bloat candidates are unused exports, redundant transformations, repeated loading/parsing of large artifacts, and unnecessary continuous work.

Evidence gates, receipts, and replay mechanisms serve identifiable product requirements. Their complexity alone is not a reason to remove them. Changes should preserve citation integrity, provenance, publication decisions, and auditability.

## Suggested implementation order

1. Make the SSE database path lightweight and remove synchronous event-loop blocking.
2. Reduce workspace and dashboard query multiplication; add connection reuse/pooling.
3. Fix audit-console pagination and terminal stream cleanup.
4. Bound signal-history reads and batch related signal records.
5. Split frontend routes, graph views, and mock data.
6. Remove unused API exports and redundant display transformations.
7. Add reduced-motion and visibility handling to decorative animations.

For each change, run the relevant existing checks and compare the specific behavior or cost being improved. No production savings or latency improvement has been measured yet.
