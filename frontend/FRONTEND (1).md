# RhetoriQ — Frontend

> This document covers everything about the frontend. Component architecture, routing, WebSocket integration, the Neo4j graph visualization, state management, and how to run and build the frontend. If you are working on anything in `frontend/`, start here.

---

## Overview

The frontend is a **React + TypeScript** single-page application built with Vite. It connects to the backend API via REST and WebSocket for live updates. The core feature is the spread graph — an interactive visualization of how a narrative traveled from its origin to mainstream adoption.

### Tech Stack

| Tool | Purpose |
|---|---|
| **React 18** | UI framework |
| **TypeScript** | Type safety |
| **Vite** | Build tool and dev server |
| **TailwindCSS** | Styling |
| **React Query** | Server state management and caching |
| **Zustand** | Client state management |
| **React Router v6** | Client-side routing |
| **Sigma.js** | Graph visualization (Neo4j spread map) |
| **Recharts** | Time-series charts |
| **shadcn/ui** | UI component library |

---

## Project Structure

```
frontend/
├── FRONTEND.md
├── index.html
├── vite.config.ts
├── tailwind.config.ts
├── tsconfig.json
├── package.json
├── .env.example
└── src/
    ├── main.tsx                  # React app entry point
    ├── App.tsx                   # Root component, router setup
    ├── types/
    │   ├── investigation.ts      # Investigation and report types
    │   ├── narrative.ts          # Narrative and anomaly types
    │   └── graph.ts              # Graph node and edge types
    ├── api/
    │   ├── client.ts             # Axios instance with auth headers
    │   ├── investigations.ts     # Investigation API calls
    │   ├── narratives.ts         # Narrative API calls
    │   ├── search.ts             # Search API calls
    │   └── graph.ts              # Graph API calls
    ├── hooks/
    │   ├── useInvestigations.ts  # React Query hooks for investigations
    │   ├── useNarratives.ts      # React Query hooks for narratives
    │   ├── useWebSocket.ts       # WebSocket connection hook
    │   └── useSearch.ts          # Search hook with debounce
    ├── store/
    │   └── liveStore.ts          # Zustand store for live feed state
    ├── pages/
    │   ├── Dashboard.tsx         # Main dashboard — live feed + trending
    │   ├── Investigation.tsx     # Single investigation report page
    │   ├── Search.tsx            # Phrase search page
    │   └── NotFound.tsx          # 404 page
    ├── components/
    │   ├── layout/
    │   │   ├── Sidebar.tsx       # Navigation sidebar
    │   │   ├── Header.tsx        # Top header with search bar
    │   │   └── Layout.tsx        # Root layout wrapper
    │   ├── dashboard/
    │   │   ├── LiveFeed.tsx      # Real-time narrative detection feed
    │   │   ├── TrendingCard.tsx  # Single trending narrative card
    │   │   └── TimelineChart.tsx # Recharts time-series of detections
    │   ├── investigation/
    │   │   ├── ReportViewer.tsx  # Renders GPT-4o markdown report
    │   │   ├── SpreadTimeline.tsx # Visual spread path timeline
    │   │   ├── AmplifierList.tsx  # Key amplifiers ranked list
    │   │   └── PatternBadge.tsx   # Grassroots/Top-down/etc badge
    │   ├── graph/
    │   │   ├── SpreadGraph.tsx   # Sigma.js graph visualization
    │   │   ├── GraphControls.tsx # Zoom, filter, reset controls
    │   │   └── NodeTooltip.tsx   # Hover tooltip for graph nodes
    │   └── shared/
    │       ├── SourceBadge.tsx   # Reddit/News/CSPAN source badge
    │       ├── PoliticalLean.tsx # Political lean indicator
    │       ├── LoadingSpinner.tsx
    │       └── ErrorBoundary.tsx
    └── utils/
        ├── dates.ts              # Date formatting helpers
        ├── markdown.ts           # Markdown rendering config
        └── graphLayout.ts        # Sigma.js layout helpers
```

