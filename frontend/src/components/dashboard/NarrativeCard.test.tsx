import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import NarrativeCard from "./NarrativeCard";
import type { LiveTrendingTopic } from "../../types/rhetoriq";

const startTrendingInvestigation = vi.fn();

vi.mock("../../lib/api", () => ({
  ApiError: class ApiError extends Error {},
  startTrendingInvestigation: (...args: unknown[]) => startTrendingInvestigation(...args),
}));

const streamTopic: LiveTrendingTopic = {
  id: "signal-1",
  title: "Climate tax",
  canonical_phrase: "climate tax",
  summary: "A monitored narrative is accelerating.",
  related_phrases: [],
  status: "emerging",
  confidence_label: "High",
  confidence_score: 0.82,
  source_count: 5,
  publisher_count: 3,
  first_observed_at: "2026-09-16T10:00:00Z",
  latest_observed_at: "2026-09-16T12:00:00Z",
  source_diversity_snapshot: { national_news: 3, forum: 2 },
  timeline: [],
  velocity_score: 2.5,
  persistence_runs: 2,
  provider_mix: {},
  supporting_document_ids: ["doc-1"],
  pipeline_source: "flink",
  signal_revision: 2,
  emerging_observed_count: 5,
  emerging_baseline_count: 2,
  emerging_spike: 2.5,
  sustained_observed_count: 10,
  sustained_baseline_count: 4,
  sustained_spike: 2.5,
  event_time_quality: "published",
  coverage_limitations: ["Only monitored publishers are included."],
  origin_disclaimer: "First observed in our dataset does not establish origin.",
};

describe("NarrativeCard B4 stream fields", () => {
  beforeEach(() => {
    startTrendingInvestigation.mockReset();
    startTrendingInvestigation.mockResolvedValue({
      investigation_id: "inv-1",
      reused_existing: false,
      topic_id: "signal-1",
      canonical_phrase: "climate tax",
    });
  });

  it("shows both horizons, limitations, origin caveat, and keeps investigation action", async () => {
    render(
      <MemoryRouter>
        <NarrativeCard topic={streamTopic} />
      </MemoryRouter>,
    );

    expect(screen.getByText(/Emerging/)).toBeInTheDocument();
    expect(screen.getByText(/5 observed \/ 2 baseline/)).toBeInTheDocument();
    expect(screen.getByText(/10 observed \/ 4 baseline/)).toBeInTheDocument();
    expect(screen.getByText("Only monitored publishers are included.")).toBeInTheDocument();
    expect(screen.getByText("First observed in our dataset does not establish origin.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Investigate" }));
    await waitFor(() => expect(startTrendingInvestigation).toHaveBeenCalledWith("signal-1"));
  });
});
