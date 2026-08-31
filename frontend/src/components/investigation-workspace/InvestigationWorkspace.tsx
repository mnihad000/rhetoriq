import { lazy, Suspense, useDeferredValue, useEffect, useMemo, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import InvestigationFlowchart from "../investigation-flowchart/InvestigationFlowchart";
import {
  buildInvestigationExperienceFromWorkspace,
  getClaimLedgerEntries,
  getOpenGaps,
  getPassHistory,
  getRecommendedChecks,
  getResolvedGaps,
  getRetryHistory,
  getSourceDiversityCaveat,
  getSourceDiversityFindings,
} from "../../lib/liveInvestigation";
import type {
  LiveClaimEvidenceVerification,
  LiveClaimVerificationRecord,
  LiveDocument,
  LiveFinalReportClaim,
  LiveInvestigationWorkspace,
} from "../../types/rhetoriq";

const ResearchConsole = lazy(() => import("../research-console/ResearchConsole"));

const VIEWS = [
  ["report", "Report"],
  ["evidence", "Evidence"],
  ["narrative", "Narrative"],
  ["method", "Method & audit"],
] as const;

type ViewId = (typeof VIEWS)[number][0];
type SourceStance = "all" | "supporting" | "counter";

type InvestigationWorkspaceProps = {
  workspace: LiveInvestigationWorkspace;
  isLoading?: boolean;
  isReverifying: boolean;
  onReverify: () => void;
};

export default function InvestigationWorkspace({
  workspace,
  isLoading = false,
  isReverifying,
  onReverify,
}: InvestigationWorkspaceProps) {
  const [searchParams, setSearchParams] = useSearchParams();
  const viewParam = searchParams.get("view");
  const selectedView: ViewId = isViewId(viewParam) ? viewParam : "report";
  const sourceId = searchParams.get("source");
  const source = workspace.retrieved_documents.find((item) => item.id === sourceId) ?? null;

  function updateParams(next: Record<string, string | null>) {
    const params = new URLSearchParams(searchParams);
    Object.entries(next).forEach(([key, value]) => {
      if (value) params.set(key, value);
      else params.delete(key);
    });
    setSearchParams(params, { replace: true });
  }

  return (
    <section aria-label="Investigation workspace" className="space-y-6">
      <WorkspaceNavigation
        activeView={selectedView}
        isRunning={isLoading}
        onSelect={(view) => updateParams({ view, source: null })}
      />

      {selectedView === "report" ? (
        <ReportView workspace={workspace} onOpenSource={(id) => updateParams({ source: id })} />
      ) : null}
      {selectedView === "evidence" ? (
        <EvidenceView workspace={workspace} onOpenSource={(id) => updateParams({ source: id })} />
      ) : null}
      {selectedView === "narrative" ? (
        <NarrativeView
          workspace={workspace}
          isLoading={isLoading}
          onOpenSource={(id) => updateParams({ source: id })}
        />
      ) : null}
      {selectedView === "method" ? (
        <MethodView workspace={workspace} isReverifying={isReverifying} onReverify={onReverify} />
      ) : null}

      <SourceDrawer source={source} onClose={() => updateParams({ source: null })} />
      <p aria-live="polite" className="sr-only">
        {isLoading ? "Investigation is updating." : "Investigation workspace updated."}
      </p>
    </section>
  );
}

function WorkspaceNavigation({
  activeView,
  isRunning,
  onSelect,
}: {
  activeView: ViewId;
  isRunning: boolean;
  onSelect: (view: ViewId) => void;
}) {
  return (
    <div className="sticky top-3 z-20 rounded-[1.4rem] border border-[var(--border)] bg-white/90 p-2 shadow-[0_18px_45px_-35px_rgba(19,35,58,0.5)] backdrop-blur-xl">
      <div aria-label="Investigation views" role="tablist" className="flex flex-wrap gap-1">
        {VIEWS.map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={activeView === id}
            onClick={() => onSelect(id)}
            className={`rounded-xl px-4 py-2.5 text-sm font-semibold transition focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] ${
              activeView === id
                ? "bg-[var(--ink)] text-white"
                : "text-[var(--muted)] hover:bg-[var(--accent-soft)] hover:text-[var(--ink)]"
            }`}
          >
            {label}
          </button>
        ))}
        {isRunning ? <span className="ml-auto inline-flex items-center gap-2 px-3 text-xs font-semibold text-[var(--muted)]"><span className="h-2 w-2 animate-pulse rounded-full bg-[var(--accent)]" />Updating</span> : null}
      </div>
    </div>
  );
}

