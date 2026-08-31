import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import RecentInvestigations from "./RecentInvestigations";

const investigations = [
  {
    investigation_id: "one",
    query_text: "Water safety",
    status: "report_completed" as const,
    updated_at: "2026-08-30T12:00:00Z",
    report_title: "Water safety report",
    report_summary: "A concise evidence summary.",
    receipt_count: 4,
    source_count: 6,
  },
  {
    investigation_id: "two",
    query_text: "Transit service",
    status: "retrieval_completed" as const,
    updated_at: "2026-08-30T11:00:00Z",
    report_title: "Transit update",
    report_summary: null,
    receipt_count: 0,
    source_count: 2,
  },
];

describe("RecentInvestigations", () => {
  it("filters history by text and status, then restores all results", () => {
    render(<MemoryRouter><RecentInvestigations investigations={investigations} errorMessage={null} /></MemoryRouter>);

    fireEvent.change(screen.getByLabelText("Search investigations"), { target: { value: "water" } });
    expect(screen.getByText("Water safety report")).toBeInTheDocument();
    expect(screen.queryByText("Transit update")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Search investigations"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "retrieval_completed" } });
    expect(screen.getByText("Transit update")).toBeInTheDocument();
    expect(screen.queryByText("Water safety report")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(screen.getByText("Water safety report")).toBeInTheDocument();
    expect(screen.getByText("Transit update")).toBeInTheDocument();
  });
});
