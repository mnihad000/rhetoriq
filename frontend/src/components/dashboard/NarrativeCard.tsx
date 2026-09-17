import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, startTrendingInvestigation } from "../../lib/api";
import { createInvestigationHref } from "../../lib/investigationHref";
import type { LiveTrendingTopic } from "../../types/rhetoriq";

type NarrativeCardProps = {
  topic: LiveTrendingTopic;
};

export default function NarrativeCard({ topic }: NarrativeCardProps) {
  const navigate = useNavigate();
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isStarting, setIsStarting] = useState(false);

  const sourceMix = Object.entries(topic.source_diversity_snapshot)
    .sort((left, right) => right[1] - left[1])
    .slice(0, 3)
    .map(([label, count]) => `${label.replaceAll("_", " ")}: ${count}`)
    .join(", ");

  async function handleInvestigate() {
    setIsStarting(true);
    setErrorMessage(null);

    try {
      const response = await startTrendingInvestigation(topic.id);
      navigate(
        createInvestigationHref(
          response.investigation_id,
          `Trace the narrative around ${topic.canonical_phrase}`,
        ),
      );
    } catch (error) {
      setErrorMessage(
        error instanceof ApiError
          ? error.message
          : "Unable to start the topic investigation right now.",
      );
    } finally {
      setIsStarting(false);
    }
  }

  return (
    <article className="surface-card group flex h-full flex-col p-6 transition duration-300 hover:-translate-y-1.5 hover:border-[var(--accent)]">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="eyebrow">{topic.status}</p>
          <h3 className="mt-4 text-2xl font-semibold tracking-[-0.04em] text-[var(--ink)]">
            {topic.title}
          </h3>
        </div>
        <span className="rounded-full bg-[rgba(124,144,172,0.12)] px-3 py-1 text-sm font-semibold text-[var(--accent)]">
          {topic.confidence_label}
        </span>
      </div>

      <p className="mt-4 flex-1 text-base leading-7 text-[var(--muted)]">{topic.summary}</p>

      <dl className="mt-6 grid grid-cols-2 gap-4 border-t border-[var(--border)] pt-5 text-sm">
        <div>
          <dt className="text-[var(--muted)]">Velocity</dt>
          <dd className="mt-1 text-lg font-semibold text-[var(--ink)]">
            {topic.velocity_score.toFixed(1)}x
          </dd>
        </div>
        <div>
          <dt className="text-[var(--muted)]">Sources</dt>
          <dd className="mt-1 text-lg font-semibold text-[var(--ink)]">
            {topic.source_count}
          </dd>
        </div>
      </dl>

      {topic.pipeline_source === "flink" ? (
        <div className="mt-5 grid grid-cols-2 gap-3 rounded-[1rem] bg-[rgba(124,144,172,0.08)] p-4 text-sm">
          <Metric label="Emerging · 6h" observed={topic.emerging_observed_count} baseline={topic.emerging_baseline_count} spike={topic.emerging_spike} />
          <Metric label="Sustained · 24h" observed={topic.sustained_observed_count} baseline={topic.sustained_baseline_count} spike={topic.sustained_spike} />
          <p className="col-span-2 text-[var(--muted)]">{topic.publisher_count} publishers · {topic.event_time_quality} event time</p>
        </div>
      ) : null}

      <div className="mt-6 space-y-3 text-sm text-[var(--muted)]">
        <p>
          First observed in our dataset at{" "}
          {new Date(topic.first_observed_at).toLocaleString()}
        </p>
        <p>
          <span className="font-semibold text-[var(--ink)]">Source mix:</span> {sourceMix}
        </p>
        <p>
          <span className="font-semibold text-[var(--ink)]">Persistence:</span>{" "}
          {topic.persistence_runs} runs
        </p>
        {topic.coverage_limitations?.length ? (
          <p><span className="font-semibold text-[var(--ink)]">Coverage:</span> {topic.coverage_limitations.join("; ")}</p>
        ) : null}
        {topic.origin_disclaimer ? <p className="text-xs leading-5">{topic.origin_disclaimer}</p> : null}
      </div>

      <button
        type="button"
        onClick={handleInvestigate}
        disabled={isStarting}
        className="mt-6 inline-flex items-center justify-between rounded-[1.1rem] border border-[var(--border)] bg-white px-4 py-3 text-sm font-semibold text-[var(--ink)] transition hover:border-[var(--accent)] hover:text-[var(--accent)]"
      >
        {isStarting ? "Starting investigation..." : "Investigate"}
        <span aria-hidden="true" className="transition group-hover:translate-x-1">
          {">"}
        </span>
      </button>

      {errorMessage ? (
        <p className="mt-4 rounded-[1rem] border border-[rgba(146,71,71,0.18)] bg-[rgba(255,244,244,0.92)] px-4 py-3 text-sm leading-6 text-[rgb(130,50,50)]">
          {errorMessage}
        </p>
      ) : null}
    </article>
  );
}

function Metric({ label, observed, baseline, spike }: { label: string; observed?: number | null; baseline?: number | null; spike?: number | null }) {
  if (observed == null && baseline == null && spike == null) return <p className="text-[var(--muted)]"><span className="font-semibold text-[var(--ink)]">{label}</span><br />No signal yet</p>;
  return <p className="text-[var(--muted)]"><span className="font-semibold text-[var(--ink)]">{label}</span><br />{observed ?? 0} observed / {baseline ?? 0} baseline · {(spike ?? 0).toFixed(1)}×</p>;
}
