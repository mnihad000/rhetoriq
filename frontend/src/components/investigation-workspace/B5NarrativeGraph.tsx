import { useEffect, useMemo, useRef, useState } from "react";
import { Background, Controls, ReactFlow, type Edge, type Node } from "@xyflow/react";
import { getInvestigationGraph, getProvenancePaths } from "../../lib/api";
import type { B5GraphEdge, B5GraphNode, B5GraphResponse, B5ProvenancePathsResponse, InvestigationFlowchartData } from "../../types/rhetoriq";
import InvestigationFlowchart from "../investigation-flowchart/InvestigationFlowchart";

type B5NarrativeGraphProps = {
  investigationId: string;
  fallbackData?: InvestigationFlowchartData;
  timeline?: Array<{ id: string; document_id: string; title: string; timestamp: string; explanation: string }>;
  onOpenSource: (documentId: string) => void;
  onOpenAudit?: () => void;
};

export default function B5NarrativeGraph({
  investigationId,
  fallbackData,
  timeline = [],
  onOpenSource,
  onOpenAudit,
}: B5NarrativeGraphProps) {
  const [includeInferred, setIncludeInferred] = useState(true);
  const [includePathInferences, setIncludePathInferences] = useState(false);
  const [edgeFilter, setEdgeFilter] = useState<"all" | "observed" | "inferred" | "contextual">("all");
  const [edgeLimit, setEdgeLimit] = useState(50);
  const [graph, setGraph] = useState<B5GraphResponse | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<B5GraphEdge | null>(null);
  const [fromDocumentId, setFromDocumentId] = useState("");
  const [toDocumentId, setToDocumentId] = useState("");
  const [paths, setPaths] = useState<B5ProvenancePathsResponse | null>(null);
  const [pathLoading, setPathLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const pathRequestRef = useRef(0);
  const pathControllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    setLoading(true);
    setError(null);
    void getInvestigationGraph(investigationId, { includeInferred, signal: controller.signal })
      .then((response) => {
        if (!current) return;
        setGraph(normalizeGraphResponse(response));
        setSelectedEdge(null);
      })
      .catch((reason: unknown) => {
        if (!current || (reason instanceof DOMException && reason.name === "AbortError")) return;
        setError("The live narrative graph is unavailable; showing the stored timeline view.");
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
      controller.abort();
    };
  }, [investigationId, includeInferred]);

  useEffect(() => () => pathControllerRef.current?.abort(), []);

  const documentNodes = useMemo(() => (graph?.nodes ?? []).filter((node) => node.document_id), [graph]);
  const visibleEdges = useMemo(
    () =>
      (graph?.edges ?? []).filter(
        (edge) =>
          (includeInferred || edge.evidence_class !== "inferred") &&
          (edgeFilter === "all" || edge.evidence_class === edgeFilter),
      ),
    [graph, includeInferred, edgeFilter],
  );
  const flowNodes = useMemo<Node[]>(
    () =>
      (graph?.nodes ?? []).map((node, index) => ({
        id: node.id,
        position: { x: (index % 3) * 260, y: Math.floor(index / 3) * 145 },
        data: { label: node.label, kind: node.kind },
        type: "default",
      })),
    [graph],
  );
  const flowEdges = useMemo<Edge[]>(
    () =>
      visibleEdges.map((edge) => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        label: edge.relationship,
        animated: false,
        data: edge,
      })),
    [visibleEdges],
  );

  useEffect(() => setEdgeLimit(50), [graph, includeInferred, edgeFilter]);

  async function loadPath() {
    if (!fromDocumentId || !toDocumentId || fromDocumentId === toDocumentId) return;
    pathControllerRef.current?.abort();
    const controller = new AbortController();
    pathControllerRef.current = controller;
    const requestId = ++pathRequestRef.current;
    setPathLoading(true);
    try {
      const response = await getProvenancePaths(investigationId, {
        fromDocumentId,
        toDocumentId,
        maxDepth: 4,
        includeInferred: includePathInferences,
        signal: controller.signal,
      });
      if (requestId === pathRequestRef.current) setPaths(response);
    } catch {
      if (requestId === pathRequestRef.current) setPaths(null);
    } finally {
      if (requestId === pathRequestRef.current) setPathLoading(false);
    }
  }

  if (!loading && (!graph || !graph.nodes.length) && fallbackData) {
    return (
      <section className="space-y-4">
        <div role="status" className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950">
          {error ?? "The live graph has no nodes yet."}
        </div>
        <InvestigationFlowchart data={fallbackData} />
      </section>
    );
  }

  return (
    <section aria-labelledby="b5-narrative-graph-title" className="space-y-4">
      <div className="workspace-panel p-5 sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="eyebrow">Narrative graph</p>
            <h3 id="b5-narrative-graph-title" className="mt-2 text-xl font-semibold text-[var(--ink)]">
              Observed relationships in this investigation
            </h3>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-[var(--muted)]">
              Edges describe recorded evidence and method metadata. They do not establish a definitive origin or explain activity outside this dataset.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3 text-sm text-[var(--muted)]">
            <label className="inline-flex items-center gap-2">
              <input type="checkbox" checked={includeInferred} onChange={(event) => setIncludeInferred(event.target.checked)} />
              Show inferred edges
            </label>
            <label>
              Evidence class
              <select value={edgeFilter} onChange={(event) => setEdgeFilter(event.target.value as typeof edgeFilter)} className="ml-2 rounded-lg border border-[var(--border)] bg-white px-2 py-1.5 text-sm text-[var(--ink)]">
                <option value="all">All</option>
                <option value="observed">Observed</option>
                <option value="inferred">Inferred</option>
                <option value="contextual">Contextual</option>
              </select>
            </label>
          </div>
        </div>
        {graph?.fallback_active ? <p role="status" className="mt-4 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-950">Graph is using the deterministic fallback.</p> : null}
        {graph?.pending ? <p role="status" className="mt-4 rounded-xl border border-sky-200 bg-sky-50 px-3 py-2 text-sm text-sky-950">Graph evidence is still being assembled.</p> : null}
        {error ? <p role="alert" className="mt-4 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-950">{error}</p> : null}
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="workspace-panel overflow-hidden">
          <div className="h-[520px]" aria-label="Narrative relationship graph">
            <ReactFlow
              nodes={flowNodes}
              edges={flowEdges}
              fitView
              minZoom={0.2}
              maxZoom={1.5}
              nodesConnectable={false}
              nodesDraggable={false}
              onNodeClick={(_event, node) => {
                const source = graph?.nodes.find((candidate) => candidate.id === node.id)?.document_id;
                if (source) onOpenSource(source);
              }}
              onEdgeClick={(_event, edge) => setSelectedEdge((edge.data as B5GraphEdge) ?? null)}
              proOptions={{ hideAttribution: true }}
            >
              <Background color="rgba(23,44,71,0.12)" gap={22} size={1} />
              <Controls showInteractive={false} />
            </ReactFlow>
          </div>
        </div>
        <aside className="space-y-4">
          <section className="workspace-panel p-4">
            <h4 className="text-sm font-semibold text-[var(--ink)]">Edge evidence</h4>
            {selectedEdge ? (
              <div className="mt-3 space-y-2 text-sm leading-6 text-[var(--muted)]">
                <p><strong className="text-[var(--ink)]">{selectedEdge.relationship}</strong> · {selectedEdge.evidence_class}</p>
                <p>Method: {selectedEdge.method} ({selectedEdge.method_version})</p>
                {selectedEdge.confidence !== null && selectedEdge.confidence !== undefined ? <p>Confidence: {Math.round(selectedEdge.confidence * 100)}%</p> : null}
                {Object.entries(selectedEdge.evidence).slice(0, 3).map(([key, value]) => <p key={key}><strong>{key.replaceAll("_", " ")}:</strong> {String(value)}</p>)}
                {selectedEdge.limitations.length ? <p>{selectedEdge.limitations.join(" ")}</p> : null}
                {edgeSupportingDocumentId(selectedEdge, graph?.nodes ?? []) ? <button type="button" onClick={() => onOpenSource(edgeSupportingDocumentId(selectedEdge, graph?.nodes ?? [])!)} className="rounded-lg border border-[var(--border)] bg-white px-3 py-2 text-xs font-semibold text-[var(--ink)] hover:border-[var(--accent)] focus-visible:outline-2 focus-visible:outline-[var(--accent)]">Inspect supporting source</button> : null}
                {onOpenAudit ? <button type="button" onClick={onOpenAudit} className="rounded-lg border border-[var(--border)] bg-white px-3 py-2 text-xs font-semibold text-[var(--ink)] hover:border-[var(--accent)] focus-visible:outline-2 focus-visible:outline-[var(--accent)]">Open A3 audit</button> : null}
              </div>
            ) : <p className="mt-3 text-sm leading-6 text-[var(--muted)]">Select an edge or use the accessible edge list below.</p>}
          </section>
          <section className="workspace-panel p-4">
            <h4 className="text-sm font-semibold text-[var(--ink)]">Accessible edge list</h4>
            <div className="mt-3 space-y-2">
              {visibleEdges.slice(0, edgeLimit).map((edge) => (
                <button key={edge.id} type="button" onClick={() => setSelectedEdge(edge)} className="block w-full rounded-lg border border-[var(--border)] bg-white p-2 text-left text-xs text-[var(--ink)] hover:border-[var(--accent)] focus-visible:outline-2 focus-visible:outline-[var(--accent)]">
                  <span className="font-semibold">{edge.relationship}</span>
                  <span className="ml-2 text-[var(--muted)]">{edge.evidence_class}</span>
                </button>
              ))}
              {!visibleEdges.length ? <p className="text-sm text-[var(--muted)]">No graph edges are available yet.</p> : null}
              {visibleEdges.length > edgeLimit ? <button type="button" onClick={() => setEdgeLimit((limit) => Math.min(visibleEdges.length, limit + 50))} className="mt-2 w-full rounded-lg border border-[var(--border)] bg-white px-3 py-2 text-xs font-semibold text-[var(--ink)] hover:border-[var(--accent)] focus-visible:outline-2 focus-visible:outline-[var(--accent)]" aria-label={`Show more graph edges (${visibleEdges.length - edgeLimit} remaining)`}>Show more edges ({visibleEdges.length - edgeLimit} remaining)</button> : null}
            </div>
          </section>
        </aside>
      </div>

      <section className="workspace-panel p-5">
        <h4 className="text-sm font-semibold text-[var(--ink)]">Inspect a provenance path</h4>
        <div className="mt-3 grid gap-3 sm:grid-cols-[1fr_1fr_auto]">
          <label className="text-xs font-semibold text-[var(--muted)]">From document
            <select value={fromDocumentId} onChange={(event) => setFromDocumentId(event.target.value)} className="mt-1 w-full rounded-lg border border-[var(--border)] bg-white px-2 py-2 text-sm font-normal">
              <option value="">Select document</option>
              {documentNodes.map((node) => <option key={`from-${node.id}`} value={node.document_id ?? ""}>{node.label}</option>)}
            </select>
          </label>
          <label className="text-xs font-semibold text-[var(--muted)]">To document
            <select value={toDocumentId} onChange={(event) => setToDocumentId(event.target.value)} className="mt-1 w-full rounded-lg border border-[var(--border)] bg-white px-2 py-2 text-sm font-normal">
              <option value="">Select document</option>
              {documentNodes.map((node) => <option key={`to-${node.id}`} value={node.document_id ?? ""}>{node.label}</option>)}
            </select>
          </label>
          <button type="button" disabled={!fromDocumentId || !toDocumentId || fromDocumentId === toDocumentId || pathLoading} onClick={() => void loadPath()} className="self-end rounded-lg bg-[var(--ink)] px-3 py-2 text-sm font-semibold text-white disabled:opacity-50">{pathLoading ? "Loading..." : "Find path"}</button>
        </div>
        <label className="mt-3 inline-flex items-center gap-2 text-xs font-semibold text-[var(--muted)]">
          <input type="checkbox" checked={includePathInferences} onChange={(event) => setIncludePathInferences(event.target.checked)} />
          Include inferred edges in paths
        </label>
        {paths?.paths.length ? <div className="mt-4 space-y-3">{paths.paths.map((path, index) => <PathCard key={index} path={path} />)}</div> : paths ? <p className="mt-4 text-sm text-[var(--muted)]">No bounded path was found between these documents.</p> : null}
      </section>

      <section className="workspace-panel p-5">
        <div className="flex flex-wrap items-center justify-between gap-3"><div><p className="eyebrow">Timeline context</p><h4 className="mt-2 text-lg font-semibold text-[var(--ink)]">Recorded events</h4></div><span className="text-sm text-[var(--muted)]">{timeline.length} recorded</span></div>
        <div className="mt-4 space-y-2">{timeline.slice(0, 12).map((event) => <button key={event.id} type="button" onClick={() => onOpenSource(event.document_id)} className="block w-full rounded-lg border border-[var(--border)] bg-white/70 p-3 text-left hover:border-[var(--accent)] focus-visible:outline-2 focus-visible:outline-[var(--accent)]"><div className="flex flex-wrap justify-between gap-2 text-sm"><span className="font-semibold text-[var(--ink)]">{event.title}</span><time className="text-xs text-[var(--muted)]">{new Date(event.timestamp).toLocaleDateString()}</time></div><p className="mt-1 text-xs leading-5 text-[var(--muted)]">{event.explanation}</p></button>)}</div>
      </section>
    </section>
  );
}