function ReportView({ workspace, onOpenSource }: { workspace: LiveInvestigationWorkspace; onOpenSource: (id: string) => void }) {
  const report = workspace.report;
  const claims = (report?.key_claims ?? []).slice(0, 5);
  const checks = getRecommendedChecks(workspace);
  const dimensions = report?.confidence_dimensions;

  if (!report) {
    return <PendingReport workspace={workspace} />;
  }

  return (
    <div role="tabpanel" className="grid gap-6 xl:grid-cols-[minmax(0,1.2fr)_20rem]">
      <div className="space-y-6">
        <article className="workspace-panel p-7 sm:p-9">
          <p className="eyebrow">Investigation conclusion</p>
          <h2 className="mt-5 text-3xl font-semibold tracking-[-0.045em] text-[var(--ink)] sm:text-4xl">{report.sections.headline || report.report_title}</h2>
          <p className="mt-5 max-w-3xl text-lg leading-8 text-[var(--ink)]">{report.report_summary || report.sections.executive_summary}</p>
          <div className="mt-7 border-t border-[var(--border)] pt-6">
            <p className="text-[0.7rem] font-bold uppercase tracking-[0.18em] text-[var(--muted)]">What the evidence supports</p>
            <p className="mt-3 max-w-3xl text-base leading-7 text-[var(--muted)]">{report.sections.observed_facts}</p>
          </div>
          {report.sections.reasonable_inferences ? <details className="mt-6 rounded-2xl border border-[var(--border)] bg-white/70 p-4"><summary className="cursor-pointer font-semibold text-[var(--ink)]">Reasonable inferences and boundaries</summary><p className="mt-3 text-sm leading-7 text-[var(--muted)]">{report.sections.reasonable_inferences}</p></details> : null}
        </article>

        <section aria-labelledby="key-claims-heading" className="space-y-3">
          <div className="flex items-end justify-between gap-4 px-1"><div><p className="eyebrow">Key claims</p><h2 id="key-claims-heading" className="mt-3 text-2xl font-semibold text-[var(--ink)]">A concise path to the evidence</h2></div><span className="text-sm text-[var(--muted)]">{report.key_claims.length} claims assessed</span></div>
          {claims.length ? claims.map((claim) => <ClaimCard key={claim.claim_id} claim={claim} onOpenSource={onOpenSource} />) : <EmptyCard message="No report claims are available yet." />}
        </section>
      </div>
      <aside className="space-y-4 xl:pt-2">
        <ConfidenceCard label={report.confidence_label} score={report.confidence_score} />
        {dimensions ? <details className="workspace-panel p-5"><summary className="cursor-pointer text-sm font-semibold text-[var(--ink)]">How confidence was assessed</summary><div className="mt-4 space-y-3">{Object.entries(dimensions).map(([name, value]) => <MetricBar key={name} label={name.replaceAll("_", " ")} value={value.score} />)}</div></details> : null}
        {report.limitations.length ? <InfoCard title="Important limitations">{report.limitations.slice(0, 4).map((item) => <p key={item}>{item}</p>)}</InfoCard> : null}
        {checks.length ? <InfoCard title="Recommended human checks">{checks.slice(0, 4).map((item) => <p key={item}>{item}</p>)}</InfoCard> : null}
        <details className="workspace-panel p-5"><summary className="cursor-pointer text-sm font-semibold text-[var(--ink)]">Investigation brief</summary><p className="mt-3 text-sm leading-6 text-[var(--muted)]">{workspace.plan?.primary_question ?? workspace.query_text}</p><p className="mt-3 text-xs font-semibold uppercase tracking-[0.14em] text-[var(--muted)]">{workspace.plan?.retrieval_mode ?? "live"} retrieval · {workspace.retrieved_documents.length} documents</p></details>
      </aside>
    </div>
  );
}

