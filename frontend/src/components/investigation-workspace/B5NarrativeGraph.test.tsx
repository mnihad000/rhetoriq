import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getInvestigationGraph, getProvenancePaths } from "../../lib/api";
import type { B5GraphResponse, B5ProvenancePath, B5ProvenancePathsResponse } from "../../types/rhetoriq";
import B5NarrativeGraph from "./B5NarrativeGraph";

vi.mock("../../lib/api", () => ({
  getInvestigationGraph: vi.fn(),
  getProvenancePaths: vi.fn(),
}));

vi.mock("@xyflow/react", () => ({
  Background: () => <div aria-hidden="true" />,
  Controls: () => <div aria-hidden="true" />,
  ReactFlow: ({ children }: { children: React.ReactNode }) => <div data-testid="react-flow">{children}</div>,
}));

const graph: B5GraphResponse = {
  investigation_id: "inv-1", nodes: [
    { id: "doc-a", kind: "document", label: "First source", document_id: "doc-a" },
    { id: "doc-b", kind: "document", label: "Second source", document_id: "doc-b" },
  ], edges: [
    { id: "edge-observed", source: "doc-a", target: "doc-b", relationship: "references", evidence_class: "observed", method: "b4.source_link", method_version: "b5-graph-v1", evidence: { surface_form: "the report" }, limitations: [], snapshot_hash: "sha-observed" },
    { id: "edge-inferred", source: "doc-a", target: "doc-b", relationship: "mutation", evidence_class: "inferred", method: "MutationDetector", method_version: "b5-mutation-v1", confidence: 0.7, evidence: { similarity: 0.7 }, limitations: ["Hypothesis only"], snapshot_hash: "sha-inferred" },
  ], source: "canonical", fallback_active: true, pending: false, complete: true, limitations: ["Fallback graph"], truncated: false, generation: 1,
};

function pathResponse(paths: B5ProvenancePath[]): B5ProvenancePathsResponse {
  return { investigation_id: "inv-1", paths, source: "canonical", fallback_active: true, pending: false, complete: true, limitations: [], generation: 1 };
}

describe("B5NarrativeGraph", () => {
  afterEach(() => { cleanup(); vi.useRealTimers(); });
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getInvestigationGraph).mockResolvedValue(graph);
    vi.mocked(getProvenancePaths).mockResolvedValue(pathResponse([]));
  });

  it("selects an edge and exposes its evidence metadata", async () => {
    render(<B5NarrativeGraph investigationId="inv-1" onOpenSource={() => undefined} />);
    const edge = await findEdge("references", "observed");
    fireEvent.click(edge);
    expect(screen.getByText(/b4\.source_link/)).toBeInTheDocument();
    expect(screen.getByText(/surface form:/i)).toBeInTheDocument();
    expect(screen.getByText(/the report/)).toBeInTheDocument();
  });

  it("filters inferred edges with the toggle and class selector", async () => {
    render(<B5NarrativeGraph investigationId="inv-1" onOpenSource={() => undefined} />);
    expect(await findEdge("mutation", "inferred")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Show inferred edges"));
    await waitFor(() => expect(findEdgeSync("mutation", "inferred")).toBeNull());
    fireEvent.click(screen.getByLabelText("Show inferred edges"));
    fireEvent.change(screen.getByLabelText("Evidence class"), { target: { value: "observed" } });
    expect(await findEdge("references", "observed")).toBeInTheDocument();
    expect(findEdgeSync("mutation", "inferred")).toBeNull();
  });

  it("loads a path using document selectors and accepts node_ids/edge_ids responses", async () => {
    vi.mocked(getProvenancePaths).mockResolvedValue(pathResponse([{ node_ids: ["doc-a", "doc-b"], edge_ids: ["edge-observed"], explanation: "Document path is bounded to acquired evidence.", limitations: ["Observed only"] }]));
    render(<B5NarrativeGraph investigationId="inv-1" onOpenSource={() => undefined} />);
    await findEdge("references", "observed");
    fireEvent.change(screen.getByLabelText("From document"), { target: { value: "doc-a" } });
    fireEvent.change(screen.getByLabelText("To document"), { target: { value: "doc-b" } });
    fireEvent.click(screen.getByRole("button", { name: "Find path" }));
    expect(await screen.findByText("Document path is bounded to acquired evidence.")).toBeInTheDocument();
    expect(getProvenancePaths).toHaveBeenCalledWith("inv-1", expect.objectContaining({ fromDocumentId: "doc-a", toDocumentId: "doc-b", maxDepth: 4, includeInferred: false }));
  });

  it("announces pending, fallback limitations, and an empty path", async () => {
    vi.mocked(getInvestigationGraph).mockResolvedValue({ ...graph, pending: true, complete: false, fallback_active: true, limitations: ["Still indexing"] });
    render(<B5NarrativeGraph investigationId="inv-1" onOpenSource={() => undefined} />);
    expect(await screen.findByText(/graph is using the deterministic fallback/i)).toBeInTheDocument();
    expect(await screen.findByText(/graph evidence is still being assembled/i)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("From document"), { target: { value: "doc-a" } });
    fireEvent.change(screen.getByLabelText("To document"), { target: { value: "doc-b" } });
    fireEvent.click(screen.getByRole("button", { name: "Find path" }));
    expect(await screen.findByText(/no bounded path was found/i)).toBeInTheDocument();
  });

  it("falls back to the stored tree and reports graph errors", async () => {
    vi.mocked(getInvestigationGraph).mockRejectedValue(new Error("offline"));
    render(<B5NarrativeGraph investigationId="inv-1" fallbackData={{ title: "Fallback", query: "q", currentNodeId: "doc-a", nodes: [], edges: [] }} onOpenSource={() => undefined} />);
    expect(await screen.findByText(/stored timeline view/i)).toBeInTheDocument();
  });

  it("refreshes a pending graph until the projection becomes current", async () => {
    vi.useFakeTimers();
    vi.mocked(getInvestigationGraph).mockResolvedValueOnce({ ...graph, pending: true, complete: false })
      .mockResolvedValue({ ...graph, source: "neo4j", pending: false, complete: true, fallback_active: false });
    render(<B5NarrativeGraph investigationId="inv-1" onOpenSource={() => undefined} />);
    await act(async () => { await Promise.resolve(); });
    expect(getInvestigationGraph).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(getInvestigationGraph).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(getInvestigationGraph).toHaveBeenCalledTimes(2);
  });

  it("makes all bounded relationships accessible through the edge list", async () => {
    const edges = Array.from({ length: 51 }, (_, index) => ({ ...graph.edges[0], id: `edge-${index}` }));
    vi.mocked(getInvestigationGraph).mockResolvedValue({ ...graph, edges });
    render(<B5NarrativeGraph investigationId="inv-1" onOpenSource={() => undefined} />);
    const more = await screen.findByRole("button", { name: "Show more graph edges (1 remaining)" });
    fireEvent.click(more);
    expect(screen.queryByRole("button", { name: /Show more graph edges/ })).toBeNull();
    expect(screen.getAllByRole("button").filter((button) => button.textContent === "referencesobserved")).toHaveLength(51);
  });
});

async function findEdge(relationship: string, evidenceClass: string) {
  await waitFor(() => expect(findEdgeSync(relationship, evidenceClass)).not.toBeNull());
  return findEdgeSync(relationship, evidenceClass)!;
}

function findEdgeSync(relationship: string, evidenceClass: string) {
  return screen.getAllByRole("button").find((button) => { const text = button.textContent?.toLowerCase() ?? ""; return text.includes(relationship) && text.includes(evidenceClass); }) ?? null;
}
