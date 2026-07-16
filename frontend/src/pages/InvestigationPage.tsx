import { startTransition, useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import InvestigationFlowchart from "../components/investigation-flowchart/InvestigationFlowchart";
import Header from "../components/layout/Header";
import { Waves } from "../components/ui/wave-background";
import {
  ApiError,
  getInvestigationWorkspace,
  runInvestigation,
} from "../lib/api";
import {
  buildInvestigationExperienceFromWorkspace,
  getClaimLedgerEntries,
  getOpenGaps,
  getPassHistory,
  getResolvedGaps,
  getRetryHistory,
  getRecommendedChecks,
  getStageLabel,
} from "../lib/liveInvestigation";
import {
  getMockInvestigationWorkspace,
  isMockInvestigationRequest,
} from "../lib/mockInvestigation";
import type { LiveInvestigationWorkspace } from "../types/rhetoriq";

export default function InvestigationPage() {
  const { id = "" } = useParams();
  const [searchParams] = useSearchParams();
  const query = searchParams.get("q") ?? undefined;
  const isMockRequest = isMockInvestigationRequest(id, searchParams);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isNotFound, setIsNotFound] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [workspace, setWorkspace] = useState<LiveInvestigationWorkspace | null>(null);

  useEffect(() => {
    if (!id) {
      setWorkspace(null);
      setErrorMessage(null);
      setIsNotFound(true);
      return;
    }

    let cancelled = false;
    let pollTimer: ReturnType<typeof setTimeout> | null = null;

    function scheduleNextPoll(investigationId: string) {
      pollTimer = setTimeout(async () => {
        if (cancelled) return;
        try {
          const updated = await getInvestigationWorkspace(investigationId);
          if (cancelled) return;
          startTransition(() => setWorkspace(updated));
          if (!updated.research_loop) {
            scheduleNextPoll(investigationId);
          } else {
            setIsRunning(false);
          }
        } catch {
          if (!cancelled) scheduleNextPoll(investigationId);
        }
      }, 8000);
    }

    async function hydrateLiveInvestigation() {
      setErrorMessage(null);
      setIsNotFound(false);

      try {
        if (isMockRequest) {
          const nextWorkspace = getMockInvestigationWorkspace(id, query);
          if (cancelled) return;
          startTransition(() => setWorkspace(nextWorkspace));
          return;
        }

        let nextWorkspace = await getInvestigationWorkspace(id);
        if (cancelled) return;
        startTransition(() => setWorkspace(nextWorkspace));

        if (!nextWorkspace.research_loop) {
          setIsRunning(true);
          // POST /run starts research loop in background and returns immediately
          nextWorkspace = await runInvestigation(id);
          if (cancelled) return;
          startTransition(() => setWorkspace(nextWorkspace));
          // Poll until background thread completes the loop
          if (!nextWorkspace.research_loop) {
            scheduleNextPoll(id);
          } else {
            setIsRunning(false);
          }
        }
      } catch (error) {
        if (!cancelled) {
          setWorkspace(null);
          setIsRunning(false);
          if (error instanceof ApiError && error.status === 404) {
            setIsNotFound(true);
            setErrorMessage(null);
          } else {
            setErrorMessage(
              error instanceof ApiError
                ? error.message
                : "Unable to load the live investigation.",
            );
          }
        }
      }
    }

    void hydrateLiveInvestigation();

    return () => {
      cancelled = true;
      if (pollTimer) clearTimeout(pollTimer);
    };
  }, [id, isMockRequest, query, searchParams]);

  const experience = workspace
    ? buildInvestigationExperienceFromWorkspace(workspace)
    : null;
  const recommendedChecks = workspace ? getRecommendedChecks(workspace) : [];
  const stageLabel = workspace ? getStageLabel(workspace) : null;
  const passHistory = workspace ? getPassHistory(workspace) : [];
  const retryHistory = workspace ? getRetryHistory(workspace) : [];
  const openGaps = workspace ? getOpenGaps(workspace) : [];
  const resolvedGaps = workspace ? getResolvedGaps(workspace) : [];
  const claimLedger = workspace ? getClaimLedgerEntries(workspace) : [];

  return (
    <main className="investigation-page min-h-screen bg-white">
      <div aria-hidden="true" className="pointer-events-none fixed inset-0 z-0">
        <Waves
          backgroundColor="#ffffff"
          className="h-full w-full opacity-100"
          strokeColor="#000000"
        />
      </div>

      <div className="relative z-10">
        <Header />
      </div>

      <section className="relative z-10 px-4 pb-24 pt-8 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-[1440px] space-y-6">
          <Link
            to="/"
            className="inline-flex items-center gap-2 text-sm font-semibold uppercase tracking-[0.2em] text-[var(--muted)] transition hover:text-[var(--accent)]"
          >
            <span aria-hidden="true">{"<"}</span>
            Back to homepage
          </Link>

          {experience ? (
            <div className="investigation-hero page-enter overflow-hidden rounded-[2.2rem] border border-[rgba(19,35,58,0.08)] p-7 shadow-[0_55px_90px_-54px_rgba(19,35,58,0.46)] backdrop-blur-xl sm:p-9">
              <div className="grid gap-7 xl:grid-cols-[minmax(0,1.15fr)_21rem] xl:items-end">
                <div>
                  <p className="eyebrow">{experience.kicker}</p>
                  <h1 className="mt-5 font-[Iowan_Old_Style,Palatino_Linotype,Book_Antiqua,Georgia,serif] text-4xl font-semibold tracking-[-0.05em] text-[var(--ink)] sm:text-5xl lg:text-6xl">
                    {experience.title}
                  </h1>
                  <p className="mt-5 max-w-3xl text-lg leading-8 text-[var(--muted)]">
                    Source-grounded investigation workspace for tracing how a civic
                    narrative spread, branched, and took shape in the available dataset.
                  </p>

                  <div className="mt-6 flex flex-wrap gap-3">
                    <span className="data-pill">{experience.status}</span>
                    <span className="data-pill">{experience.confidence} confidence</span>
                    <span className="data-pill">{experience.sourceCount} sources</span>
                    <span className="data-pill">{experience.receiptCount} receipts</span>
                    {isRunning ? (
                      <span className="data-pill animate-pulse">Researching&hellip;</span>
                    ) : null}
                  </div>

                  <div className="mt-7 rounded-[1.6rem] border border-[rgba(19,35,58,0.08)] bg-white/78 p-5">
                    <p className="text-[0.72rem] font-semibold uppercase tracking-[0.2em] text-[var(--muted)]">
                      User asked
                    </p>
                    <p className="mt-3 text-lg leading-8 text-[var(--ink)]">
                      "{workspace?.query_text ?? query ?? experience.flowchartData.query}"
                    </p>
                  </div>
                </div>

                <div className="grid gap-3">
                  <MetricCard label="Generated" value={experience.generatedAt} />
                  <MetricCard
                    label="First observed in our dataset"
                    value={experience.firstObserved}
                  />
                  <MetricCard label="Receipts available" value={`${experience.receiptCount}`} />
                  <MetricCard
                    label="Current narrative state"
                    value={isRunning ? (stageLabel ?? "Investigating") : (stageLabel ?? experience.status)}
                  />
                </div>
              </div>
            </div>
          ) : isNotFound ? (
            <InvestigationNotFoundCard investigationId={id} />
          ) : (
            <LoadingHero query={query} isRunning={isRunning} />
          )}

          {workspace ? (
            <div className="grid gap-6 xl:grid-cols-[minmax(0,1.15fr)_minmax(18rem,24rem)]">
              <div className="space-y-6">
                <InvestigationBriefCard workspace={workspace} />
                <InvestigationFlowchart
                  data={experience?.flowchartData}
                  isLoading={
                    !experience ||
                    (!isMockRequest &&
                      !workspace?.report &&
                      !workspace?.research_loop)
                  }
                />
                {workspace.research_loop ? (
                  <ResearchLoopCard workspace={workspace} />
                ) : null}
              </div>
              <div className="space-y-6">
                {workspace.agent_debate ? (
                  <AgentDebateCard debate={workspace.agent_debate} />
                ) : null}
                {workspace.provenance_trace ? (
                  <ProvenanceCard workspace={workspace} />
                ) : null}
                {openGaps.length > 0 || resolvedGaps.length > 0 ? (
                  <GapsCard openGaps={openGaps} resolvedGaps={resolvedGaps} />
                ) : null}
                {claimLedger.length > 0 ? (
                  <ClaimLedgerCard entries={claimLedger} />
                ) : null}
                {recommendedChecks.length > 0 ? (
                  <InfoCard title="Recommended Human Checks">
                    {recommendedChecks.map((item) => (
                      <InfoListItem key={item} value={item} />
                    ))}
                  </InfoCard>
                ) : null}
                {passHistory.length > 0 ? (
                  <InfoCard title="Pass History">
                    {passHistory.map((item) => (
                      <InfoListItem
                        key={`pass-${item.pass_number}`}
                        value={`Pass ${item.pass_number}: ${item.lanes_run.join(", ")} | skeptic ${item.skeptic_decision ?? "n/a"} | open gaps ${item.gaps_opened.length}`}
                      />
                    ))}
                    {retryHistory.map((item, index) => (
                      <InfoListItem
                        key={`retry-${item.pass_number}-${index}`}
                        value={`Retry ${item.pass_number}: ${item.lane} because ${item.reason}`}
                      />
                    ))}
                  </InfoCard>
                ) : null}
              </div>
            </div>
          ) : null}

          {!workspace && !experience && errorMessage && !isNotFound ? (
            <div className="rounded-[1.6rem] border border-[rgba(146,71,71,0.18)] bg-[rgba(255,244,244,0.92)] p-6 text-sm leading-7 text-[rgb(130,50,50)]">
              {errorMessage}
            </div>
          ) : null}
        </div>
      </section>
    </main>
  );
}