export function graphNodeToDocument(node: B5GraphNode) {
  return node.document_id ?? null;
}

export function edgeSupportingDocumentId(edge: B5GraphEdge, nodes: B5GraphNode[]) {
  if (edge.document_id) return edge.document_id;
  for (const endpoint of [edge.source, edge.target]) {
    const node = nodes.find((candidate) => candidate.id === endpoint || candidate.document_id === endpoint);
    if (node?.document_id) return node.document_id;
  }
  return null;
}

function PathCard({ path }: { path: B5ProvenancePathsResponse["paths"][number] }) {
  const value = path as B5ProvenancePathsResponse["paths"][number] & { document_ids?: string[]; node_ids?: string[]; edge_ids?: string[] };
  const ids = value.document_ids ?? value.node_ids ?? [];
  const explanation = value.explanation || (ids.length ? `${ids.join(" -> ")}${value.edge_ids?.length ? ` (${value.edge_ids.length} edges)` : ""}` : "Bounded path returned without an explanation.");
  return <article className="rounded-xl border border-[var(--border)] bg-white/70 p-3"><p className="text-sm leading-6 text-[var(--ink)]">{explanation}</p>{value.limitations?.length ? <p className="mt-2 text-xs leading-5 text-[var(--muted)]">{value.limitations.join(" ")}</p> : null}</article>;
}

function normalizeGraphResponse(response: B5GraphResponse): B5GraphResponse {
  return {
    ...response,
    nodes: response.nodes.map((node) => ({ ...node, kind: node.kind || "document" })),
    edges: response.edges.map((edge) => {
      const legacy = edge as B5GraphEdge & { edge_type?: string; inferred?: boolean; weight?: number; evidence?: Record<string, unknown> };
      return {
        ...edge,
        relationship: edge.relationship || legacy.edge_type || "related",
        evidence_class: edge.evidence_class || (legacy.inferred ? "inferred" : "observed"),
        method: edge.method || "canonical graph fallback",
        method_version: edge.method_version || "b5-graph-v1",
        confidence: edge.confidence ?? legacy.weight ?? null,
        evidence: edge.evidence || {},
        limitations: edge.limitations || [],
        snapshot_hash: edge.snapshot_hash || "",
      };
    }),
  };
}