function EvidenceView({ workspace, onOpenSource }: { workspace: LiveInvestigationWorkspace; onOpenSource: (id: string) => void }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const query = searchParams.get("evidenceQuery") ?? "";
  const sourceType = searchParams.get("sourceType") ?? "all";
  const stance = (searchParams.get("stance") as SourceStance | null) ?? "all";
  const deferredQuery = useDeferredValue(query.trim().toLocaleLowerCase());
  const sources = useMemo(() => getSources(workspace), [workspace]);
  const types = useMemo(() => Array.from(new Set(sources.map((item) => item.source_type))).sort(), [sources]);
  const filteredSources = sources.filter((source) => {
    const searchText = `${source.source_name} ${source.title} ${source.snippet ?? ""} ${source.source_profile?.institution_kind ?? ""}`.toLowerCase();
    return (!deferredQuery || searchText.includes(deferredQuery)) && (sourceType === "all" || source.source_type === sourceType) && (stance === "all" || getSourceStance(workspace, source.id) === stance);
  });
  const claims = workspace.report?.key_claims ?? [];

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(searchParams);
    if (!value || value === "all") next.delete(key);
    else next.set(key, value);
    setSearchParams(next, { replace: true });
  }

  return <div role="tabpanel" className="space-y-6">
    <header className="workspace-panel p-7 sm:p-8"><p className="eyebrow">Evidence library</p><h2 className="mt-4 text-3xl font-semibold tracking-[-0.04em] text-[var(--ink)]">Trace every claim back to a source.</h2><p className="mt-3 max-w-3xl text-base leading-7 text-[var(--muted)]">Filter the source set without losing the distinction between supporting and counter evidence.</p></header>
    <section className="workspace-panel p-5"><div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_12rem_11rem]"><label className="text-sm font-semibold text-[var(--ink)]">Search sources<input value={query} onChange={(event) => setFilter("evidenceQuery", event.target.value)} placeholder="Source, title, or evidence" className="mt-2 w-full rounded-xl border border-[var(--border)] bg-white px-3 py-2.5 text-sm font-normal outline-none transition focus:border-[var(--accent)] focus:ring-2 focus:ring-[var(--accent-soft)]" /></label><label className="text-sm font-semibold text-[var(--ink)]">Source type<select value={sourceType} onChange={(event) => setFilter("sourceType", event.target.value)} className="mt-2 w-full rounded-xl border border-[var(--border)] bg-white px-3 py-2.5 text-sm font-normal"><option value="all">All types</option>{types.map((type) => <option key={type} value={type}>{type.replaceAll("_", " ")}</option>)}</select></label><label className="text-sm font-semibold text-[var(--ink)]">Evidence stance<select value={stance} onChange={(event) => setFilter("stance", event.target.value)} className="mt-2 w-full rounded-xl border border-[var(--border)] bg-white px-3 py-2.5 text-sm font-normal"><option value="all">All evidence</option><option value="supporting">Supporting</option><option value="counter">Counter</option></select></label></div><div className="mt-4 flex items-center justify-between gap-3 text-sm text-[var(--muted)]"><span>{filteredSources.length} of {sources.length} sources shown</span>{query || sourceType !== "all" || stance !== "all" ? <button type="button" onClick={() => { const next = new URLSearchParams(searchParams); ["evidenceQuery", "sourceType", "stance"].forEach((key) => next.delete(key)); setSearchParams(next, { replace: true }); }} className="font-semibold text-[var(--ink)] underline underline-offset-4">Clear filters</button> : null}</div></section>
    <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(19rem,.75fr)]"><section className="space-y-3"><h3 className="px-1 text-lg font-semibold text-[var(--ink)]">Claims and receipts</h3>{claims.length ? claims.map((claim) => <ClaimCard key={claim.claim_id} claim={claim} onOpenSource={onOpenSource} showEvidence />) : <EmptyCard message="Claims will appear after the report has been assembled." />}</section><section className="space-y-3"><h3 className="px-1 text-lg font-semibold text-[var(--ink)]">Sources</h3>{filteredSources.length ? filteredSources.slice(0, 12).map((source) => <SourceCard key={source.id} source={source} stance={getSourceStance(workspace, source.id)} onOpen={() => onOpenSource(source.id)} />) : <EmptyCard message="No sources match these filters. Try clearing one or more filters." />}</section></div>
  </div>;
}