function LoadingHero({ query, isRunning }: { query?: string; isRunning?: boolean }) {
  const stages = [
    "Retrieving sources",
    "Building timeline",
    "Running counter-narratives",
    "Analyst synthesis",
    "Skeptic review",
    "Verifying sources",
    "Finalizing receipts",
  ];
  const [stageIndex, setStageIndex] = useState(0);

  useEffect(() => {
    if (!isRunning) return;
    const timer = setInterval(() => {
      setStageIndex((i) => (i + 1) % stages.length);
    }, 4500);
    return () => clearInterval(timer);
  }, [isRunning]);

  return (
    <div className="investigation-hero page-enter overflow-hidden rounded-[2.2rem] border border-[rgba(19,35,58,0.08)] p-7 shadow-[0_55px_90px_-54px_rgba(19,35,58,0.46)] backdrop-blur-xl sm:p-9">
      <p className="eyebrow">Live investigation workspace</p>
      <h1 className="mt-5 font-[Iowan_Old_Style,Palatino_Linotype,Book_Antiqua,Georgia,serif] text-4xl font-semibold tracking-[-0.05em] text-[var(--ink)] sm:text-5xl lg:text-6xl">
        {isRunning ? "Investigating" : "Building investigation"}
      </h1>
      <p className="mt-5 max-w-3xl text-lg leading-8 text-[var(--muted)]">
        {isRunning
          ? `Multi-agent research loop running${query ? ` for "${query}"` : ""}. Results update automatically.`
          : query
            ? `Preparing evidence-backed investigation for "${query}".`
            : "Preparing evidence-backed investigation."}
      </p>
      {isRunning ? (
        <div className="mt-6 flex items-center gap-3">
          <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-[var(--accent)]" />
          <span className="text-sm font-medium text-[var(--muted)]">{stages[stageIndex]}&hellip;</span>
        </div>
      ) : null}
    </div>
  );
}

