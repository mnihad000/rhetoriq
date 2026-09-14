# RhetoriQ Frontend

The frontend is a React 19, TypeScript, Vite, and Tailwind CSS application in `frontend/`. It presents the public landing experience, dashboard, recent investigations, and an inspectable investigation workspace.

## Run locally

```powershell
cd frontend
npm install
npm run dev
```

Vite prints the local URL, normally `http://127.0.0.1:5173`. Build the production bundle with:

```powershell
npm run build
```

## Routes

| Route | Purpose |
|---|---|
| `/` | Landing page and product overview. |
| `/dashboard` | Trending narratives, investigation entry point, and recent workspaces. |
| `/investigation/:id` | Persisted or live investigation workspace. |

The application uses `react-router-dom` and communicates with the FastAPI API through `src/lib/api.ts`. It can show bundled demo content when live API data is unavailable.

## Current user experience

- Landing and dashboard pages with responsive layouts.
- Trending narrative cards and persisted recent-investigation list.
- Investigation creation with SSE research progress and polling fallback.
- A lazily loaded React Flow research console showing the active graph path, action trail, budgets, evidence gate, terminal explanation, and recorded replay control.
- Completed workspace views for reports, claim/evidence receipts, gaps, timelines, provenance, narrative families, source diversity, and agent-debate artifacts.
- Loading, running, unavailable, and completed states.

## Current boundaries

The frontend does not currently use React Query, Zustand, Sigma.js, WebSockets, or a component-test runner. The A2 console uses native `EventSource` with resumable persisted events and polling fallback; broader navigation, filtering, accessibility, and end-to-end work remains A4.

## Environment

Local Vite development uses `VITE_API_BASE_URL` when set and otherwise uses
the API helper's local default and demo fallback. The production Nginx image
reads `PUBLIC_API_BASE_URL` at container startup from `runtime-config.js`; set
it to the public Railway API origin, including `https://` and no trailing path.
The API's `CORS_ALLOW_ORIGINS` must contain the exact public frontend origin.
Changing the API domain requires updating the Railway frontend variable and
restarting/redeploying the frontend container, not rebuilding the application
bundle.

The initial public deployment has no browser-renderer service. A source that
requires JavaScript rendering is shown as a retrieval limitation when the
backend cannot use the local-only browser adapter.