function NarrativeView({ workspace, isLoading, onOpenSource }: { workspace: LiveInvestigationWorkspace; isLoading: boolean; onOpenSource: (id: string) => void }) {
  const events = workspace.timeline?.timeline_events ?? [];
  return <div role="tabpanel" className="space-y-6"><header className="workspace-panel p-7 sm:p-8"><p className="eyebrow">Narrative trace</p><h2 className="mt-4 text-3xl font-semibold tracking-[-0.04em] text-[var(--ink)]">How the narrative developed.</h2><p className="mt-3 max-w-3xl text-base leading-7 text-[var(--muted)]">The provenance path is limited to what was observed in this dataset; it is not a claim about the whole web.</p></header><InvestigationFlowchart data={buildInvestigationExperienceFromWorkspace(workspace).flowchartData} isLoading={isLoading} /><section className="workspace-panel p-6"><div className="flex flex-wrap items-center justify-between gap-3"><div><p className="eyebrow">Timeline</p><h3 className="mt-3 text-xl font-semibold text-[var(--ink)]">Observed events</h3></div><span className="text-sm text-[var(--muted)]">{events.length} recorded</span></div><div className="mt-5 space-y-3">{events.length ? events.slice(0, 12).map((event) => <button key={event.id} type="button" onClick={() => onOpenSource(event.document_id)} className="block w-full rounded-xl border border-[var(--border)] bg-white p-4 text-left transition hover:border-[var(--accent)] focus-visible:outline-2 focus-visible:outline-[var(--accent)]"><div className="flex flex-wrap justify-between gap-2"><span className="font-semibold text-[var(--ink)]">{event.title}</span><span className="text-xs font-semibold uppercase tracking-[0.12em] text-[var(--muted)]">{formatDate(event.timestamp)}</span></div><p className="mt-2 text-sm leading-6 text-[var(--muted)]">{event.explanation}</p></button>) : <EmptyCard message="A timeline will appear as evidence resolves." />}</div></section></div>;
}

function MethodView({ workspace, isReverifying, onReverify }: { workspace: LiveInvestigationWorkspace; isReverifying: boolean; onReverify: () => void }) {
  const gaps = getOpenGaps(workspace);
  const resolvedGaps = getResolvedGaps(workspace);
  const passHistory = getPassHistory(workspace);
  const retries = getRetryHistory(workspace);
  const diversity = getSourceDiversityFindings(workspace);
  const diversityCaveat = getSourceDiversityCaveat(workspace.source_diversity);
  const ledger = getClaimLedgerEntries(workspace);
  return <div role="tabpanel" className="space-y-6"><header className="workspace-panel p-7 sm:p-8"><p className="eyebrow">Method & audit</p><h2 className="mt-4 text-3xl font-semibold tracking-[-0.04em] text-[var(--ink)]">Inspect the process without crowding the report.</h2><p className="mt-3 max-w-3xl text-base leading-7 text-[var(--muted)]">Runtime artifacts and verification data are preserved for review; these details inform confidence but do not replace the report.</p></header>{workspace.research_run ? <Suspense fallback={<div className="h-64 animate-pulse rounded-3xl border border-[var(--border)] bg-white/60" />}><ResearchConsole investigationId={workspace.investigation_id} initialRun={workspace.research_run} /></Suspense> : null}<div className="grid gap-5 lg:grid-cols-2"><AuditCard verification={workspace.claim_verification ?? null} isReverifying={isReverifying} onReverify={onReverify} /><InfoCard title="Evidence gaps">{[...gaps, ...resolvedGaps].length ? [...gaps, ...resolvedGaps].slice(0, 8).map((gap) => <p key={gap.gap_id}><strong>{gap.status === "open" ? "Open" : "Resolved"}:</strong> {gap.summary}</p>) : <p>No evidence gaps were recorded.</p>}</InfoCard><InfoCard title="Provenance">{workspace.provenance_trace ? <><p>{workspace.provenance_trace.earliest_anchor_summary}</p>{workspace.provenance_trace.likely_upstream_source ? <p><strong>Likely upstream:</strong> {workspace.provenance_trace.likely_upstream_source}</p> : null}</> : <p>Provenance data is not available for this run.</p>}</InfoCard><InfoCard title="Research passes">{passHistory.length ? passHistory.map((pass) => <p key={pass.pass_number}>Pass {pass.pass_number}: {pass.lanes_run.join(", ")} · {pass.gaps_opened.length} gaps opened</p>) : <p>No completed research passes yet.</p>}{retries.slice(0, 3).map((retry, index) => <p key={`${retry.pass_number}-${index}`}><strong>Retry:</strong> {retry.reason}</p>)}</InfoCard><InfoCard title="Source diversity">{diversity.length ? diversity.slice(0, 5).map((item) => <p key={item.id}><strong>{item.label}:</strong> {item.detail}</p>) : <p>No source-diversity findings are available.</p>}{diversityCaveat ? <p className="border-t border-[var(--border)] pt-3 text-xs leading-5 text-[var(--muted)]">{diversityCaveat}</p> : null}</InfoCard><InfoCard title="Claim ledger">{ledger.length ? ledger.slice(0, 8).map((entry) => <p key={entry.claim_id}><strong>{entry.state.replaceAll("_", " ")}:</strong> {entry.claim_text}</p>) : <p>No claim ledger is available.</p>}</InfoCard></div>{workspace.agent_debate ? <InfoCard title="Agent debate"><p><strong>Analyst:</strong> {workspace.agent_debate.analyst_position}</p><p><strong>Skeptic:</strong> {workspace.agent_debate.skeptic_response}</p><p><strong>Decision:</strong> {workspace.agent_debate.final_language_decision}</p></InfoCard> : null}</div>;
}