function InvestigationNotFoundCard({
  investigationId,
}: {
  investigationId: string;
}) {
  return (
    <div className="investigation-hero page-enter overflow-hidden rounded-[2.2rem] border border-[rgba(146,71,71,0.18)] bg-[rgba(255,244,244,0.92)] p-7 shadow-[0_55px_90px_-54px_rgba(130,50,50,0.24)] backdrop-blur-xl sm:p-9">
      <p className="eyebrow">Investigation unavailable</p>
      <h1 className="mt-5 font-[Iowan_Old_Style,Palatino_Linotype,Book_Antiqua,Georgia,serif] text-4xl font-semibold tracking-[-0.05em] text-[var(--ink)] sm:text-5xl lg:text-6xl">
        Investigation not found
      </h1>
      <p className="mt-5 max-w-3xl text-lg leading-8 text-[var(--muted)]">
        {`The live investigation "${investigationId}" could not be found. Old seeded demo routes are no longer supported.`}
      </p>
      <div className="mt-7 flex flex-wrap gap-3">
        <Link
          to="/"
          className="inline-flex items-center justify-center rounded-[1.1rem] bg-[var(--ink)] px-5 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent)]"
        >
          Back to homepage
        </Link>
        <Link
          to="/#ask-rhetoriq"
          className="inline-flex items-center justify-center rounded-[1.1rem] border border-[var(--border)] bg-white px-5 py-3 text-sm font-semibold text-[var(--ink)] transition hover:border-[var(--accent)] hover:text-[var(--accent)]"
        >
          Start a new investigation
        </Link>
      </div>
    </div>
  );
}

