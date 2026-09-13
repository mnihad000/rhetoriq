import { Position } from "@xyflow/react";
import { describe, expect, it } from "vitest";
import { GRAPH_EDGES, GRAPH_HANDLES, GRAPH_NODES } from "./researchGraph";

// The transitions in AutonomousResearchManager._build_graph are the graph contract.
const backendTransitions = [
  "initialize_run->assess_research_state",
  "assess_research_state->supervisor_select_action",
  "assess_research_state->build_evidence_artifacts",
  "assess_research_state->finalize_insufficient_evidence",
  "supervisor_select_action->validate_policy_and_budget",
  "validate_policy_and_budget->dispatch_action",
  "dispatch_action->normalize_and_persist",
  "normalize_and_persist->assess_research_state",
  "build_evidence_artifacts->skeptic_review",
  "skeptic_review->supervisor_select_action",
  "skeptic_review->build_candidate_report_and_receipts",
  "build_candidate_report_and_receipts->publication_gate",
  "publication_gate->publish_report",
  "publication_gate->withhold_report",
];

describe("research graph wiring", () => {
  it("keeps the frontend transitions aligned with the backend workflow", () => {
    expect(GRAPH_EDGES.map(({ source, target }) => `${source}->${target}`).sort()).toEqual(backendTransitions.sort());
  });

  it("attaches every edge to existing handles with the correct direction", () => {
    const nodes = new Map<string, (typeof GRAPH_NODES)[number]>();
    GRAPH_NODES.forEach((node) => nodes.set(node.id, node));

    for (const edge of GRAPH_EDGES) {
      const source = nodes.get(edge.source);
      const target = nodes.get(edge.target);
      expect(source, edge.id).toBeDefined();
      expect(target, edge.id).toBeDefined();
      expect(source?.handles).toContain(edge.sourceHandle);
      expect(target?.handles).toContain(edge.targetHandle);
      expect(GRAPH_HANDLES[edge.sourceHandle as keyof typeof GRAPH_HANDLES].type).toBe("source");
      expect(GRAPH_HANDLES[edge.targetHandle as keyof typeof GRAPH_HANDLES].type).toBe("target");
    }
  });

  it("routes backward and return transitions from the appropriate sides", () => {
    const edge = (source: string, target: string) => GRAPH_EDGES.find((item) => item.source === source && item.target === target);
    const handlePosition = (id: string | null | undefined) => GRAPH_HANDLES[id as keyof typeof GRAPH_HANDLES].position;

    expect(handlePosition(edge("dispatch_action", "normalize_and_persist")?.sourceHandle)).toBe(Position.Left);
    expect(handlePosition(edge("dispatch_action", "normalize_and_persist")?.targetHandle)).toBe(Position.Right);
    expect(edge("normalize_and_persist", "assess_research_state")?.data?.route).toBe("feedback");
    expect(edge("skeptic_review", "supervisor_select_action")?.data?.route).toBe("retry");
    expect(edge("assess_research_state", "finalize_insufficient_evidence")?.data?.route).toBe("no-evidence");
  });
});
