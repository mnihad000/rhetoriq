import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import B5EvidenceSearch from "./B5EvidenceSearch";

function response(overrides: Record<string, unknown> = {}) {
  return {
    ok: true,
    json: async () => ({
      investigation_id: "inv-1", query: "", mode: "fulltext", results: [], total: 0,
      next_offset: null, source: "fallback", fallback_active: true, pending: false,
      complete: true, limitations: [], generation: 1, ...overrides,
    }),
  };
}

describe("B5EvidenceSearch", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("renders fallback state and exposes source result actions", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(response({
      total: 1,
      results: [{ document_id: "doc-1", title: "A source", source_name: "Example", url: "https://example.com/a", citable: true, score: 0.9, contributions: {}, spans: [{ start: 0, end: 8, text: "A source" }], revision: 1, semantic_hash: "hash" }],
    }) as Response);
    const onOpenSource = vi.fn();
    render(<B5EvidenceSearch investigationId="inv-1" sourceTypes={["blog"]} onOpenSource={onOpenSource} />);
    fireEvent.change(screen.getByLabelText("Query"), { target: { value: "source" } });
    expect(await screen.findByRole("status")).toHaveTextContent("fallback");
    expect(screen.getAllByText("A source")[0]).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open source" }));
    expect(onOpenSource).toHaveBeenCalledWith("doc-1", { start: 0, end: 8, text: "A source" });
  });

  it("ignores a stale response after an aborted query", async () => {
    let resolveFirst: ((value: Response) => void) | undefined;
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((_input, init) => new Promise((resolve) => {
      if (String(_input).includes("q=old")) resolveFirst = resolve;
      else resolve(response({ query: "new", total: 0 }) as Response);
      init?.signal?.addEventListener("abort", () => undefined);
    }));
    render(<B5EvidenceSearch investigationId="inv-1" onOpenSource={() => undefined} />);
    const queryInput = screen.getAllByLabelText("Query").at(-1)!;
    fireEvent.change(queryInput, { target: { value: "old" } });
    fireEvent.change(queryInput, { target: { value: "new" } });
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    resolveFirst?.(response({ query: "old", total: 99 }) as Response);
    await waitFor(() => expect(screen.queryByText("99 results")).not.toBeInTheDocument());
  });
});