---

## Running Locally

```bash
cd frontend
npm install
npm run dev
# Available at http://localhost:3000
```

### Environment Variables

```env
# .env.local
VITE_API_URL=http://localhost:8000/api/v1
VITE_WS_URL=ws://localhost:8000/ws
```

### Build for Production

```bash
npm run build
# Output in dist/
```

---

## Routing

```tsx
// App.tsx
import { BrowserRouter, Routes, Route } from "react-router-dom";
import Layout from "./components/layout/Layout";
import Dashboard from "./pages/Dashboard";
import Investigation from "./pages/Investigation";
import Search from "./pages/Search";
import NotFound from "./pages/NotFound";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="investigation/:id" element={<Investigation />} />
          <Route path="search" element={<Search />} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
```

| Route | Page | Description |
|---|---|---|
| `/` | Dashboard | Live feed, trending narratives, timeline chart |
| `/investigation/:id` | Investigation | Full report, spread graph, amplifier list |
| `/search` | Search | Full-text and semantic phrase search |

---

## Pages

### Dashboard

The main landing page. Three sections:

1. **Live Feed** — real-time stream of detected anomalies and investigations powered by WebSocket
2. **Trending** — top 10 narratives by spike magnitude in the last 24 hours
3. **Timeline Chart** — Recharts time-series showing detection frequency over the last 7 days

```tsx
// pages/Dashboard.tsx
import LiveFeed from "../components/dashboard/LiveFeed";
import TrendingCard from "../components/dashboard/TrendingCard";
import TimelineChart from "../components/dashboard/TimelineChart";
import { useNarrativeTimeline, useTrending } from "../hooks/useNarratives";

export default function Dashboard() {
  const { data: trending, isLoading } = useTrending();
  const { data: timeline } = useNarrativeTimeline({ daysBack: 7 });

  return (
    <div className="grid grid-cols-12 gap-6 p-6">

      {/* Live Feed — left column */}
      <div className="col-span-4">
        <h2 className="text-lg font-semibold mb-4">Live Feed</h2>
        <LiveFeed />
      </div>

      {/* Trending + Timeline — right columns */}
      <div className="col-span-8 flex flex-col gap-6">
        <TimelineChart data={timeline} />
        <div className="grid grid-cols-2 gap-4">
          {trending?.map((narrative) => (
            <TrendingCard key={narrative.anomaly_id} narrative={narrative} />
          ))}
        </div>
      </div>

    </div>
  );
}
```

---

### Investigation

Displays a full investigation report. Four sections:

1. **Report Viewer** — renders the GPT-4o markdown report
2. **Spread Timeline** — horizontal visual timeline of the spread path stages
3. **Spread Graph** — interactive Sigma.js graph visualization
4. **Amplifier List** — ranked list of key amplifier nodes

```tsx
// pages/Investigation.tsx
import { useParams } from "react-router-dom";
import { useInvestigation } from "../hooks/useInvestigations";
import ReportViewer from "../components/investigation/ReportViewer";
import SpreadTimeline from "../components/investigation/SpreadTimeline";
import SpreadGraph from "../components/graph/SpreadGraph";
import AmplifierList from "../components/investigation/AmplifierList";
import PatternBadge from "../components/investigation/PatternBadge";

export default function Investigation() {
  const { id } = useParams<{ id: string }>();
  const { data: investigation, isLoading } = useInvestigation(id!);

  if (isLoading) return <LoadingSpinner />;
  if (!investigation) return <NotFound />;

  return (
    <div className="p-6 max-w-7xl mx-auto">

      {/* Header */}
      <div className="flex items-center gap-4 mb-8">
        <h1 className="text-2xl font-bold">"{investigation.phrase}"</h1>
        <PatternBadge pattern={investigation.pattern_classification} />
      </div>

      {/* Two column layout */}
      <div className="grid grid-cols-12 gap-6">

        {/* Left: Report + Timeline */}
        <div className="col-span-5 flex flex-col gap-6">
          <SpreadTimeline spreadPath={investigation.spread_path} />
          <AmplifierList amplifiers={investigation.key_amplifiers} />
          <ReportViewer report={investigation.report} />
        </div>

        {/* Right: Graph */}
        <div className="col-span-7">
          <SpreadGraph investigationId={id!} />
        </div>

      </div>
    </div>
  );
}
```

