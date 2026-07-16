import { startTransition, useEffect, useState } from "react";
import Hero from "../components/dashboard/Hero";
import NarrativeRadar from "../components/dashboard/NarrativeRadar";
import RecentInvestigations from "../components/dashboard/RecentInvestigations";
import TrustStrip from "../components/dashboard/TrustStrip";
import Header from "../components/layout/Header";
import { ApiError, getRecentInvestigations, getTrendingFeed } from "../lib/api";
import { examplePrompts } from "../lib/demoData";
import type {
  LiveRecentInvestigationSummary,
  LiveTrendingFeed,
} from "../types/rhetoriq";

export default function DashboardPage() {
  const [feed, setFeed] = useState<LiveTrendingFeed | null>(null);
  const [feedErrorMessage, setFeedErrorMessage] = useState<string | null>(null);
  const [recentInvestigations, setRecentInvestigations] = useState<
    LiveRecentInvestigationSummary[] | null
  >(null);
  const [recentErrorMessage, setRecentErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadDashboard() {
      const [feedResult, recentResult] = await Promise.allSettled([
        getTrendingFeed(),
        getRecentInvestigations(),
      ]);

      if (cancelled) {
        return;
      }

      if (feedResult.status === "fulfilled") {
        startTransition(() => {
          setFeed(feedResult.value);
          setFeedErrorMessage(null);
        });
      } else {
        const error = feedResult.reason;
        setFeedErrorMessage(
          error instanceof ApiError
            ? error.message
            : "Unable to load the live hot-topics feed.",
        );
      }

      if (recentResult.status === "fulfilled") {
        startTransition(() => {
          setRecentInvestigations(recentResult.value);
          setRecentErrorMessage(null);
        });
      } else {
        const error = recentResult.reason;
        setRecentErrorMessage(
          error instanceof ApiError
            ? error.message
            : "Unable to load recent live investigations.",
        );
      }
    }

    void loadDashboard();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main>
      <Header />
      <Hero prompts={examplePrompts} />
      <NarrativeRadar feed={feed} errorMessage={feedErrorMessage} />
      <RecentInvestigations
        investigations={recentInvestigations}
        errorMessage={recentErrorMessage}
      />
      <TrustStrip />
    </main>
  );
}
