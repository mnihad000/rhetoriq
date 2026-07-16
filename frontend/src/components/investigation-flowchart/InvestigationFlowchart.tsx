import { motion } from "framer-motion";
import { useMemo } from "react";
import type { InvestigationFlowchartData } from "../../types/rhetoriq";
import InvestigationNodeCard from "./InvestigationNodeCard";
import { buildTree, type TreeRow } from "./treeLayout";

const EASE_OUT = [0.16, 1, 0.3, 1] as const;

type InvestigationFlowchartProps = {
  data?: InvestigationFlowchartData;
  isLoading?: boolean;
};

export default function InvestigationFlowchart({
  data,
  isLoading = false,
}: InvestigationFlowchartProps) {
  const rows = useMemo<TreeRow[]>(
    () => (data && data.nodes.length > 0 ? buildTree(data) : []),
    [data],
  );

  const hasGraphData = rows.length > 0;
  // Re-key on the shape of the tree so the reveal animation replays on new data.
  const treeKey = useMemo(
    () => (data ? `${data.currentNodeId}:${data.nodes.length}:${data.edges.length}` : "empty"),
    [data],
  );

  return (
    <section>
      <div className="relative overflow-hidden rounded-[1.4rem] border border-[var(--border)] bg-[rgba(255,255,255,0.7)] shadow-[0_32px_70px_-48px_rgba(19,35,58,0.4)] backdrop-blur-xl">
        {/* header */}
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--border)] bg-white/55 px-6 py-4">
          <div>
            <p className="text-[0.62rem] font-semibold uppercase tracking-[0.24em] text-[var(--muted)]">
              Narrative provenance
            </p>
            <p className="mt-1 text-sm font-medium text-[var(--ink)]">
              Origin to current state · click any article to open the source
            </p>
          </div>
          <div className="flex items-center gap-3 text-[0.62rem] font-semibold uppercase tracking-[0.14em] text-[var(--muted)]">
            <LegendDot color="var(--accent)" label="Timeline" />
            <LegendDot color="#b06a5b" label="Counter-frame" />
            <span className="inline-flex items-center gap-1 rounded-md border border-[var(--ink)] bg-[var(--ink)] px-2 py-1 text-[0.6rem] text-white">
              <svg aria-hidden="true" viewBox="0 0 12 12" className="h-2 w-2 shrink-0" fill="none">
                <circle cx="6" cy="6" r="5" stroke="currentColor" strokeWidth="1.4" />
                <path d="M3.5 6l1.8 1.8L8.5 4.2" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              Receipt verified
            </span>
          </div>
        </div>

        {/* contained, vertically-scrolling tree — never overflows horizontally */}
        <div className="relative h-[560px] overflow-y-auto overflow-x-hidden px-5 py-7 sm:h-[640px] sm:px-7 [scrollbar-color:rgba(23,44,71,0.18)_transparent] [scrollbar-width:thin]">
          {hasGraphData ? (
            <motion.div
              key={treeKey}
              animate="show"
              className="mx-auto flex max-w-2xl flex-col"
              initial="hidden"
              variants={{
                hidden: {},
                show: { transition: { staggerChildren: 0.12, delayChildren: 0.05 } },
              }}
            >
              {rows.map((row, index) => (
                <TreeRowBlock key={row.node.id} row={row} isLast={index === rows.length - 1} />
              ))}
            </motion.div>
          ) : null}

          {!hasGraphData && isLoading ? <WaitingTraceOverlay /> : null}
          {!hasGraphData && !isLoading ? <EmptyState /> : null}
        </div>
      </div>
    </section>
  );
}

