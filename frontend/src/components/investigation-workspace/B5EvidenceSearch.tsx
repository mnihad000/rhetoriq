import { useEffect, useMemo, useState } from "react";
import { ApiError, searchInvestigationEvidence } from "../../lib/api";
import type { B5EvidenceResult, B5SearchMode } from "../../types/rhetoriq";

type B5EvidenceSearchProps = {
  investigationId: string;
  sourceTypes?: string[];
  onOpenSource: (documentId: string, span?: { start: number; end: number; text: string }) => void;
};

export default function B5EvidenceSearch({ investigationId, sourceTypes = [], onOpenSource }: B5EvidenceSearchProps) {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<B5SearchMode>("fulltext");
  const [sourceType, setSourceType] = useState("");
  const [sourceId, setSourceId] = useState("");
  const [language, setLanguage] = useState("");
  const [publishedAfter, setPublishedAfter] = useState("");
  const [publishedBefore, setPublishedBefore] = useState("");
  const [collectedAfter, setCollectedAfter] = useState("");
  const [collectedBefore, setCollectedBefore] = useState("");
  const [includeUnknownDates, setIncludeUnknownDates] = useState(false);
  const [includeLeads, setIncludeLeads] = useState(false);
  const [offset, setOffset] = useState(0);
  const [results, setResults] = useState<B5EvidenceResult[]>([]);
  const [total, setTotal] = useState(0);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [meta, setMeta] = useState<{ fallback: boolean; pending: boolean; complete: boolean; limitations: string[] }>({ fallback: false, pending: false, complete: true, limitations: [] });
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    if (!query.trim()) {
      setResults([]);
      setTotal(0);
      setNextOffset(null);
      setMeta({ fallback: false, pending: false, complete: true, limitations: [] });
      setError(null);
      setLoading(false);
      setOffset(0);
      return () => { current = false; controller.abort(); };
    }
    setLoading(true);
    setError(null);
    void searchInvestigationEvidence(investigationId, {
      q: query,
      mode,
      sourceType: sourceType || undefined,
      sourceId: sourceId || undefined,
      language: language || undefined,
      publishedAfter: publishedAfter || undefined,
      publishedBefore: publishedBefore || undefined,
      collectedAfter: collectedAfter || undefined,
      collectedBefore: collectedBefore || undefined,
      includeUnknownDates,
      includeLeads,
      limit: 25,
      offset,
      signal: controller.signal,
    }).then((response) => {
      if (!current) return;
      setResults((previous) => offset ? [...previous, ...response.results] : response.results);
      setTotal(response.total);
      setNextOffset(response.next_offset);
      setMeta({ fallback: response.fallback_active, pending: response.pending, complete: response.complete, limitations: response.limitations });
    }).catch((reason: unknown) => {
      if (!current || (reason instanceof DOMException && reason.name === "AbortError")) return;
      setError(reason instanceof ApiError ? reason.message : "Evidence search is unavailable right now.");
      setResults([]);
    }).finally(() => {
      if (current) setLoading(false);
    });
    return () => {
      current = false;
      controller.abort();
    };
  }, [investigationId, query, mode, sourceType, sourceId, language, publishedAfter, publishedBefore, collectedAfter, collectedBefore, includeUnknownDates, includeLeads, offset]);

  useEffect(() => {
    setOffset(0);
  }, [investigationId, query, mode, sourceType, sourceId, language, publishedAfter, publishedBefore, collectedAfter, collectedBefore, includeUnknownDates, includeLeads]);

  const sourceTypeOptions = useMemo(() => Array.from(new Set(sourceTypes)).sort(), [sourceTypes]);

  return (
    <section aria-labelledby="b5-evidence-search-title" className="workspace-panel p-5 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="eyebrow">Evidence search</p>
          <h3 id="b5-evidence-search-title" className="mt-2 text-xl font-semibold text-[var(--ink)]">Search the canonical evidence set</h3>
        </div>
        <span aria-live="polite" className="text-sm text-[var(--muted)]">{loading ? "Searching…" : `${total} result${total === 1 ? "" : "s"}`}</span>
      </div>
      <div className="mt-5 grid gap-3 md:grid-cols-[minmax(0,1fr)_10rem_10rem]">
        <label className="text-sm font-semibold text-[var(--ink)]">Query
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search claims, phrases, or source text" className="mt-2 w-full rounded-xl border border-[var(--border)] bg-white px-3 py-2.5 font-normal outline-none focus:border-[var(--accent)] focus:ring-2 focus:ring-[var(--accent-soft)]" />
        </label>
        <label className="text-sm font-semibold text-[var(--ink)]">Mode
          <select value={mode} onChange={(event) => setMode(event.target.value as B5SearchMode)} className="mt-2 w-full rounded-xl border border-[var(--border)] bg-white px-3 py-2.5 font-normal">
            <option value="fulltext">Full text</option><option value="phrase">Exact phrase</option><option value="semantic">Semantic</option><option value="hybrid">Hybrid</option>
          </select>
        </label>
        {sourceTypeOptions.length ? <label className="text-sm font-semibold text-[var(--ink)]">Source type
          <select value={sourceType} onChange={(event) => setSourceType(event.target.value)} className="mt-2 w-full rounded-xl border border-[var(--border)] bg-white px-3 py-2.5 font-normal"><option value="">All types</option>{sourceTypeOptions.map((type) => <option key={type} value={type}>{type.replaceAll("_", " ")}</option>)}</select>
        </label> : null}
      </div>
      <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <label className="text-sm font-semibold text-[var(--ink)]">Source ID<input value={sourceId} onChange={(event) => setSourceId(event.target.value)} placeholder="Optional source ID" className="mt-2 w-full rounded-xl border border-[var(--border)] bg-white px-3 py-2 font-normal" /></label>
        <label className="text-sm font-semibold text-[var(--ink)]">Language<input value={language} onChange={(event) => setLanguage(event.target.value)} placeholder="e.g. en" className="mt-2 w-full rounded-xl border border-[var(--border)] bg-white px-3 py-2 font-normal" /></label>
        <label className="text-sm font-semibold text-[var(--ink)]">Published after<input type="date" value={publishedAfter} onChange={(event) => setPublishedAfter(event.target.value)} className="mt-2 w-full rounded-xl border border-[var(--border)] bg-white px-3 py-2 font-normal" /></label>
        <label className="text-sm font-semibold text-[var(--ink)]">Published before<input type="date" value={publishedBefore} onChange={(event) => setPublishedBefore(event.target.value)} className="mt-2 w-full rounded-xl border border-[var(--border)] bg-white px-3 py-2 font-normal" /></label>
        <label className="text-sm font-semibold text-[var(--ink)]">Collected after<input type="date" value={collectedAfter} onChange={(event) => setCollectedAfter(event.target.value)} className="mt-2 w-full rounded-xl border border-[var(--border)] bg-white px-3 py-2 font-normal" /></label>
        <label className="text-sm font-semibold text-[var(--ink)]">Collected before<input type="date" value={collectedBefore} onChange={(event) => setCollectedBefore(event.target.value)} className="mt-2 w-full rounded-xl border border-[var(--border)] bg-white px-3 py-2 font-normal" /></label>
      </div>
      <div className="mt-4 flex flex-wrap gap-4 text-sm text-[var(--muted)]">
        <label className="inline-flex items-center gap-2"><input type="checkbox" checked={includeUnknownDates} onChange={(event) => setIncludeUnknownDates(event.target.checked)} />Include unknown dates</label>
        <label className="inline-flex items-center gap-2"><input type="checkbox" checked={includeLeads} onChange={(event) => setIncludeLeads(event.target.checked)} />Include lead records</label>
      </div>
      {meta.fallback ? <p role="status" className="mt-4 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-950">Search is using the deterministic fallback index.</p> : null}
      {meta.pending ? <p role="status" className="mt-4 rounded-xl border border-sky-200 bg-sky-50 px-3 py-2 text-sm text-sky-950">The canonical index is still updating; results may be incomplete.</p> : null}
      {error ? <p role="alert" className="mt-4 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-950">{error}</p> : null}
      {meta.limitations.length ? <p className="mt-4 text-xs leading-5 text-[var(--muted)]">{meta.limitations.slice(0, 2).join(" ")}</p> : null}
      <div className="mt-5 space-y-3" aria-live="polite">
        {results.map((result) => <EvidenceResultCard key={`${result.document_id}-${result.revision}`} result={result} onOpenSource={onOpenSource} />)}
        {!loading && !error && !results.length ? <p className="rounded-xl border border-dashed border-[var(--border)] p-4 text-sm text-[var(--muted)]">No canonical evidence matched this search.</p> : null}
      </div>
      {nextOffset !== null ? <button type="button" className="mt-5 rounded-xl border border-[var(--border)] bg-white px-4 py-2 text-sm font-semibold text-[var(--ink)] hover:border-[var(--accent)]" onClick={() => setOffset(nextOffset)} aria-label="More evidence results are available">More results available</button> : null}
      {!meta.complete ? <p className="mt-3 text-xs text-[var(--muted)]">This result set is still being assembled.</p> : null}
    </section>
  );
}

