import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import NarrativeRadar from "./NarrativeRadar";
import type { LiveTrendingFeed } from "../../types/rhetoriq";

const fallbackFeed: LiveTrendingFeed = {
  state: "stale",
  source: "legacy",
  fallback_active: true,
  warning: "Flink is unavailable; showing the last valid snapshot.",
  topics: [{
    id: "legacy-1",
    title: "Legacy topic",
    canonical_phrase: "legacy topic",
    summary: "A previously valid topic.",
    related_phrases: [],
    status: "emerging",
    confidence_label: "Medium",
    confidence_score: 0.5,
    source_count: 4,
    publisher_count: 2,
    first_observed_at: "2026-09-16T10:00:00Z",
    latest_observed_at: "2026-09-16T12:00:00Z",
    source_diversity_snapshot: {},
    timeline: [],
    velocity_score: 1,
    persistence_runs: 1,
    provider_mix: {},
    supporting_document_ids: [],
    pipeline_source: "legacy",
  }],
};

describe("NarrativeRadar fallback", () => {
  it("keeps the last valid card visible with a clear fallback warning", () => {
    render(<MemoryRouter><NarrativeRadar feed={fallbackFeed} errorMessage={null} /></MemoryRouter>);

    expect(screen.getAllByText("Flink is unavailable; showing the last valid snapshot.")).toHaveLength(2);
    expect(screen.getByRole("heading", { name: "Legacy topic" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Investigate" })).toBeInTheDocument();
  });
});