function PendingReport({ workspace }: { workspace: LiveInvestigationWorkspace }) { return <div role="tabpanel" className="workspace-panel p-8 sm:p-10"><p className="eyebrow">Investigation in progress</p><h2 className="mt-5 text-3xl font-semibold tracking-[-0.04em] text-[var(--ink)]">The report will appear when evidence is ready.</h2><p className="mt-4 max-w-2xl text-base leading-7 text-[var(--muted)]">RhetoriQ is gathering sources, checking gaps, and preparing a report that remains clear about its limits. You can follow the live runtime in Method & audit.</p><div className="mt-7 grid gap-3 sm:grid-cols-3"><MetricMini label="Documents" value={`${workspace.retrieved_documents.length}`} /><MetricMini label="Current stage" value={workspace.current_stage.replaceAll("_", " ")} /><MetricMini label="Status" value={workspace.status.replaceAll("_", " ")} /></div></div>; }

function ClaimCard({ claim, onOpenSource, showEvidence = false }: { claim: LiveFinalReportClaim; onOpenSource: (id: string) => void; showEvidence?: boolean }) { const primary = claim.supporting_receipts[0] ?? claim.citations[0]; return <article className="workspace-panel p-5"><div className="flex flex-wrap items-start justify-between gap-3"><StatusBadge value={claim.verification?.disposition ?? claim.support_status ?? "unresolved"} /><span className="text-sm font-semibold text-[var(--muted)]">{Math.round(claim.confidence_score * 100)}% confidence</span></div><h3 className="mt-4 text-lg font-semibold leading-7 text-[var(--ink)]">{claim.claim_text}</h3>{claim.support_summary ? <p className="mt-3 text-sm leading-6 text-[var(--muted)]">{claim.support_summary}</p> : null}{claim.counterpoint_summary ? <p className="mt-3 rounded-xl bg-[rgba(176,106,91,0.08)] p-3 text-sm leading-6 text-[var(--ink)]"><strong>Counterpoint:</strong> {claim.counterpoint_summary}</p> : null}{showEvidence && primary ? <button type="button" onClick={() => onOpenSource(primary.document_id)} className="mt-4 text-sm font-semibold text-[var(--ink)] underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-[var(--accent)]">View primary receipt →</button> : null}{showEvidence && claim.missing_evidence_notes.length ? <p className="mt-4 text-xs leading-5 text-[var(--muted)]"><strong>Missing evidence:</strong> {claim.missing_evidence_notes.join(" ")}</p> : null}</article>; }

function AuditCard({ verification, isReverifying, onReverify }: { verification: LiveInvestigationWorkspace["claim_verification"] | null; isReverifying: boolean; onReverify: () => void }) { return <InfoCard title="Claim-evidence audit"><div className="flex flex-wrap items-center justify-between gap-3"><p>{verification ? `${Math.round(verification.confidence_score * 100)}% aggregate verification confidence.` : "This report predates A3 verification."}</p><button type="button" onClick={onReverify} disabled={isReverifying} className="rounded-xl border border-[var(--border)] bg-white px-3 py-2 text-xs font-semibold text-[var(--ink)] transition hover:border-[var(--accent)] disabled:opacity-60">{isReverifying ? "Re-verifying…" : verification ? "Re-verify evidence" : "Create A3 audit"}</button></div>{verification?.records.map((record) => <AuditRecord key={record.claim_id} record={record} />)}</InfoCard>; }

