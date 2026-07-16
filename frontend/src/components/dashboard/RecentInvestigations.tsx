import { Link } from "react-router-dom";
import { createInvestigationHref } from "../../lib/investigationHref";
import type { LiveRecentInvestigationSummary } from "../../types/rhetoriq";
import Section from "../layout/Section";

type RecentInvestigationsProps = {
  investigations: LiveRecentInvestigationSummary[] | null;
  errorMessage: string | null;
};

export default function RecentInvestigations({
  investigations,
  errorMessage,
}: RecentInvestigationsProps) {
  return (
    <Section
      eyebrow="Recent Investigations"
<<<<<<< HEAD
      title="Open a prepared investigation."
      description="Source-grounded investigations ready to explore — each with a full narrative tree, timeline, and receipts."
=======
      title="Return to live investigations."
      description="Every card below is sourced from persisted backend investigations rather than seeded demo routes."
>>>>>>> origin/main
      className="pt-16"
    >
      {errorMessage ? (
        <StateCard
          title="Recent investigations unavailable"
          body={errorMessage}
          tone="error"
        />
      ) : investigations === null ? (
        <StateCard
          title="Loading recent investigations"
          body="Fetching persisted investigation workspaces from the backend."
          tone="neutral"
        />
      ) : investigations.length === 0 ? (
        <StateCard
          title="No live investigations yet"
          body="Start a new investigation from the dashboard and it will appear here once retrieval has completed."
          tone="neutral"
        />
      ) : (
        <div className="grid gap-4 lg:grid-cols-3">
          {investigations.map((investigation) => (
            <Link
              key={investigation.investigation_id}
              to={createInvestigationHref(investigation.investigation_id)}
              className="surface-card group flex h-full flex-col gap-5 p-6 transition hover:-translate-y-1 hover:border-[var(--accent)]"
            >
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="eyebrow">
                    {investigation.status.replaceAll("_", " ")}
                  </p>
                  <h3 className="mt-4 text-2xl font-semibold tracking-[-0.03em] text-[var(--ink)]">
                    {investigation.report_title}
                  </h3>
                </div>
                <span className="rounded-full border border-[var(--border)] px-3 py-1 text-sm font-semibold text-[var(--muted)]">
                  {investigation.receipt_count} receipts
                </span>
              </div>

              <p className="flex-1 text-base leading-7 text-[var(--muted)]">
                {investigation.report_summary ??
                  "This investigation has live persisted state, but its summary is still being assembled."}
              </p>

              <div className="flex items-center justify-between text-sm text-[var(--muted)]">
                <span>{formatUpdatedAt(investigation.updated_at)}</span>
                <span>
                  {investigation.source_count}{" "}
                  {investigation.source_count === 1 ? "source" : "sources"}
                </span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </Section>
  );
}

function StateCard({
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

function formatUpdatedAt(value: string) {
  return `Updated ${new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value))}`;
}
