import { motion } from "framer-motion";
import type { InvestigationNode } from "../../types/rhetoriq";
import {
  countNodeReceipts,
  getConfidenceLabel,
  getNodeTypeLabel,
  hasBrowserVerifiedReceipt,
  pickNodeUrl,
  type BranchVariant,
} from "./treeLayout";

type CardKind = "trunk" | "current" | BranchVariant;

const EASE_OUT = [0.16, 1, 0.3, 1] as const;

const kindAccent: Record<CardKind, string> = {
  current: "var(--ink)",
  trunk: "var(--accent)",
  counter: "#b06a5b",
  related: "var(--accent)",
  uncertain: "#9a8a5c",
};

export default function InvestigationNodeCard({
  node,
  kind,
}: {
  node: InvestigationNode;
  kind: CardKind;
}) {
  const fallbackUrl = pickNodeUrl(node);
  const receiptCount = countNodeReceipts(node);
  const verified = hasBrowserVerifiedReceipt(node);
  const accent = kindAccent[kind];
  const isBranch = kind === "counter" || kind === "related" || kind === "uncertain";
  const isCurrent = kind === "current";
  const visibleSourceCount = isCurrent ? 8 : isBranch ? 3 : 4;
  const visibleSources = (node.sources ?? []).filter((source) => source.url).slice(0, visibleSourceCount);
  const hiddenSourceCount = Math.max(0, (node.sources?.filter((source) => source.url).length ?? 0) - visibleSources.length);

  const shell = isCurrent
    ? "border-[var(--ink)] bg-white shadow-[0_30px_60px_-40px_rgba(19,35,58,0.45)] ring-1 ring-[rgba(19,35,58,0.08)]"
    : isBranch
      ? "border-[rgba(23,44,71,0.12)] bg-[rgba(255,255,255,0.78)] shadow-[0_18px_38px_-34px_rgba(19,35,58,0.3)]"
      : "border-[rgba(23,44,71,0.12)] bg-[rgba(255,255,255,0.92)] shadow-[0_24px_50px_-40px_rgba(19,35,58,0.36)]";

  return (
    <motion.div
      className={`group relative block overflow-hidden rounded-[1.15rem] border ${shell} ${
        fallbackUrl
          ? "cursor-pointer transition-shadow hover:shadow-[0_34px_64px_-38px_rgba(19,35,58,0.5)]"
          : ""
      } ${isBranch ? "px-4 py-4" : "px-5 py-5 sm:px-6 sm:py-6"}`}
      transition={{ duration: 0.35, ease: EASE_OUT }}
      whileHover={fallbackUrl ? { y: -3 } : undefined}
    >
      {/* accent spine on the card's left edge */}
      <span
        aria-hidden="true"
        className="absolute inset-y-0 left-0 w-[3px]"
        style={{ backgroundColor: accent, opacity: isCurrent ? 1 : 0.55 }}
      />

      <div className="flex items-start justify-between gap-3">
        <p
          className="text-[0.62rem] font-semibold uppercase tracking-[0.22em]"
          style={{ color: accent }}
        >
          {getNodeTypeLabel(node.nodeType)}
        </p>
        {node.timestamp ? (
          <span className="shrink-0 rounded-full border border-[var(--border)] bg-[var(--accent-soft)] px-2.5 py-1 text-[0.62rem] font-semibold uppercase tracking-[0.12em] text-[var(--muted)]">
            {node.timestamp}
          </span>
        ) : null}
      </div>

      <h3
        className={`mt-3 font-[Iowan_Old_Style,Palatino_Linotype,Book_Antiqua,Georgia,serif] tracking-[-0.02em] text-[var(--ink)] ${
          isCurrent
            ? "text-[1.5rem] leading-[1.12]"
            : isBranch
              ? "text-[1.05rem] leading-[1.2]"
              : "text-[1.25rem] leading-[1.16]"
        }`}
      >
        {node.label}
      </h3>

      {node.summary ? (
        <p
          className={`mt-2.5 text-[0.9rem] leading-6 text-[var(--muted)] ${
            isBranch ? "line-clamp-2" : "line-clamp-3"
          }`}
        >
          {node.summary}
        </p>
      ) : null}

      <div className="mt-4 flex flex-wrap items-center gap-2 text-[0.68rem] font-semibold uppercase tracking-[0.1em] text-[var(--muted)]">
        <span className="rounded-md border border-[var(--border)] bg-white/70 px-2 py-1">
          {node.sourceCount} {node.sourceCount === 1 ? "source" : "sources"}
        </span>
        {receiptCount > 0 ? (
          <span className="rounded-md border border-[var(--border)] bg-white/70 px-2 py-1">
            {receiptCount} receipts
          </span>
        ) : null}
        {node.confidence ? (
          <span className="rounded-md border border-[var(--border)] bg-white/70 px-2 py-1">
            {getConfidenceLabel(node.confidence)}
          </span>
        ) : null}
        {verified ? (
          <span className="inline-flex items-center gap-1 rounded-md border border-[var(--ink)] bg-[var(--ink)] px-2 py-1 text-white">
            <svg aria-hidden="true" viewBox="0 0 12 12" className="h-2.5 w-2.5 shrink-0" fill="none">
              <circle cx="6" cy="6" r="5" stroke="currentColor" strokeWidth="1.4" />
              <path d="M3.5 6l1.8 1.8L8.5 4.2" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            Receipt verified
          </span>
        ) : null}
      </div>

      {visibleSources.length > 0 ? (
        <div className="mt-4 space-y-2 border-t border-[var(--border)] pt-3">
          {visibleSources.map((source) => (
            <a
              key={source.id}
              className="flex items-center justify-between gap-3 rounded-lg px-1.5 py-1 text-[0.72rem] transition hover:bg-[rgba(124,144,172,0.1)]"
              href={source.url}
              rel="noreferrer noopener"
              target="_blank"
            >
              <span className="min-w-0">
                <span className="block truncate font-semibold text-[var(--ink)]">
                  {source.title}
                </span>
                <span className="block truncate font-medium text-[var(--muted)]">
                  {getHostFromUrl(source.url) ?? source.name}
                </span>
              </span>
              <span className="inline-flex shrink-0 items-center gap-1 font-semibold text-[var(--accent)]">
                Open
                <svg
                  aria-hidden="true"
                  className="h-3.5 w-3.5"
                  viewBox="0 0 16 16"
                  fill="none"
                >
                  <path
                    d="M5 11L11 5M11 5H6M11 5V10"
                    stroke="currentColor"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth="1.6"
                  />
                </svg>
              </span>
            </a>
          ))}
          {hiddenSourceCount > 0 ? (
            <p className="px-1.5 text-[0.68rem] font-medium text-[var(--muted)]">
              +{hiddenSourceCount} more source{hiddenSourceCount === 1 ? "" : "s"} in this node
            </p>
          ) : null}
        </div>
      ) : fallbackUrl ? (
        <a
          className="mt-4 flex items-center justify-between gap-3 border-t border-[var(--border)] pt-3"
          href={fallbackUrl}
          rel="noreferrer noopener"
          target="_blank"
        >
          <span className="truncate text-[0.72rem] font-medium text-[var(--muted)]">
            {getHostFromUrl(fallbackUrl) ?? "Open source"}
          </span>
          <span className="inline-flex shrink-0 items-center gap-1 text-[0.72rem] font-semibold text-[var(--accent)] transition-colors hover:text-[var(--ink)]">
            Read article
            <svg
              aria-hidden="true"
              className="h-3.5 w-3.5"
              viewBox="0 0 16 16"
              fill="none"
            >
              <path
                d="M5 11L11 5M11 5H6M11 5V10"
                stroke="currentColor"
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth="1.6"
              />
            </svg>
          </span>
        </a>
      ) : null}
    </motion.div>
  );
}

function getHostFromUrl(url: string | undefined): string | undefined {
  if (!url) return undefined;

  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return undefined;
  }
}
