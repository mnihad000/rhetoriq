import { describe, expect, it } from "vitest";
import {
  buildInvestigationFlowchartData,
  buildInvestigationHeaderFromWorkspace,
} from "./liveInvestigation";
import { getMockInvestigationWorkspace } from "./mockInvestigation";

describe("investigation display adapters", () => {
  const workspace = getMockInvestigationWorkspace("display-test");

  it("derives header fields without constructing graph data", () => {
    const header = buildInvestigationHeaderFromWorkspace(workspace);

    expect(header).toMatchObject({
      id: workspace.investigation_id,
      title: workspace.report?.report_title,
      sourceCount: workspace.retrieval?.coverage_summary.total_documents,
    });
    expect(header.receiptCount).toBeGreaterThan(0);
    expect(header).not.toHaveProperty("flowchartData");
  });

  it("preserves the flowchart fallback data independently", () => {
    const flowchart = buildInvestigationFlowchartData(workspace);

    expect(flowchart.currentNodeId).toBe("current-narrative");
    expect(flowchart.query).toBe(workspace.query_text);
    expect(flowchart.nodes.some((node) => node.id === flowchart.currentNodeId)).toBe(true);
    expect(flowchart.edges.length).toBeGreaterThan(0);
  });
});