---

## Key Components

### LiveFeed

Consumes WebSocket messages from the `useWebSocket` hook and renders a scrolling feed of live events.

```tsx
// components/dashboard/LiveFeed.tsx
import { useWebSocket } from "../../hooks/useWebSocket";
import { useLiveStore } from "../../store/liveStore";
import SourceBadge from "../shared/SourceBadge";

export default function LiveFeed() {
  const { events } = useLiveStore();

  return (
    <div className="flex flex-col gap-2 overflow-y-auto max-h-[600px]">
      {events.map((event) => (
        <div
          key={event.id}
          className="p-3 rounded-lg border border-gray-200 bg-white 
                     animate-slide-in-from-top"
        >
          {event.type === "anomaly_detected" && (
            <div className="flex items-center justify-between">
              <span className="font-medium text-sm">
                "{event.payload.phrase}"
              </span>
              <span className="text-xs text-orange-500 font-semibold">
                {event.payload.spike_magnitude.toFixed(1)}x spike
              </span>
            </div>
          )}
          {event.type === "investigation_complete" && (
            <div
              className="flex items-center gap-2 cursor-pointer hover:bg-gray-50"
              onClick={() => navigate(`/investigation/${event.payload.investigation_id}`)}
            >
              <span className="text-green-500 text-xs font-semibold">
                ✓ INVESTIGATED
              </span>
              <span className="font-medium text-sm">
                "{event.payload.phrase}"
              </span>
            </div>
          )}
          <span className="text-xs text-gray-400 mt-1">
            {formatRelativeTime(event.timestamp)}
          </span>
        </div>
      ))}
    </div>
  );
}
```

---

### SpreadGraph

The most complex component. Renders an interactive graph using Sigma.js showing how a narrative spread from source to source.

```tsx
// components/graph/SpreadGraph.tsx
import { useEffect, useRef } from "react";
import Sigma from "sigma";
import Graph from "graphology";
import { useGraphData } from "../../hooks/useGraphData";
import GraphControls from "./GraphControls";
import NodeTooltip from "./NodeTooltip";

// Node color by source type
const NODE_COLORS = {
  subreddit:  "#FF4500",  // Reddit orange
  outlet:     "#1A73E8",  // News blue
  politician: "#34A853",  // Political green
};

// Node size by amplification count
const getNodeSize = (amplificationCount: number) =>
  Math.max(5, Math.min(20, amplificationCount * 2));

export default function SpreadGraph({ investigationId }: { investigationId: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const sigmaRef = useRef<Sigma | null>(null);
  const { data: graphData, isLoading } = useGraphData(investigationId);

  useEffect(() => {
    if (!graphData || !containerRef.current) return;

    // Build graphology graph
    const graph = new Graph();

    graphData.nodes.forEach((node) => {
      graph.addNode(node.id, {
        label: node.label,
        size: getNodeSize(node.amplification_count ?? 1),
        color: NODE_COLORS[node.type] ?? "#999",
        x: Math.random(),  // Initial random position — layout algorithm takes over
        y: Math.random(),
        // Store full node data for tooltip
        nodeData: node,
      });
    });

    graphData.edges.forEach((edge) => {
      graph.addEdge(edge.source, edge.target, {
        weight: edge.weight,
        color: "#E0E0E0",
        size: Math.max(1, edge.weight * 0.5),
      });
    });

    // Initialize Sigma
    sigmaRef.current = new Sigma(graph, containerRef.current, {
      renderEdgeLabels: false,
      defaultEdgeColor: "#E0E0E0",
      labelFont: "Inter, sans-serif",
      labelSize: 12,
    });

    // Cleanup on unmount
    return () => {
      sigmaRef.current?.kill();
    };
  }, [graphData]);

  if (isLoading) return <LoadingSpinner />;

  return (
    <div className="relative w-full h-[600px] rounded-xl border border-gray-200 
                    bg-gray-50 overflow-hidden">
      <div ref={containerRef} className="w-full h-full" />
      <GraphControls sigmaRef={sigmaRef} />
      <NodeTooltip sigmaRef={sigmaRef} />

      {/* Legend */}
      <div className="absolute bottom-4 left-4 flex flex-col gap-1 
                      bg-white p-3 rounded-lg shadow-sm border border-gray-100">
        {Object.entries(NODE_COLORS).map(([type, color]) => (
          <div key={type} className="flex items-center gap-2 text-xs">
            <div
              className="w-3 h-3 rounded-full"
              style={{ backgroundColor: color }}
            />
            <span className="capitalize text-gray-600">{type}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
```

