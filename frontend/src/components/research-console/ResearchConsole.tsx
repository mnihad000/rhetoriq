import {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { startTransition, useEffect, useMemo, useState } from "react";
import {
  getResearchEventsUrl,
  getResearchTrail,
  replayResearchRun,
} from "../../lib/api";
import type {
  LivePublicationCheck,
  LiveResearchAction,
  LiveResearchRun,
  LiveResearchTrail,
} from "../../types/rhetoriq";

type ResearchConsoleProps = {
  investigationId: string;
  initialRun: LiveResearchRun;
};

type ResearchNodeData = {
  label: string;
  state: "waiting" | "active" | "complete" | "failed";
};

const EVENT_TYPES = [
  "run.started",
  "node.started",
  "node.completed",
  "action.started",
  "action.completed",
  "action.failed",
  "document.normalized",
  "artifact.updated",
  "budget.updated",
  "gate.evaluated",
  "run.completed",
  "run.failed",
];

const GRAPH_NODES = [
  ["initialize_run", "Initialize", 0, 0],
  ["assess_research_state", "Assess gaps", 210, 0],
  ["supervisor_select_action", "Select action", 420, 0],
  ["validate_policy_and_budget", "Policy + budget", 630, 0],
  ["dispatch_action", "Run tool", 630, 170],
  ["normalize_and_persist", "Normalize + receipt", 420, 170],
  ["build_evidence_artifacts", "Build evidence", 210, 170],
  ["skeptic_review", "Skeptic review", 0, 170],
  ["build_candidate_report_and_receipts", "Stage candidate", 0, 340],
  ["publication_gate", "Publication gate", 210, 340],
  ["publish_report", "Publish", 420, 310],
  ["withhold_report", "Withhold", 420, 390],
  ["finalize_insufficient_evidence", "No evidence", 630, 340],
] as const;

const GRAPH_EDGES: Edge[] = [
  ["initialize_run", "assess_research_state"],
  ["assess_research_state", "supervisor_select_action"],
  ["supervisor_select_action", "validate_policy_and_budget"],
  ["validate_policy_and_budget", "dispatch_action"],
  ["dispatch_action", "normalize_and_persist"],
  ["normalize_and_persist", "assess_research_state"],
  ["assess_research_state", "build_evidence_artifacts"],
  ["assess_research_state", "finalize_insufficient_evidence"],
  ["build_evidence_artifacts", "skeptic_review"],
  ["skeptic_review", "supervisor_select_action"],
  ["skeptic_review", "build_candidate_report_and_receipts"],
  ["build_candidate_report_and_receipts", "publication_gate"],
  ["publication_gate", "publish_report"],
  ["publication_gate", "withhold_report"],
].map(([source, target], index) => ({
  id: `research-edge-${index}`,
  source,
  target,
  animated: target === "assess_research_state",
  style: { stroke: "rgba(40, 73, 107, 0.38)", strokeWidth: 1.5 },
}));

function ResearchGraphNode({ data }: NodeProps<Node<ResearchNodeData>>) {
  return (
    <div
      className={`min-w-[9rem] rounded-2xl border px-4 py-3 shadow-sm transition ${
        data.state === "active"
          ? "border-[var(--accent)] bg-[var(--ink)] text-white shadow-[0_0_0_4px_rgba(33,95,130,0.12)]"
          : data.state === "complete"
            ? "border-emerald-300 bg-emerald-50 text-emerald-950"
            : data.state === "failed"
              ? "border-rose-300 bg-rose-50 text-rose-950"
              : "border-[var(--border)] bg-white text-[var(--muted)]"
      }`}
    >
      <Handle position={Position.Left} type="target" className="!h-2 !w-2 !border-0 !bg-[var(--accent)]" />
      <p className="text-[0.62rem] font-semibold uppercase tracking-[0.17em]">{data.state}</p>
      <p className="mt-1 text-sm font-semibold">{data.label}</p>
      <Handle position={Position.Right} type="source" className="!h-2 !w-2 !border-0 !bg-[var(--accent)]" />
    </div>
  );
}

const nodeTypes = { research: ResearchGraphNode };

export default function ResearchConsole({ investigationId, initialRun }: ResearchConsoleProps) {
  const [trail, setTrail] = useState<LiveResearchTrail>({
    run: initialRun,
    events: [],
    actions: [],
    evaluation: null,
    replay_comparison: null,
    next_sequence: 0,
  });
  const [selectedNode, setSelectedNode] = useState(initialRun.active_node);
  const [isReplaying, setIsReplaying] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let refreshTimer: ReturnType<typeof setTimeout> | null = null;
    let fallbackTimer: ReturnType<typeof setInterval> | null = null;

    const refresh = async () => {
      try {
        const next = await getResearchTrail(investigationId);
        if (!cancelled) startTransition(() => setTrail(next));
      } catch {
        // The workspace poll remains the final fallback.
      }
    };

    const scheduleRefresh = () => {
      if (refreshTimer) return;
      refreshTimer = setTimeout(() => {
        refreshTimer = null;
        void refresh();
      }, 180);
    };

    void refresh();
    const source = new EventSource(getResearchEventsUrl(investigationId));
    EVENT_TYPES.forEach((eventType) => source.addEventListener(eventType, scheduleRefresh));
    source.onerror = () => {
      if (!fallbackTimer) fallbackTimer = setInterval(() => void refresh(), 5000);
    };
    source.onopen = () => {
      if (fallbackTimer) {
        clearInterval(fallbackTimer);
        fallbackTimer = null;
      }
    };

    return () => {
      cancelled = true;
      source.close();
      if (refreshTimer) clearTimeout(refreshTimer);
      if (fallbackTimer) clearInterval(fallbackTimer);
    };
  }, [investigationId]);

  const completedNodes = useMemo(() => {
    const completed = new Set<string>();
    const failed = new Set<string>();
    trail.events.forEach((event) => {
      const node = typeof event.payload.node === "string" ? event.payload.node : null;
      if (node && event.event_type === "node.completed") completed.add(node);
      if (node && event.event_type === "run.failed") failed.add(node);
    });
    return { completed, failed };
  }, [trail.events]);

  const nodes = useMemo<Node<ResearchNodeData>[]>(
    () =>
      GRAPH_NODES.map(([id, label, x, y]) => ({
        id,
        type: "research",
        position: { x, y },
        data: {
          label,
          state:
            trail.run?.active_node === id && trail.run.status === "running"
              ? "active"
              : trail.run?.active_node === id && trail.run.status === "failed"
                ? "failed"
              : completedNodes.failed.has(id)
                ? "failed"
                : completedNodes.completed.has(id)
                  ? "complete"
                  : "waiting",
        },
      })),
    [completedNodes, trail.run?.active_node, trail.run?.status],
  );

  const selectedEvents = selectedNode
    ? trail.events.filter((event) => event.payload.node === selectedNode)
    : [];
  const selectedCompletion = [...selectedEvents].reverse().find((event) => event.event_type === "node.completed");

  const handleReplay = async () => {
    if (!trail.run || isReplaying) return;
    setIsReplaying(true);
    try {
      await replayResearchRun(investigationId, trail.run.run_id);
      const next = await getResearchTrail(investigationId);
      startTransition(() => setTrail(next));
    } finally {
      setIsReplaying(false);
    }
  };

  return (
    <section className="overflow-hidden rounded-[2rem] border border-[rgba(19,35,58,0.1)] bg-[rgba(250,252,255,0.94)] shadow-[0_42px_80px_-52px_rgba(19,35,58,0.55)] backdrop-blur-xl">
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-[var(--border)] bg-white/80 px-6 py-5 sm:px-8">
        <div>
          <p className="eyebrow">Autonomous research runtime</p>
          <h2 className="mt-2 text-xl font-semibold tracking-[-0.025em] text-[var(--ink)]">Live, durable, and auditable</h2>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={trail.run?.status ?? initialRun.status} />
          <span className="data-pill">{trail.run?.mode ?? "live"} run</span>
          {trail.run && trail.run.status !== "queued" && trail.run.status !== "running" ? (
            <button
              type="button"
              onClick={handleReplay}
              disabled={isReplaying}
              className="rounded-xl border border-[var(--border)] bg-white px-3 py-2 text-xs font-semibold text-[var(--ink)] transition hover:border-[var(--accent)] disabled:opacity-50"
            >
              {isReplaying ? "Starting replay…" : "Replay without network"}
            </button>
          ) : null}
        </div>
      </div>
      {(trail.run?.warnings ?? initialRun.warnings).length > 0 ? (
        <div className="border-b border-amber-200 bg-amber-50 px-6 py-4 text-sm text-amber-950 sm:px-8">
          <p className="font-semibold">Runtime notice</p>
          <ul className="mt-1 list-disc space-y-1 pl-5">
            {(trail.run?.warnings ?? initialRun.warnings).slice(0, 4).map((warning) => <li key={warning}>{warning}</li>)}
          </ul>
        </div>
      ) : null}

      <div className="grid xl:grid-cols-[minmax(0,1.35fr)_minmax(19rem,0.65fr)]">
        <div className="border-b border-[var(--border)] xl:border-b-0 xl:border-r">
          <div className="h-[520px] bg-[radial-gradient(circle_at_top,rgba(70,121,155,0.08),transparent_55%)]">
            <ReactFlow
              nodes={nodes}
              edges={GRAPH_EDGES}
              nodeTypes={nodeTypes}
              fitView
              fitViewOptions={{ padding: 0.16 }}
              minZoom={0.55}
              maxZoom={1.35}
              nodesDraggable={false}
              nodesConnectable={false}
              elementsSelectable
              onNodeClick={(_event, node) => setSelectedNode(node.id)}
              proOptions={{ hideAttribution: true }}
            >
              <Background color="rgba(23,44,71,0.12)" gap={22} size={1} />
              <Controls showInteractive={false} position="bottom-right" />
            </ReactFlow>
          </div>
          <div className="border-t border-[var(--border)] bg-white/70 px-6 py-4 text-sm text-[var(--muted)]">
            {selectedNode ? (
              <p>
                <span className="font-semibold text-[var(--ink)]">{selectedNode.replaceAll("_", " ")}</span>
                {selectedEvents.length > 0 ? ` · ${selectedEvents.length} persisted transition(s)` : " · waiting"}
                {typeof selectedCompletion?.payload.duration_ms === "number" ? ` · ${selectedCompletion.payload.duration_ms} ms` : ""}
                {typeof selectedCompletion?.payload.document_count === "number" ? ` · ${selectedCompletion.payload.document_count} documents` : ""}
              </p>
            ) : (
              <p>Select a graph node to inspect its sanitized transition history.</p>
            )}
          </div>
        </div>

        <div className="max-h-[585px] overflow-y-auto p-5 sm:p-6 [scrollbar-width:thin]">
          <p className="text-[0.64rem] font-semibold uppercase tracking-[0.2em] text-[var(--muted)]">Research activity</p>
          <div className="mt-4 space-y-3">
            {trail.actions.length > 0 ? trail.actions.map((action) => <ActionCard key={action.action_id} action={action} />) : <EmptyActivity run={trail.run ?? initialRun} />}
          </div>
        </div>
      </div>

      <div className="grid gap-0 border-t border-[var(--border)] lg:grid-cols-[0.9fr_1.1fr]">
        <BudgetPanel run={trail.run ?? initialRun} />
        <GatePanel checks={trail.evaluation?.checks ?? []} decision={trail.evaluation?.final_decision ?? null} />
      </div>
      {trail.replay_comparison ? <ReplayComparison comparison={trail.replay_comparison} /> : null}
    </section>
  );
}