function TreeRowBlock({ row, isLast }: { row: TreeRow; isLast: boolean }) {
  const kind = row.isCurrent ? "current" : "trunk";

  return (
    <motion.div
      className="relative flex gap-4 sm:gap-5"
      variants={{
        hidden: { opacity: 0, y: 18 },
        show: { opacity: 1, y: 0, transition: { duration: 0.55, ease: EASE_OUT } },
      }}
    >
      {/* rail: continuous spine + node marker */}
      <div className="relative flex w-7 flex-col items-center">
        {/* line above the dot (hidden on the origin row) */}
        <span
          aria-hidden="true"
          className={`w-px flex-none ${row.isOrigin ? "h-0" : "h-6"}`}
          style={{ backgroundColor: "rgba(23,44,71,0.16)" }}
        />
        <span
          aria-hidden="true"
          className="relative z-10 my-1 grid h-3.5 w-3.5 place-items-center rounded-full"
          style={{
            backgroundColor: row.isCurrent ? "var(--ink)" : "white",
            border: `2px solid ${row.isCurrent ? "var(--ink)" : "var(--accent)"}`,
          }}
        >
          {row.isCurrent ? (
            <span className="h-1.5 w-1.5 rounded-full bg-white" />
          ) : (
            <span className="h-1 w-1 rounded-full bg-[var(--accent)]" />
          )}
        </span>
        {/* line below the dot fills the rest of the row (hidden on the last row) */}
        {!isLast ? (
          <span
            aria-hidden="true"
            className="w-px flex-1"
            style={{ backgroundColor: "rgba(23,44,71,0.16)" }}
          />
        ) : null}
      </div>

      {/* content: trunk article + nested branch articles */}
      <div className={`min-w-0 flex-1 ${isLast ? "pb-2" : "pb-7"}`}>
        {row.isOrigin ? (
          <p className="mb-2 text-[0.6rem] font-semibold uppercase tracking-[0.24em] text-[var(--accent)]">
            Earliest appearance in our dataset
          </p>
        ) : null}

        <InvestigationNodeCard node={row.node} kind={kind} />

        {row.branches.length > 0 ? (
          <div className="mt-3 space-y-3 pl-5">
            {row.branches.map((branch) => (
              <div key={branch.node.id} className="relative">
                {/* elbow connector from trunk card down into the branch */}
                <span
                  aria-hidden="true"
                  className="absolute -left-5 top-0 h-6 w-px"
                  style={{ backgroundColor: "rgba(23,44,71,0.14)" }}
                />
                <span
                  aria-hidden="true"
                  className="absolute -left-5 top-6 h-px w-5"
                  style={{ backgroundColor: "rgba(23,44,71,0.14)" }}
                />
                <InvestigationNodeCard node={branch.node} kind={branch.variant} />
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </motion.div>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="h-2 w-2 rounded-full" style={{ backgroundColor: color }} />
      {label}
    </span>
  );
}

function EmptyState() {
  return (
    <div className="flex h-full items-center justify-center px-6 text-center">
      <p className="max-w-sm text-sm leading-6 text-[var(--muted)]">
        The narrative tree will appear here once the investigation has gathered timeline
        evidence.
      </p>
    </div>
  );
}

function WaitingTraceOverlay() {
  return (
    <div className="flex h-full items-center justify-center">
      <div className="relative flex w-full max-w-md flex-col items-center">
        {/* drawing spine */}
        <div className="relative h-48 w-px overflow-hidden bg-[rgba(23,44,71,0.1)]">
          <motion.div
            animate={{ y: ["-100%", "100%"] }}
            className="absolute inset-x-[-1px] h-20 bg-[linear-gradient(180deg,transparent,rgba(124,144,172,0.9),transparent)]"
            transition={{ duration: 1.8, repeat: Infinity, ease: "easeInOut" }}
          />
        </div>
        {[0, 1, 2].map((index) => (
          <motion.span
            key={index}
            animate={{ scale: [0.6, 1, 0.6], opacity: [0.3, 1, 0.3] }}
            className="absolute h-3 w-3 rounded-full border-2 border-[var(--accent)] bg-white"
            style={{ top: `${22 + index * 30}%` }}
            transition={{
              duration: 1.8,
              repeat: Infinity,
              ease: "easeInOut",
              delay: index * 0.25,
            }}
          />
        ))}
        <div className="mt-8 flex flex-col items-center gap-2 text-center">
          <p className="text-[0.7rem] font-semibold uppercase tracking-[0.24em] text-[var(--muted)]">
            Tracing narrative origin
          </p>
          <p className="max-w-xs text-sm leading-6 text-[var(--muted)]">
            Building the provenance tree as evidence resolves.
          </p>
        </div>
      </div>
    </div>
  );
}