---

### ReportViewer

Renders the GPT-4o generated markdown report with syntax highlighting and clean typography.

```tsx
// components/investigation/ReportViewer.tsx
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export default function ReportViewer({ report }: { report: string }) {
  return (
    <div className="prose prose-sm max-w-none p-6 bg-white rounded-xl 
                    border border-gray-200">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h2: ({ children }) => (
            <h2 className="text-lg font-bold text-gray-900 mt-6 mb-3">
              {children}
            </h2>
          ),
          h3: ({ children }) => (
            <h3 className="text-sm font-semibold text-gray-700 mt-4 mb-2 
                           uppercase tracking-wide">
              {children}
            </h3>
          ),
          strong: ({ children }) => (
            <strong className="font-semibold text-gray-900">{children}</strong>
          ),
        }}
      >
        {report}
      </ReactMarkdown>
    </div>
  );
}
```

---

## Hooks

### useWebSocket

Manages the WebSocket connection and pushes incoming messages to the Zustand live store.

```tsx
// hooks/useWebSocket.ts
import { useEffect, useRef } from "react";
import { useLiveStore } from "../store/liveStore";

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const { addEvent } = useLiveStore();

  useEffect(() => {
    const token = localStorage.getItem("auth_token");
    const ws = new WebSocket(`${import.meta.env.VITE_WS_URL}?token=${token}`);
    wsRef.current = ws;

    ws.onmessage = (event) => {
      const message = JSON.parse(event.data);
      addEvent(message);
    };

    ws.onclose = () => {
      // Reconnect after 3 seconds on unexpected close
      setTimeout(() => {
        wsRef.current = new WebSocket(
          `${import.meta.env.VITE_WS_URL}?token=${token}`
        );
      }, 3000);
    };

    // Keepalive ping every 30 seconds
    const pingInterval = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "ping" }));
      }
    }, 30000);

    return () => {
      clearInterval(pingInterval);
      ws.close();
    };
  }, []);
}
```

---

### useInvestigations

React Query hooks for fetching investigation data.

```tsx
// hooks/useInvestigations.ts
import { useQuery, useInfiniteQuery } from "@tanstack/react-query";
import { fetchInvestigations, fetchInvestigation } from "../api/investigations";

export function useInvestigations(filters = {}) {
  return useInfiniteQuery({
    queryKey: ["investigations", filters],
    queryFn: ({ pageParam = 1 }) =>
      fetchInvestigations({ page: pageParam, ...filters }),
    getNextPageParam: (lastPage, pages) =>
      lastPage.results.length === 20 ? pages.length + 1 : undefined,
    staleTime: 5 * 60 * 1000,   // 5 minutes
  });
}

export function useInvestigation(id: string) {
  return useQuery({
    queryKey: ["investigation", id],
    queryFn: () => fetchInvestigation(id),
    staleTime: 60 * 60 * 1000,  // 1 hour — reports don't change
    enabled: !!id,
  });
}
```