function ReplayComparison({ comparison }: { comparison: NonNullable<LiveResearchTrail["replay_comparison"]> }) {
  const actionScore = comparison.action_equivalence;
  const rows = [
    ["Recorded actions", actionScore === undefined ? "Pending" : `${Math.round(actionScore * 100)}% equivalent`],
    ["Evidence artifacts", comparison.artifact_hash_equivalent === undefined ? "Pending" : comparison.artifact_hash_equivalent ? "Hash match" : "Hash differs"],
    ["Gate decision", comparison.evaluation_equivalent === undefined ? "Pending" : comparison.evaluation_equivalent ? "Match" : "Differs"],
  ];
  return (
    <div className="border-t border-[var(--border)] bg-slate-950 px-6 py-6 text-white sm:px-8">
      <p className="text-[0.64rem] font-semibold uppercase tracking-[0.2em] text-cyan-300">Network-disabled replay comparison</p>
      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        {rows.map(([label, value]) => (
          <div key={label} className="rounded-xl border border-white/10 bg-white/5 p-4">
            <p className="text-xs text-slate-400">{label}</p>
            <p className="mt-1 text-sm font-semibold">{value}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

function ActionCard({ action }: { action: LiveResearchAction }) {
  return (
    <article className="rounded-2xl border border-[var(--border)] bg-white p-4">
      <div className="flex items-center justify-between gap-3">
        <span className="text-[0.62rem] font-semibold uppercase tracking-[0.16em] text-[var(--accent)]">{action.decision.action_type.replaceAll("_", " ")}</span>
        <span className="text-[0.62rem] font-semibold uppercase tracking-[0.14em] text-[var(--muted)]">{action.status}</span>
      </div>
      <p className="mt-2 text-sm leading-6 text-[var(--ink)]">{action.decision.action_summary}</p>
      <div className="mt-3 flex flex-wrap gap-2 text-[0.68rem] text-[var(--muted)]">
        {action.provider ? <><span>{action.provider.replaceAll("_", " ")}</span><span>/</span></> : null}
        <span>{action.decision.retrieval_lane}</span>
        <span>·</span>
        <span>{action.result_count} results</span>
        <span>·</span>
        <span>{action.document_ids.length} docs</span>
        {action.duration_ms !== null ? <><span>·</span><span>{action.duration_ms} ms</span></> : null}
      </div>
      {action.decision.gap_ids.length > 0 ? (
        <p className="mt-2 text-[0.68rem] text-[var(--muted)]">Gaps: {action.decision.gap_ids.join(", ")}</p>
      ) : null}
      {action.warning ? <p className="mt-3 text-xs leading-5 text-amber-800">{action.warning}</p> : null}
    </article>
  );
}

function BudgetPanel({ run }: { run: LiveResearchRun }) {
  const busiestDomain = Math.max(0, ...Object.values(run.usage.domain_requests));
  const budgets = [
    ["Active seconds", Math.round(run.usage.active_seconds), run.limits.wall_seconds],
    ["Tool calls", run.usage.tool_calls, run.limits.tool_calls],
    ["Model calls", run.usage.model_calls, run.limits.model_calls],
    ["Model tokens", run.usage.model_tokens, run.limits.model_tokens],
    ["Search results", run.usage.search_results, run.limits.search_results],
    ["Canonical fetches", run.usage.canonical_fetches, run.limits.canonical_fetches],
    ["Browser renders", run.usage.browser_renders, run.limits.browser_renders],
    ["Internal searches", run.usage.internal_searches, run.limits.internal_searches],
    ["Retries", run.usage.retries, run.limits.retries],
    ["Busiest domain", busiestDomain, run.limits.domain_requests],
  ] as const;
  return (
    <div className="border-b border-[var(--border)] p-6 lg:border-b-0 lg:border-r sm:p-8">
      <p className="eyebrow">Bounded execution</p>
      <div className="mt-5 space-y-4">
        {budgets.map(([label, used, limit]) => <BudgetBar key={label} label={label} used={used} limit={limit} />)}
        <BudgetBar label="Estimated spend" used={run.usage.spend_usd} limit={run.limits.spend_usd} currency />
      </div>
    </div>
  );
}

function BudgetBar({ label, used, limit, currency = false }: { label: string; used: number; limit: number; currency?: boolean }) {
  const percentage = Math.min(100, Math.round((used / Math.max(limit, 0.0001)) * 100));
  return (
    <div>
      <div className="flex justify-between gap-4 text-xs text-[var(--muted)]">
        <span>{label}</span>
        <span>{currency ? `$${used.toFixed(3)} / $${limit.toFixed(2)}` : `${used} / ${limit}`}</span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-200">
        <div className="h-full rounded-full bg-[var(--accent)] transition-[width] duration-500" style={{ width: `${percentage}%` }} />
      </div>
    </div>
  );
}

function GatePanel({ checks, decision }: { checks: LivePublicationCheck[]; decision: string | null }) {
  return (
    <div className="p-6 sm:p-8">
      <div className="flex items-center justify-between gap-3">
        <p className="eyebrow">Publication gate</p>
        {decision ? <span className="data-pill">{decision.replaceAll("_", " ")}</span> : null}
      </div>
      <div className="mt-5 grid gap-2 sm:grid-cols-2">
        {checks.length > 0 ? checks.map((check) => (
          <div key={check.key} title={check.detail} className={`rounded-xl border px-3 py-3 text-xs ${check.passed ? "border-emerald-200 bg-emerald-50 text-emerald-900" : "border-rose-200 bg-rose-50 text-rose-900"}`}>
            <p><span aria-hidden="true">{check.passed ? "✓" : "×"}</span> {check.label}</p>
            <p className="mt-1 opacity-70">{String(check.measured ?? "n/a")} / target {String(check.threshold ?? "n/a")}</p>
          </div>
        )) : <p className="col-span-2 text-sm leading-6 text-[var(--muted)]">The evidence scorecard appears after skeptic and receipt review.</p>}
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: LiveResearchRun["status"] }) {
  const running = status === "queued" || status === "running";
  return <span className={`rounded-full px-3 py-1.5 text-[0.65rem] font-semibold uppercase tracking-[0.14em] ${running ? "bg-blue-100 text-blue-800" : status === "completed" ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-900"}`}>{status.replaceAll("_", " ")}</span>;
}

function EmptyActivity({ run }: { run: LiveResearchRun }) {
  return <div className="rounded-2xl border border-dashed border-[var(--border)] bg-white/65 p-5 text-sm leading-6 text-[var(--muted)]">{run.status === "queued" ? "The durable worker has queued this investigation." : "Waiting for the first persisted research action."}</div>;
}
