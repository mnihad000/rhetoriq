import { startTransition, useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import Header from "../components/layout/Header";
import InvestigationWorkspace from "../components/investigation-workspace/InvestigationWorkspace";
import { Waves } from "../components/ui/wave-background";
import { ApiError, getInvestigationWorkspace, getResearchEventsUrl, runInvestigation, verifyInvestigationClaims } from "../lib/api";
import { buildInvestigationExperienceFromWorkspace, getStageLabel } from "../lib/liveInvestigation";
import { getMockInvestigationWorkspace, isMockInvestigationRequest } from "../lib/mockInvestigation";
import type { LiveInvestigationWorkspace } from "../types/rhetoriq";

const WORKSPACE_EVENTS = ["run.started", "node.completed", "action.completed", "artifact.updated", "budget.updated", "gate.evaluated", "run.completed", "run.failed"];

export default function InvestigationPage() {
  const { id = "" } = useParams();
  const [searchParams] = useSearchParams();
  const query = searchParams.get("q") ?? undefined;
  const isMockRequest = isMockInvestigationRequest(id, searchParams);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isNotFound, setIsNotFound] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [isReverifying, setIsReverifying] = useState(false);
  const [workspace, setWorkspace] = useState<LiveInvestigationWorkspace | null>(null);

  useEffect(() => {
    if (!id) { setWorkspace(null); setIsNotFound(true); return; }
    let cancelled = false;
    async function hydrate() {
      setErrorMessage(null); setIsNotFound(false);
      try {
        if (isMockRequest) {
          const mock = getMockInvestigationWorkspace(id, query);
          if (!cancelled) startTransition(() => setWorkspace(mock));
          return;
        }
        let next = await getInvestigationWorkspace(id);
        if (cancelled) return;
        startTransition(() => setWorkspace(next));
        if (!next.research_loop) {
          setIsRunning(true);
          next = await runInvestigation(id);
          if (!cancelled) startTransition(() => setWorkspace(next));
          if (next.research_loop) setIsRunning(false);
        }
      } catch (error) {
        if (cancelled) return;
        setWorkspace(null); setIsRunning(false);
        if (error instanceof ApiError && error.status === 404) { setIsNotFound(true); setErrorMessage(null); }
        else setErrorMessage(error instanceof ApiError ? error.message : "Unable to load the live investigation.");
      }
    }
    void hydrate();
    return () => { cancelled = true; };
  }, [id, isMockRequest, query]);

  useEffect(() => {
    if (!id || isMockRequest || !workspace || workspace.research_loop) return;
    let cancelled = false;
    let refreshTimer: ReturnType<typeof setTimeout> | null = null;
    let fallbackTimer: ReturnType<typeof setInterval> | null = null;
    const refresh = async () => {
      try {
        const next = await getInvestigationWorkspace(id);
        if (cancelled) return;
        startTransition(() => setWorkspace(next));
        if (next.research_loop) setIsRunning(false);
      } catch { /* Stream retries; fallback remains conservative. */ }
    };
    const queueRefresh = () => {
      if (refreshTimer) return;
      refreshTimer = setTimeout(() => { refreshTimer = null; void refresh(); }, 220);
    };
    const source = new EventSource(getResearchEventsUrl(id));
    WORKSPACE_EVENTS.forEach((eventName) => source.addEventListener(eventName, queueRefresh));
    source.onerror = () => { if (!fallbackTimer) fallbackTimer = setInterval(() => void refresh(), 8000); };
    source.onopen = () => { if (fallbackTimer) { clearInterval(fallbackTimer); fallbackTimer = null; } };
    return () => { cancelled = true; source.close(); if (refreshTimer) clearTimeout(refreshTimer); if (fallbackTimer) clearInterval(fallbackTimer); };
  }, [id, isMockRequest, workspace?.research_loop]);

  async function handleReverify() {
    if (!workspace || isReverifying) return;
    setIsReverifying(true); setErrorMessage(null);
    try {
      await verifyInvestigationClaims(workspace.investigation_id);
      const next = await getInvestigationWorkspace(workspace.investigation_id);
      startTransition(() => setWorkspace(next));
    } catch (error) { setErrorMessage(error instanceof ApiError ? error.message : "Claim re-verification failed. Please try again."); }
    finally { setIsReverifying(false); }
  }

  const experience = workspace ? buildInvestigationExperienceFromWorkspace(workspace) : null;
  return <main className="investigation-page min-h-screen bg-white"><div aria-hidden="true" className="pointer-events-none fixed inset-0 z-0"><Waves backgroundColor="#ffffff" className="h-full w-full opacity-100" strokeColor="#000000" /></div><div className="relative z-10"><Header /></div><section className="relative z-10 px-4 pb-24 pt-8 sm:px-6 lg:px-8"><div className="mx-auto max-w-[1240px] space-y-6"><Link to="/dashboard" className="inline-flex items-center gap-2 text-sm font-semibold uppercase tracking-[0.16em] text-[var(--muted)] transition hover:text-[var(--accent)] focus-visible:outline-2 focus-visible:outline-[var(--accent)]"><span aria-hidden="true">←</span>Back to investigations</Link>{experience ? <InvestigationHeader workspace={workspace!} isRunning={isRunning} /> : isNotFound ? <NotFound investigationId={id} /> : <LoadingState query={query} isRunning={isRunning} />}{workspace ? <InvestigationWorkspace workspace={workspace} isLoading={isRunning} isReverifying={isReverifying} onReverify={handleReverify} /> : null}{errorMessage ? <div role="alert" className="rounded-2xl border border-[rgba(146,71,71,0.24)] bg-[rgba(255,244,244,0.95)] p-5 text-sm leading-7 text-[rgb(130,50,50)]">{errorMessage}</div> : null}</div></section></main>;
}