function AuditRecord({ record }: { record: LiveClaimVerificationRecord }) { return <details className="rounded-xl border border-[var(--border)] bg-white/70 p-3"><summary className="cursor-pointer text-sm font-semibold leading-6 text-[var(--ink)]"><StatusBadge value={record.disposition} /> <span className="ml-2">{record.claim_text}</span></summary><p className="mt-3 text-sm leading-6 text-[var(--muted)]">{record.summary}</p><div className="mt-3 space-y-2">{[...record.supporting_evidence, ...record.contradicting_evidence].map((evidence) => <EvidenceSpan key={`${evidence.document_id}-${evidence.span_start}-${evidence.evidence_side}`} evidence={evidence} />)}</div></details>; }

function EvidenceSpan({ evidence }: { evidence: LiveClaimEvidenceVerification }) { return <div className="rounded-lg bg-[var(--page-deep)] p-3"><p className="text-[0.65rem] font-bold uppercase tracking-[0.12em] text-[var(--muted)]">{evidence.evidence_side} · {evidence.nli_verdict} · {Math.round(evidence.confidence_score * 100)}%</p><p className="mt-2 text-sm leading-6 text-[var(--ink)]">“{evidence.evidence_span}”</p><p className="mt-2 text-xs text-[var(--muted)]">{evidence.source_intelligence.source_role.replaceAll("_", " ")} · {evidence.source_intelligence.registrable_domain ?? "domain unavailable"} · independent group {evidence.source_intelligence.independence_group}</p></div>; }

function SourceDrawer({ source, onClose }: { source: LiveDocument | null; onClose: () => void }) { const closeButton = useRef<HTMLButtonElement>(null); useEffect(() => { if (!source) return; const previous = document.activeElement as HTMLElement | null; closeButton.current?.focus(); const onKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); }; window.addEventListener("keydown", onKeyDown); return () => { window.removeEventListener("keydown", onKeyDown); previous?.focus(); }; }, [source, onClose]); if (!source) return null; return <div role="presentation" className="fixed inset-0 z-50 bg-[rgba(19,35,58,0.35)] p-3 sm:p-6" onMouseDown={onClose}><aside role="dialog" aria-modal="true" aria-labelledby="source-detail-title" onMouseDown={(event) => event.stopPropagation()} className="ml-auto flex h-full max-w-xl flex-col rounded-[1.6rem] bg-white p-6 shadow-2xl sm:p-8"><div className="flex items-start justify-between gap-4"><div><p className="eyebrow">Source detail</p><h2 id="source-detail-title" className="mt-4 text-2xl font-semibold leading-8 text-[var(--ink)]">{source.title}</h2></div><button ref={closeButton} type="button" onClick={onClose} className="rounded-lg p-2 text-lg text-[var(--muted)] hover:bg-[var(--accent-soft)] focus-visible:outline-2 focus-visible:outline-[var(--accent)]" aria-label="Close source detail">×</button></div><dl className="mt-6 grid gap-4 text-sm"><Detail label="Publisher" value={source.source_name} /><Detail label="Published" value={source.published_at ? formatDate(source.published_at) : "Date unavailable"} /><Detail label="Source type" value={source.source_type.replaceAll("_", " ")} /><Detail label="Institution" value={source.source_profile?.institution_kind ?? "Not classified"} /></dl><div className="mt-6 min-h-0 flex-1 overflow-y-auto rounded-xl bg-[var(--page-deep)] p-4"><p className="text-sm leading-7 text-[var(--ink)]">{source.text || source.snippet || "No source text was stored for this document."}</p></div><a href={source.url} target="_blank" rel="noreferrer" className="mt-6 inline-flex justify-center rounded-xl bg-[var(--ink)] px-4 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent)]">Open original source <span className="ml-2" aria-hidden="true">↗</span><span className="sr-only"> (opens in a new tab)</span></a></aside></div>; }

