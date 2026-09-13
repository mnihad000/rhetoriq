import { Position, type Edge } from "@xyflow/react";

type GraphHandle = {
  type: "source" | "target";
  position: Position;
  offset?: number;
};

export const GRAPH_HANDLES = {
  "in-left": { type: "target", position: Position.Left },
  "in-right": { type: "target", position: Position.Right },
  "in-top": { type: "target", position: Position.Top },
  "in-top-left": { type: "target", position: Position.Top, offset: 30 },
  "in-bottom-left": { type: "target", position: Position.Bottom, offset: 25 },
  "in-bottom-right": { type: "target", position: Position.Bottom, offset: 85 },
  "out-left": { type: "source", position: Position.Left },
  "out-right": { type: "source", position: Position.Right },
  "out-top": { type: "source", position: Position.Top },
  "out-top-right": { type: "source", position: Position.Top, offset: 70 },
  "out-bottom": { type: "source", position: Position.Bottom },
  "out-bottom-left": { type: "source", position: Position.Bottom, offset: 25 },
  "out-bottom-middle": { type: "source", position: Position.Bottom, offset: 65 },
  "out-bottom-right": { type: "source", position: Position.Bottom, offset: 70 },
  "out-bottom-gate-left": { type: "source", position: Position.Bottom, offset: 30 },
} as const satisfies Record<string, GraphHandle>;

export type GraphHandleId = keyof typeof GRAPH_HANDLES;

type GraphNodeSpec = {
  id: string;
  label: string;
  x: number;
  y: number;
  handles: readonly GraphHandleId[];
};

export const GRAPH_NODES = [
  { id: "initialize_run", label: "Initialize", x: 0, y: 0, handles: ["out-right"] },
  { id: "assess_research_state", label: "Assess gaps", x: 210, y: 0, handles: ["in-left", "in-bottom-right", "out-right", "out-bottom-left", "out-bottom-middle"] },
  { id: "supervisor_select_action", label: "Select action", x: 420, y: 0, handles: ["in-left", "in-bottom-left", "out-right"] },
  { id: "validate_policy_and_budget", label: "Policy + budget", x: 630, y: 0, handles: ["in-left", "out-bottom"] },
  { id: "finalize_insufficient_evidence", label: "No evidence", x: 0, y: 160, handles: ["in-right"] },
  { id: "build_evidence_artifacts", label: "Build evidence", x: 210, y: 160, handles: ["in-top", "out-bottom"] },
  { id: "normalize_and_persist", label: "Normalize + receipt", x: 420, y: 160, handles: ["in-right", "out-top"] },
  { id: "dispatch_action", label: "Run tool", x: 630, y: 160, handles: ["in-top", "out-left"] },
  { id: "skeptic_review", label: "Skeptic review", x: 210, y: 320, handles: ["in-top-left", "out-top-right", "out-right"] },
  { id: "build_candidate_report_and_receipts", label: "Stage candidate", x: 420, y: 320, handles: ["in-left", "out-right"] },
  { id: "publication_gate", label: "Publication gate", x: 630, y: 320, handles: ["in-left", "out-bottom-right", "out-bottom-gate-left"] },
  { id: "withhold_report", label: "Withhold", x: 420, y: 480, handles: ["in-top"] },
  { id: "publish_report", label: "Publish", x: 630, y: 480, handles: ["in-top"] },
] as const satisfies readonly GraphNodeSpec[];

type GraphRoute = "feedback" | "retry" | "no-evidence";
export type ResearchGraphEdge = Edge<{ route?: GraphRoute }>;

type EdgeSpec = readonly [string, string, GraphHandleId, GraphHandleId, GraphRoute?];

const EDGE_SPECS: readonly EdgeSpec[] = [
  ["initialize_run", "assess_research_state", "out-right", "in-left"],
  ["assess_research_state", "supervisor_select_action", "out-right", "in-left"],
  ["supervisor_select_action", "validate_policy_and_budget", "out-right", "in-left"],
  ["validate_policy_and_budget", "dispatch_action", "out-bottom", "in-top"],
  ["dispatch_action", "normalize_and_persist", "out-left", "in-right"],
  ["normalize_and_persist", "assess_research_state", "out-top", "in-bottom-right", "feedback"],
  ["assess_research_state", "build_evidence_artifacts", "out-bottom-middle", "in-top"],
  ["assess_research_state", "finalize_insufficient_evidence", "out-bottom-left", "in-right", "no-evidence"],
  ["build_evidence_artifacts", "skeptic_review", "out-bottom", "in-top-left"],
  ["skeptic_review", "supervisor_select_action", "out-top-right", "in-bottom-left", "retry"],
  ["skeptic_review", "build_candidate_report_and_receipts", "out-right", "in-left"],
  ["build_candidate_report_and_receipts", "publication_gate", "out-right", "in-left"],
  ["publication_gate", "publish_report", "out-bottom-right", "in-top"],
  ["publication_gate", "withhold_report", "out-bottom-gate-left", "in-top"],
];

export const GRAPH_EDGES: ResearchGraphEdge[] = EDGE_SPECS.map(([source, target, sourceHandle, targetHandle, route]) => ({
  id: `${source}-${target}`,
  source,
  target,
  sourceHandle,
  targetHandle,
  type: route ? "routed" : "smoothstep",
  ...(route ? { data: { route } } : {}),
  animated: route === "feedback",
  style: {
    stroke: route === "retry" ? "rgba(126, 82, 69, 0.68)" : "rgba(40, 73, 107, 0.54)",
    strokeWidth: route === "feedback" || route === "retry" ? 1.8 : 1.5,
    ...(route === "feedback" || route === "retry" ? { strokeDasharray: "5 4" } : {}),
  },
}));