function InvestigationBriefCard({
  workspace,
}: {
  workspace: LiveInvestigationWorkspace;
}) {
  const plan = workspace.plan;
  if (!plan) {
    return null;
  }

  return (
    <div className="rounded-[2rem] border border-[rgba(19,35,58,0.08)] bg-[rgba(255,255,255,0.86)] p-7 shadow-[0_38px_68px_-46px_rgba(19,35,58,0.4)] backdrop-blur-xl sm:p-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="eyebrow">Investigation brief</p>
          <p className="mt-4 text-lg leading-8 text-[var(--ink)]">
            {plan.primary_question ?? workspace.query_text}
          </p>
          <p className="mt-3 text-sm leading-6 text-[var(--muted)]">
            Intent: {plan.intent} {"·"} Retrieval mode: {plan.retrieval_mode}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {plan.retrieval_lanes.map((lane) => (
            <span key={lane} className="data-pill">
              {lane}
            </span>
          ))}
        </div>
      </div>

      {plan.subquestions.length > 0 ? (
        <div className="mt-6 space-y-3">
          {plan.subquestions.map((item) => (
            <p
              key={item}
              className="rounded-[1rem] border border-[rgba(19,35,58,0.08)] bg-white/90 px-4 py-3 text-sm leading-7 text-[var(--ink)]"
            >
              {item}
            </p>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function ResearchLoopCard({
  workspace,
}: {
  workspace: LiveInvestigationWorkspace;
}) {
  const loop = workspace.research_loop;
  if (!loop) {
    return null;
  }

  return (
    <div className="rounded-[2rem] border border-[rgba(19,35,58,0.08)] bg-[rgba(255,255,255,0.86)] p-7 shadow-[0_38px_68px_-46px_rgba(19,35,58,0.4)] backdrop-blur-xl sm:p-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="eyebrow">Research loop</p>
          <p className="mt-4 text-base leading-7 text-[var(--muted)]">
            Final decision: {loop.final_decision.replaceAll("_", " ")}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <span className="data-pill">{loop.pass_history.length} pass(es)</span>
          <span className="data-pill">{loop.evidence_budget.documents_fetched} docs</span>
          <span className="data-pill">{loop.evidence_budget.retries_used} retries</span>
        </div>
      </div>

      <div className="mt-6 grid gap-3 sm:grid-cols-2">
        <InfoPill value={`Coverage ${Math.round(loop.confidence_dimensions.coverage_confidence.score * 100)}%`} />
        <InfoPill value={`Chronology ${Math.round(loop.confidence_dimensions.chronology_confidence.score * 100)}%`} />
        <InfoPill value={`Contradiction ${Math.round(loop.confidence_dimensions.contradiction_confidence.score * 100)}%`} />
        <InfoPill value={`Provenance ${Math.round(loop.confidence_dimensions.provenance_confidence.score * 100)}%`} />
        <InfoPill value={`Verification ${Math.round(loop.confidence_dimensions.verification_confidence.score * 100)}%`} />
        <InfoPill value={`Synthesis ${Math.round(loop.confidence_dimensions.synthesis_confidence.score * 100)}%`} />
      </div>
    </div>
  );
}

function ProvenanceCard({
  workspace,
}: {
  workspace: LiveInvestigationWorkspace;
}) {
  const provenance = workspace.provenance_trace;
  if (!provenance) {
    return null;
  }

  return (
    <InfoCard title="Provenance">
      <InfoListItem value={provenance.earliest_anchor_summary} />
      {provenance.likely_upstream_source ? (
        <InfoListItem value={`Likely upstream source: ${provenance.likely_upstream_source}`} />
      ) : null}
      {Object.entries(provenance.duplicate_clusters).map(([clusterId, docIds]) => (
        <InfoListItem
          key={clusterId}
          value={`Duplicate cluster ${clusterId}: ${docIds.length} related document(s)`}
        />
      ))}
      {provenance.trace_nodes.slice(0, 4).map((node) => (
        <InfoListItem
          key={`${node.document_id}-${node.role}`}
          value={`${node.role.replaceAll("_", " ")}: ${node.source_name}${node.citation_hint ? ` (${node.citation_hint})` : ""}`}
        />
      ))}
    </InfoCard>
  );
}

function GapsCard({
  openGaps,
  resolvedGaps,
}: {
  openGaps: NonNullable<LiveInvestigationWorkspace["gap_ledger"]>["entries"];
  resolvedGaps: NonNullable<LiveInvestigationWorkspace["gap_ledger"]>["entries"];
}) {
  return (
    <InfoCard title="Evidence Gaps">
      {openGaps.map((gap) => (
        <InfoListItem
          key={gap.gap_id}
          value={`Open ${gap.severity} ${gap.gap_type.replaceAll("_", " ")} gap: ${gap.summary}`}
        />
      ))}
      {resolvedGaps.map((gap) => (
        <InfoListItem
          key={gap.gap_id}
          value={`Resolved ${gap.gap_type.replaceAll("_", " ")} gap${gap.resolved_in_pass ? ` in pass ${gap.resolved_in_pass}` : ""}: ${gap.summary}`}
        />
      ))}
    </InfoCard>
  );
}

function ClaimLedgerCard({
  entries,
}: {
  entries: NonNullable<LiveInvestigationWorkspace["claim_ledger"]>["entries"];
}) {
  return (
    <InfoCard title="Claim Ledger">
      {entries.slice(0, 8).map((entry) => (
        <InfoListItem
          key={entry.claim_id}
          value={`${entry.state.replaceAll("_", " ")}: ${entry.claim_text}`}
        />
      ))}
    </InfoCard>
  );
}

function AgentDebateCard({
  debate,
}: {
  debate: NonNullable<LiveInvestigationWorkspace["agent_debate"]>;
}) {
  return (
    <div className="rounded-[1.7rem] border border-[rgba(19,35,58,0.08)] bg-[rgba(255,255,255,0.88)] p-5 shadow-[0_24px_44px_-34px_rgba(19,35,58,0.34)]">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-[0.72rem] font-semibold uppercase tracking-[0.18em] text-[var(--muted)]">
          Agent debate
        </p>
        <div className="flex flex-wrap gap-2">
          <span className="data-pill">{debate.confidence_label} confidence</span>
        </div>
      </div>

      <div className="mt-4 space-y-4">
        <DebateBlock title="Analyst Agent" value={debate.analyst_position} />
        <DebateBlock title="Skeptic Summary" value={debate.skeptic_response} />
        <DebateBlock title="Receipts Check" value={debate.receipts_check} />
        <DebateBlock title="Counter-Narrative Note" value={debate.counter_narrative_note} />
        <DebateBlock title="Safety / Grounding" value={debate.safety_grounding_decision} />
        <DebateBlock title="Final Language Decision" value={debate.final_language_decision} />

        {debate.rejected_claims.length > 0 ? (
          <div className="rounded-[1.1rem] border border-[rgba(19,35,58,0.08)] bg-white/92 p-4">
            <p className="text-[0.72rem] font-semibold uppercase tracking-[0.18em] text-[var(--muted)]">
              Rejected claims
            </p>
            <div className="mt-3 space-y-2">
              {debate.rejected_claims.map((item) => (
                <p key={item} className="text-sm leading-6 text-[var(--ink)]">
                  {item}
                </p>
              ))}
            </div>
          </div>
        ) : null}

        {debate.softened_claims.length > 0 ? (
          <div className="rounded-[1.1rem] border border-[rgba(19,35,58,0.08)] bg-white/92 p-4">
            <p className="text-[0.72rem] font-semibold uppercase tracking-[0.18em] text-[var(--muted)]">
              Softened claims
            </p>
            <div className="mt-3 space-y-3">
              {debate.softened_claims.map((item) => (
                <div key={item.claim_id} className="rounded-[0.95rem] bg-[rgba(245,247,250,0.9)] p-3">
                  <p className="text-sm font-semibold text-[var(--ink)]">{item.original}</p>
                  <p className="mt-2 text-sm leading-6 text-[var(--muted)]">{item.softened}</p>
                  <p className="mt-2 text-xs uppercase tracking-[0.12em] text-[var(--muted)]">
                    {item.reason}
                  </p>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function DebateBlock({ title, value }: { title: string; value: string }) {
  return (
    <div className="rounded-[1.1rem] border border-[rgba(19,35,58,0.08)] bg-white/92 p-4">
      <p className="text-[0.72rem] font-semibold uppercase tracking-[0.18em] text-[var(--muted)]">
        {title}
      </p>
      <p className="mt-3 text-sm leading-7 text-[var(--ink)]">{value}</p>
    </div>
  );
}

function InfoCard({
  children,
  title,
}: {
  children: React.ReactNode;
  title: string;
}) {
  return (
    <div className="rounded-[1.7rem] border border-[rgba(19,35,58,0.08)] bg-[rgba(255,255,255,0.88)] p-5 shadow-[0_24px_44px_-34px_rgba(19,35,58,0.34)]">
      <p className="text-[0.72rem] font-semibold uppercase tracking-[0.18em] text-[var(--muted)]">
        {title}
      </p>
      <div className="mt-4 space-y-3">{children}</div>
    </div>
  );
}

function InfoPill({ value }: { value: string }) {
  return (
    <div className="rounded-[1rem] border border-[rgba(19,35,58,0.08)] bg-white/90 px-4 py-3 text-sm font-semibold text-[var(--ink)]">
      {value}
    </div>
  );
}

function InfoListItem({ value }: { value: string }) {
  return (
    <p className="rounded-[1rem] border border-[rgba(19,35,58,0.08)] bg-white/90 px-4 py-3 text-sm leading-7 text-[var(--ink)]">
      {value}
    </p>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-[1.45rem] border border-[rgba(19,35,58,0.08)] bg-white/82 p-4 shadow-[0_20px_44px_-34px_rgba(19,35,58,0.34)]">
      <p className="text-[0.68rem] font-semibold uppercase tracking-[0.18em] text-[var(--muted)]">
        {label}
      </p>
      <p className="mt-2 text-sm font-semibold leading-6 text-[var(--ink)]">{value}</p>
    </div>
  );
}