function EvidenceResultCard({ result, onOpenSource }: { result: B5EvidenceResult; onOpenSource: (documentId: string, span?: { start: number; end: number; text: string }) => void }) {
  return <article className="rounded-2xl border border-[var(--border)] bg-white/80 p-4">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><p className="text-xs font-bold uppercase tracking-[0.12em] text-[var(--muted)]">{result.source_name}</p><h4 className="mt-2 font-semibold leading-6 text-[var(--ink)]">{result.title}</h4></div>
      <span className="text-xs font-semibold text-[var(--muted)]">Score {Math.round(result.score * 100)}%</span>
    </div>
    {result.spans.length ? <div className="mt-3 flex flex-wrap gap-2">{result.spans.slice(0, 4).map((span) => <mark key={`${span.start}-${span.end}`} className="rounded bg-[var(--accent-soft)] px-1.5 py-1 text-sm text-[var(--ink)]">{span.text}</mark>)}</div> : null}
    <div className="mt-4 flex flex-wrap items-center justify-between gap-3"><a href={result.url} target="_blank" rel="noreferrer" className="max-w-full truncate text-xs text-[var(--muted)] underline underline-offset-4">{result.url}</a><button type="button" onClick={() => onOpenSource(result.document_id, result.spans[0])} className="rounded-lg bg-[var(--ink)] px-3 py-2 text-xs font-semibold text-white hover:bg-[var(--accent)] focus-visible:outline-2 focus-visible:outline-[var(--accent)]">Open source</button></div>
  </article>;
}
