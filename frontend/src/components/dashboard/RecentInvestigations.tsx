import { useDeferredValue, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { createInvestigationHref } from "../../lib/investigationHref";
import type { LiveRecentInvestigationSummary } from "../../types/rhetoriq";
import Section from "../layout/Section";

type RecentInvestigationsProps = { investigations: LiveRecentInvestigationSummary[] | null; errorMessage: string | null };

export default function RecentInvestigations({ investigations, errorMessage }: RecentInvestigationsProps) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const deferredQuery = useDeferredValue(query.trim().toLowerCase());
  const statuses = useMemo(() => Array.from(new Set((investigations ?? []).map((item) => item.status))).sort(), [investigations]);
  const filtered = useMemo(() => (investigations ?? []).filter((item) => {
    const searchable = `${item.report_title} ${item.query_text} ${item.report_summary ?? ""}`.toLowerCase();
    return (!deferredQuery || searchable.includes(deferredQuery)) && (status === "all" || item.status === status);
  }), [deferredQuery, investigations, status]);

  return <Section eyebrow="Recent Investigations" title="Open a prepared investigation." description="Source-grounded investigations ready to explore — each with a concise report and inspectable evidence." className="pt-16">
    {errorMessage ? <StateCard title="Recent investigations unavailable" body={errorMessage} tone="error" /> : investigations === null ? <StateCard title="Loading recent investigations" body="Fetching persisted investigation workspaces from the backend." tone="neutral" /> : investigations.length === 0 ? <StateCard title="No live investigations yet" body="Start a new investigation from the dashboard and it will appear here once retrieval has completed." tone="neutral" /> : <>
      <div className="mb-6 grid gap-3 rounded-[1.4rem] border border-[var(--border)] bg-white/55 p-4 sm:grid-cols-[minmax(0,1fr)_13rem_auto]">
        <label className="text-sm font-semibold text-[var(--ink)]">Search investigations<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Title, question, or summary" className="mt-2 w-full rounded-xl border border-[var(--border)] bg-white px-3 py-2.5 text-sm font-normal outline-none transition focus:border-[var(--accent)] focus:ring-2 focus:ring-[var(--accent-soft)]" /></label>
        <label className="text-sm font-semibold text-[var(--ink)]">Status<select value={status} onChange={(event) => setStatus(event.target.value)} className="mt-2 w-full rounded-xl border border-[var(--border)] bg-white px-3 py-2.5 text-sm font-normal"><option value="all">All statuses</option>{statuses.map((item) => <option key={item} value={item}>{item.replaceAll("_", " ")}</option>)}</select></label>
        {query || status !== "all" ? <button type="button" onClick={() => { setQuery(""); setStatus("all"); }} className="self-end rounded-xl px-3 py-2.5 text-sm font-semibold text-[var(--ink)] underline underline-offset-4">Clear</button> : <span className="self-end pb-3 text-sm text-[var(--muted)]">Latest 12 workspaces</span>}
      </div>
      {filtered.length === 0 ? <StateCard title="No matching investigations" body="Try a different search term or clear the active filters." tone="neutral" /> : <div className="grid gap-4 lg:grid-cols-3">{filtered.map((investigation) => <Link key={investigation.investigation_id} to={createInvestigationHref(investigation.investigation_id)} className="surface-card group flex h-full flex-col gap-5 p-6 transition hover:-translate-y-1 hover:border-[var(--accent)] focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[var(--accent)]"><div className="flex items-start justify-between gap-4"><div><p className="eyebrow">{investigation.status.replaceAll("_", " ")}</p><h3 className="mt-4 text-2xl font-semibold tracking-[-0.03em] text-[var(--ink)]">{investigation.report_title}</h3></div><span className="rounded-full border border-[var(--border)] px-3 py-1 text-sm font-semibold text-[var(--muted)]">{investigation.receipt_count} receipts</span></div><p className="flex-1 text-base leading-7 text-[var(--muted)]">{investigation.report_summary ?? "This investigation has live persisted state, but its summary is still being assembled."}</p><div className="flex items-center justify-between text-sm text-[var(--muted)]"><span>{formatUpdatedAt(investigation.updated_at)}</span><span>{investigation.source_count} {investigation.source_count === 1 ? "source" : "sources"}</span></div></Link>)}</div>}
    </>}
  </Section>;
}

function StateCard({ title, body, tone }: { title: string; body: string; tone: "neutral" | "error" }) { return <div className={tone === "error" ? "surface-card rounded-[1.6rem] border border-[rgba(146,71,71,0.18)] bg-[rgba(255,244,244,0.92)] p-6 text-[rgb(130,50,50)]" : "surface-card rounded-[1.6rem] p-6"}><p className="eyebrow">{title}</p><p className="mt-4 max-w-3xl text-base leading-7">{body}</p></div>; }
function formatUpdatedAt(value: string) { return `Updated ${new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value))}`; }