---

## State Management

### Zustand Live Store

Only live feed data lives in Zustand — everything else is React Query server state.

```tsx
// store/liveStore.ts
import { create } from "zustand";

interface LiveEvent {
  id: string;
  type: "anomaly_detected" | "investigation_started" | "investigation_complete";
  payload: Record<string, unknown>;
  timestamp: string;
}

interface LiveStore {
  events: LiveEvent[];
  activeInvestigations: Set<string>;
  addEvent: (event: LiveEvent) => void;
  clearEvents: () => void;
}

export const useLiveStore = create<LiveStore>((set) => ({
  events: [],
  activeInvestigations: new Set(),

  addEvent: (event) =>
    set((state) => ({
      // Keep only the last 50 events in the feed
      events: [event, ...state.events].slice(0, 50),
      activeInvestigations:
        event.type === "investigation_started"
          ? new Set([...state.activeInvestigations, event.payload.investigation_id as string])
          : event.type === "investigation_complete"
          ? new Set(
              [...state.activeInvestigations].filter(
                (id) => id !== event.payload.investigation_id
              )
            )
          : state.activeInvestigations,
    })),

  clearEvents: () => set({ events: [], activeInvestigations: new Set() }),
}));
```

---

## TypeScript Types

```tsx
// types/investigation.ts

export interface SpreadStage {
  stage: number;
  source: string;
  outlet_or_subreddit: string;
  published_at: string;
  document_count: number;
}

export interface KeyAmplifier {
  source_id: string;
  name: string;
  type: "subreddit" | "outlet" | "politician";
  amplification_count: number;
}

export type PatternClassification =
  | "grassroots"
  | "top-down"
  | "astroturfed"
  | "reactive";

export interface Investigation {
  investigation_id: string;
  anomaly_id: string;
  phrase: string;
  started_at: string;
  completed_at: string;
  duration_seconds: number;
  origin: {
    document_id: string;
    source: string;
    outlet_or_subreddit: string;
    published_at: string;
    url: string;
    confidence: number;
  };
  spread_path: SpreadStage[];
  key_amplifiers: KeyAmplifier[];
  pattern_classification: PatternClassification;
  report: string;
  similar_document_count: number;
  graph_node_count: number;
}

// types/graph.ts

export interface GraphNode {
  id: string;
  label: string;
  type: "subreddit" | "outlet" | "politician";
  political_lean?: string;
  first_published_at: string;
  amplification_count?: number;
}

export interface GraphEdge {
  source: string;
  target: string;
  weight: number;
  first_amplified_at: string;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}
```

---

## API Client

```tsx
// api/client.ts
import axios from "axios";

const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_URL,
  timeout: 10000,
});

// Attach JWT token to every request
apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem("auth_token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Handle 401 globally
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem("auth_token");
      window.location.href = "/login";
    }
    return Promise.reject(error);
  }
);

export default apiClient;
```

---

## Package Dependencies

```json
{
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "react-router-dom": "^6.22.0",
    "@tanstack/react-query": "^5.28.0",
    "zustand": "^4.5.0",
    "axios": "^1.6.0",
    "sigma": "^3.0.0",
    "graphology": "^0.25.0",
    "graphology-layout-forceatlas2": "^0.10.0",
    "recharts": "^2.12.0",
    "react-markdown": "^9.0.0",
    "remark-gfm": "^4.0.0",
    "tailwindcss": "^3.4.0",
    "clsx": "^2.1.0",
    "date-fns": "^3.6.0"
  },
  "devDependencies": {
    "typescript": "^5.4.0",
    "vite": "^5.2.0",
    "@vitejs/plugin-react": "^4.2.0",
    "@types/react": "^18.2.0",
    "vitest": "^1.4.0",
    "@testing-library/react": "^15.0.0"
  }
}
```
