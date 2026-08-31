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

The frontend currently uses its API helper's configured defaults and demo fallback. If deployment introduces public API configuration, document the exact variable and origin policy in the deployment change; no production environment variable is required for local use today.