function InvestigationHeader({ workspace, isRunning }: { workspace: LiveInvestigationWorkspace; isRunning: boolean }) {
  const experience = buildInvestigationExperienceFromWorkspace(workspace);
  return <header className="investigation-hero page-enter overflow-hidden rounded-[2.2rem] border border-[rgba(19,35,58,0.08)] p-7 shadow-[0_55px_90px_-54px_rgba(19,35,58,0.46)] backdrop-blur-xl sm:p-9"><div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_17rem] lg:items-end"><div><p className="eyebrow">{isRunning ? "Live investigation" : "Evidence-backed investigation"}</p><h1 className="mt-5 max-w-4xl text-4xl font-semibold tracking-[-0.05em] text-[var(--ink)] sm:text-5xl">{experience.title}</h1><p className="mt-4 max-w-3xl text-lg leading-8 text-[var(--muted)]">{workspace.query_text}</p></div><div className="grid grid-cols-2 gap-3 text-sm"><HeaderMetric label="Confidence" value={`${experience.confidence}`} /><HeaderMetric label="Sources" value={`${experience.sourceCount}`} /><HeaderMetric label="Receipts" value={`${experience.receiptCount}`} /><HeaderMetric label="Status" value={isRunning ? getStageLabel(workspace) : experience.status} /></div></div></header>;
}
function HeaderMetric({ label, value }: { label: string; value: string }) { return <div className="rounded-xl border border-[var(--border)] bg-white/70 p-3"><p className="text-[0.62rem] font-bold uppercase tracking-[0.13em] text-[var(--muted)]">{label}</p><p className="mt-1 text-sm font-semibold capitalize text-[var(--ink)]">{value}</p></div>; }
function LoadingState({ query, isRunning }: { query?: string; isRunning: boolean }) { return <div className="investigation-hero rounded-[2.2rem] border border-[var(--border)] p-8 sm:p-10"><p className="eyebrow">Live investigation workspace</p><h1 className="mt-5 text-4xl font-semibold tracking-[-0.05em] text-[var(--ink)]">{isRunning ? "Investigating" : "Loading investigation"}</h1><p className="mt-4 max-w-2xl text-lg leading-8 text-[var(--muted)]">{query ? `Preparing evidence-backed research for “${query}”.` : "Preparing the evidence workspace."}</p></div>; }
function NotFound({ investigationId }: { investigationId: string }) { return <div className="investigation-hero rounded-[2.2rem] border border-[rgba(146,71,71,0.2)] p-8 sm:p-10"><p className="eyebrow">Investigation unavailable</p><h1 className="mt-5 text-4xl font-semibold tracking-[-0.05em] text-[var(--ink)]">This investigation was not found.</h1><p className="mt-4 max-w-2xl text-lg leading-8 text-[var(--muted)]">The workspace “{investigationId}” may have expired or its link may be incomplete.</p><Link to="/dashboard" className="mt-7 inline-flex rounded-xl bg-[var(--ink)] px-4 py-3 text-sm font-semibold text-white">Browse investigations</Link></div>; }
