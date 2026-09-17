import Section from "../layout/Section";
import NarrativeCard from "./NarrativeCard";
import type { LiveTrendingFeed } from "../../types/rhetoriq";

type NarrativeRadarProps = {
  feed: LiveTrendingFeed | null;
  errorMessage: string | null;
};

export default function NarrativeRadar({
  feed,
  errorMessage,
}: NarrativeRadarProps) {
  const description = feed?.warning
    ? feed.warning
    : "Live hot topics are discovered from our own scheduled search corpus and published only after deterministic scoring.";

  return (
    <Section
      eyebrow="Live Narrative Radar"
      title="What is breaking into the conversation right now?"
      description={description}
      className="pt-6"
    >
      {errorMessage ? (
        <RadarStateCard
          title="Radar unavailable"
          body={errorMessage}
          tone="error"
        />
      ) : feed === null ? (
        <RadarStateCard
          title="Loading live radar"
          body="Fetching the latest published hot-topics snapshot."
          tone="neutral"
        />
      ) : feed.topics.length === 0 ? (
        <RadarStateCard
          title={feed.state === "error" ? "Live radar unavailable" : "Live radar warming"}
          body={
            feed.warning ??
            "No topic has cleared the live publish thresholds yet. The dashboard is waiting for a fresh ranked snapshot."
          }
          tone={feed.state === "error" ? "error" : "neutral"}
        />
      ) : (
        <>
          {feed.fallback_active ? (
            <div className="mb-5 rounded-[1rem] border border-[rgba(146,71,71,0.18)] bg-[rgba(255,244,244,0.92)] px-4 py-3 text-sm text-[rgb(130,50,50)]">
              {feed.warning ?? "The stream is stale; these cards show the last valid legacy snapshot."}
            </div>
          ) : null}
          <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
            {feed.topics.map((topic) => (
              <NarrativeCard key={topic.id} topic={topic} />
            ))}
          </div>
        </>
      )}
    </Section>
  );
}

function RadarStateCard({
  title,
  body,
  tone,
}: {
  title: string;
  body: string;
  tone: "neutral" | "error";
}) {
  return (
    <div
      className={
        tone === "error"
          ? "surface-card rounded-[1.6rem] border border-[rgba(146,71,71,0.18)] bg-[rgba(255,244,244,0.92)] p-6 text-[rgb(130,50,50)]"
          : "surface-card rounded-[1.6rem] p-6"
      }
    >
      <p className="eyebrow">{title}</p>
      <p className="mt-4 max-w-3xl text-base leading-7">{body}</p>
    </div>
  );
}