function SourceCard({ source, stance, onOpen }: { source: LiveDocument; stance: SourceStance; onOpen: () => void }) { return <button type="button" onClick={onOpen} className="block w-full rounded-2xl border border-[var(--border)] bg-white/80 p-4 text-left transition hover:border-[var(--accent)] hover:shadow-sm focus-visible:outline-2 focus-visible:outline-[var(--accent)]"><div className="flex flex-wrap items-start justify-between gap-2"><span className="text-xs font-bold uppercase tracking-[0.12em] text-[var(--muted)]">{source.source_name}</span><StatusBadge value={stance === "all" ? "context" : stance} /></div><p className="mt-3 font-semibold leading-6 text-[var(--ink)]">{source.title}</p><p className="mt-2 line-clamp-3 text-sm leading-6 text-[var(--muted)]">{source.snippet ?? "Open source details to review the stored text."}</p></button>; }
function ConfidenceCard({ label, score }: { label: string; score: number }) { return <div className="rounded-[1.4rem] bg-[var(--ink)] p-5 text-white"><p className="text-[0.68rem] font-bold uppercase tracking-[0.16em] text-white/60">Confidence</p><p className="mt-3 text-3xl font-semibold">{Math.round(score * 100)}%</p><p className="mt-2 text-sm capitalize text-white/75">{label} confidence</p></div>; }
function MetricBar({ label, value }: { label: string; value: number }) { return <div><div className="flex justify-between gap-3 text-xs capitalize text-[var(--muted)]"><span>{label}</span><span>{Math.round(value * 100)}%</span></div><div className="mt-2 h-1.5 overflow-hidden rounded-full bg-[var(--page-deep)]"><div className="h-full rounded-full bg-[var(--accent)]" style={{ width: `${Math.min(100, Math.max(0, value * 100))}%` }} /></div></div>; }
function InfoCard({ title, children }: { title: string; children: React.ReactNode }) { return <section className="workspace-panel p-5"><h3 className="text-sm font-semibold text-[var(--ink)]">{title}</h3><div className="mt-4 space-y-3 text-sm leading-6 text-[var(--muted)]">{children}</div></section>; }
function EmptyCard({ message }: { message: string }) { return <div className="rounded-2xl border border-dashed border-[var(--border)] bg-white/65 p-5 text-sm leading-6 text-[var(--muted)]">{message}</div>; }
function MetricMini({ label, value }: { label: string; value: string }) { return <div className="rounded-xl border border-[var(--border)] bg-white/80 p-4"><p className="text-[0.65rem] font-bold uppercase tracking-[0.14em] text-[var(--muted)]">{label}</p><p className="mt-2 text-sm font-semibold capitalize text-[var(--ink)]">{value}</p></div>; }
function StatusBadge({ value }: { value: string }) { const normalized = value.replaceAll("_", " "); const tone = /supported|verified|supporting/.test(value) ? "bg-emerald-100 text-emerald-900" : /contradict|counter|withheld|unresolved|insufficient/.test(value) ? "bg-amber-100 text-amber-950" : "bg-slate-100 text-slate-700"; return <span className={`inline-flex rounded-full px-2.5 py-1 text-[0.63rem] font-bold uppercase tracking-[0.11em] ${tone}`}>{normalized}</span>; }
function Detail({ label, value }: { label: string; value: string }) { return <div><dt className="text-[0.65rem] font-bold uppercase tracking-[0.13em] text-[var(--muted)]">{label}</dt><dd className="mt-1 capitalize text-[var(--ink)]">{value}</dd></div>; }
function getSources(workspace: LiveInvestigationWorkspace) { return workspace.retrieved_documents.filter((source, index, items) => items.findIndex((item) => item.id === source.id) === index); }
function getSourceStance(workspace: LiveInvestigationWorkspace, sourceId: string): SourceStance { const claims = workspace.report?.key_claims ?? []; if (claims.some((claim) => claim.counter_citations.some((citation) => citation.document_id === sourceId) || claim.contradicting_receipts.some((receipt) => receipt.document_id === sourceId))) return "counter"; if (claims.some((claim) => claim.citations.some((citation) => citation.document_id === sourceId) || claim.supporting_receipts.some((receipt) => receipt.document_id === sourceId))) return "supporting"; return "all"; }
function isViewId(value: string | null): value is ViewId { return VIEWS.some(([id]) => id === value); }
function formatDate(value: string) { return new Intl.DateTimeFormat("en-US", { dateStyle: "medium" }).format(new Date(value)); }
