import {
  forwardRef,
  startTransition,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { useNavigate } from "react-router-dom";
import {
  motion,
  useMotionValue,
  useMotionValueEvent,
  useScroll,
  useSpring,
  type Variants,
} from "framer-motion";
import Header from "../components/layout/Header";
import AtcShader from "@/components/ui/atc-shader";
import {
  ApiError,
  createInvestigation,
  getTrendingFeed,
  startTrendingInvestigation,
} from "../lib/api";
import { createInvestigationHref } from "../lib/investigationHref";
import type { LiveTrendingFeed, LiveTrendingTopic, RadarTopic } from "../types/rhetoriq";

const EASE_OUT = [0.16, 1, 0.3, 1] as const;

type Point = { x: number; y: number };

const FALLBACK_TOPICS: RadarTopic[] = [
  {
    id: "hidden-energy-tax",
    title: "Hidden Energy Tax",
    summary:
      "A cost-focused frame begins in local coverage before spreading into broader policy commentary.",
    spike: "7.4x",
    sourceCount: 12,
    firstObserved: "Jun 20, 9:14 AM",
    status: "Amplifying",
    sourceMix: "Local, national, advocacy",
    confidence: "High",
  },
  {
    id: "tiktok-ban",
    title: "TikTok Ban",
    summary:
      "Security, speech, and competition frames compete as the story moves through officials and platforms.",
    spike: "5.8x",
    sourceCount: 9,
    firstObserved: "Jun 20, 10:42 AM",
    status: "Mainstreaming",
    sourceMix: "Official, tech, national",
    confidence: "Medium",
  },
  {
    id: "education-policy",
    title: "Education Policy",
    summary:
      "Counter-frames form around school funding, curriculum control, and local implementation tradeoffs.",
    spike: "4.2x",
    sourceCount: 7,
    firstObserved: "Jun 20, 1:06 PM",
    status: "Emerging",
    sourceMix: "Local, forum, advocacy",
    confidence: "Medium",
  },
];



export default function LandingPage() {
  const streamRef = useRef<HTMLElement>(null);
  const [feed, setFeed] = useState<LiveTrendingFeed | null>(null);
  const [feedErrorMessage, setFeedErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    async function loadFeed() {
      try {
        const response = await getTrendingFeed(3);
        if (cancelled) return;
        startTransition(() => {
          setFeed(response);
          setFeedErrorMessage(null);
        });
        // Keep polling while backend is still warming up or data is stale
        if (response.state === "warming" || response.state === "stale") {
          retryTimer = setTimeout(() => { if (!cancelled) void loadFeed(); }, 15000);
        }
      } catch (error) {
        if (cancelled) return;
        setFeedErrorMessage(
          error instanceof ApiError
            ? error.message
            : "Unable to load the live hot political news feed.",
        );
        retryTimer = setTimeout(() => { if (!cancelled) void loadFeed(); }, 20000);
      }
    }

    void loadFeed();
    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
    };
  }, []);

  function scrollToStream() {
    streamRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  return (
    <main className="landing-page relative isolate overflow-x-clip">
      <AtcShader className="pointer-events-none fixed inset-0 -z-20 h-full w-full" />
      <div className="relative z-0">
        <Header variant="landing" />
        <Hero onGetStarted={scrollToStream} />
        <Stream ref={streamRef} feed={feed} errorMessage={feedErrorMessage} />
        <SiteFooter />
      </div>
    </main>
  );
}

/* ════════════════════════════════════════════════════════════
   HERO
════════════════════════════════════════════════════════════ */

const wordRise: Variants = {
  hidden: { opacity: 0, y: "0.4em" },
  show: (i: number) => ({
    opacity: 1,
    y: "0em",
    transition: { delay: 0.15 + i * 0.08, duration: 0.7, ease: EASE_OUT },
  }),
};

function Hero({ onGetStarted }: { onGetStarted: () => void }) {
  const headline = ["Trace", "how", "political", "stories", "spread."];

  return (
    <section className="relative flex min-h-[88vh] flex-col items-center justify-center px-4 text-center sm:px-6">
      {/* Faint vertical seed of the curve, descending from the headline */}
      <motion.div
        aria-hidden="true"
        initial={{ scaleY: 0, opacity: 0 }}
        animate={{ scaleY: 1, opacity: 1 }}
        transition={{ delay: 0.9, duration: 1.1, ease: EASE_OUT }}
        className="absolute bottom-0 left-1/2 h-24 w-px origin-top -translate-x-1/2 bg-gradient-to-b from-transparent via-[var(--border)] to-[var(--accent)]"
      />

      <motion.p
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6 }}
        className="eyebrow"
      >
        Source-grounded narrative intelligence
      </motion.p>

      <h1 className="mt-7 max-w-4xl text-5xl font-semibold leading-[0.98] tracking-normal text-[var(--ink)] drop-shadow-[0_0_24px_rgba(255,173,97,0.28)] sm:text-6xl lg:text-7xl">
        {headline.map((word, i) => (
          <span key={word} className="inline-block overflow-hidden align-baseline">
            <motion.span
              custom={i}
              variants={wordRise}
              initial="hidden"
              animate="show"
              className="inline-block pr-[0.22em]"
            >
              {word}
            </motion.span>
          </span>
        ))}
      </h1>

      <motion.p
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.7, duration: 0.7 }}
        className="mt-7 max-w-xl text-lg leading-8 text-[var(--muted)]"
      >
        Follow a single thread through every claim — how a narrative emerges,
        mutates, and gets challenged, with receipts at every turn.
      </motion.p>

      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.95, duration: 0.7 }}
        className="mt-10"
      >
        <button
          type="button"
          onClick={onGetStarted}
          className="group inline-flex items-center gap-3 rounded-md border border-[var(--accent)] bg-[var(--accent)] px-7 py-3.5 text-sm font-semibold tracking-normal text-[#171016] transition hover:-translate-y-0.5 hover:bg-[#ffc184] hover:shadow-[0_0_28px_rgba(255,173,97,0.48)]"
        >
          Get started
          <motion.span
            aria-hidden="true"
            className="text-base"
            animate={{ y: [0, 4, 0] }}
            transition={{ duration: 1.6, repeat: Infinity, ease: "easeInOut" }}
          >
            ↓
          </motion.span>
        </button>
      </motion.div>
    </section>
  );
}

/* ════════════════════════════════════════════════════════════
   STREAM — serpentine curve weaving under each story card
════════════════════════════════════════════════════════════ */

/**
 * Fluid serpentine path. Each segment is a cubic bezier whose control points
 * share the endpoints' x — giving a vertical tangent at every node, so the
 * curve runs straight down *under* each card, bows to that card's side, then
 * smoothly S-weaves across to the next card. A flowing ribbon, not diagonals.
 */
function buildSmoothPath(points: Point[]): string {
  if (points.length < 2) return "";
  const f = (n: number) => n.toFixed(2);
  let d = `M ${f(points[0].x)} ${f(points[0].y)}`;
  for (let i = 0; i < points.length - 1; i++) {
    const a = points[i];
    const b = points[i + 1];
    const midY = (a.y + b.y) / 2;
    d += ` C ${f(a.x)} ${f(midY)}, ${f(b.x)} ${f(midY)}, ${f(b.x)} ${f(b.y)}`;
  }
  return d;
}

const Stream = forwardRef<
  HTMLElement,
  { feed: LiveTrendingFeed | null; errorMessage: string | null }
>(function Stream({ feed, errorMessage }, forwardedRef) {
  const topics = (feed?.state === "ready" || feed?.state === "stale") ? feed.topics.slice(0, 3) : [];
  const containerRef = useRef<HTMLDivElement>(null);
  const cardRefs = useRef<(HTMLDivElement | null)[]>([]);
  const pathRef = useRef<SVGPathElement>(null);
  const anchorsRef = useRef<Point[]>([]);
  const totalLenRef = useRef(0);

  const [size, setSize] = useState({ w: 0, h: 0 });
  const [pathD, setPathD] = useState("");
  const [fractions, setFractions] = useState<number[]>([]);
  const [lit, setLit] = useState<boolean[]>(() => FALLBACK_TOPICS.map(() => false));

  // Scroll progress across the curve region.
  const { scrollYProgress } = useScroll({
    target: containerRef,
    offset: ["start 0.72", "end 0.82"],
  });
  const draw = useSpring(scrollYProgress, {
    stiffness: 90,
    damping: 26,
    restDelta: 0.0005,
  });

  // Node that rides the curve.
  const nodeX = useMotionValue(0);
  const nodeY = useMotionValue(0);

  // Measure card centers and rebuild the curve.
  const measure = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;
    const w = container.clientWidth;
    const h = container.clientHeight;
    const isNarrow = w < 768;
    const leftX = isNarrow ? w * 0.5 : w * 0.28;
    const rightX = isNarrow ? w * 0.5 : w * 0.72;

    const anchors: Point[] = [];
    cardRefs.current.forEach((card, i) => {
      if (!card) return;
      const y = card.offsetTop + card.offsetHeight / 2;
      const x = isNarrow ? w * 0.5 : i % 2 === 0 ? leftX : rightX;
      anchors.push({ x, y });
    });

    const points: Point[] = [
      { x: w * 0.5, y: 0 },
      ...anchors,
      { x: w * 0.5, y: h },
    ];

    anchorsRef.current = anchors;
    setSize({ w, h });
    setPathD(buildSmoothPath(points));
  }, []);

  useLayoutEffect(() => {
    measure();
    const container = containerRef.current;
    if (!container || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => measure());
    ro.observe(container);
    return () => ro.disconnect();
  }, [measure]);

  // After the path is laid out, find each card's position along it (0–1).
  useLayoutEffect(() => {
    const path = pathRef.current;
    if (!path || !pathD) return;
    const total = path.getTotalLength();
    totalLenRef.current = total;

    const anchors = anchorsRef.current;
    const best = anchors.map(() => ({ dist: Infinity, len: 0 }));
    const samples = 600;
    for (let s = 0; s <= samples; s++) {
      const len = (s / samples) * total;
      const pt = path.getPointAtLength(len);
      anchors.forEach((a, idx) => {
        const dx = pt.x - a.x;
        const dy = pt.y - a.y;
        const dist = dx * dx + dy * dy;
        if (dist < best[idx].dist) best[idx] = { dist, len };
      });
    }
    setFractions(best.map((b) => b.len / total));
    // Seed the node at the start.
    const start = path.getPointAtLength(0);
    nodeX.set(start.x);
    nodeY.set(start.y);
  }, [pathD, nodeX, nodeY]);

  // Drive node position + card lighting from scroll progress.
  useMotionValueEvent(draw, "change", (v) => {
    const path = pathRef.current;
    const total = totalLenRef.current;
    if (path && total) {
      const clamped = Math.min(Math.max(v, 0), 1);
      const pt = path.getPointAtLength(total * clamped);
      nodeX.set(pt.x);
      nodeY.set(pt.y);
    }
    if (fractions.length) {
      let changed = false;
      const next = fractions.map((f, i) => {
        const isLit = v >= f - 0.012;
        if (isLit !== lit[i]) changed = true;
        return isLit;
      });
      if (changed) setLit(next);
    }
  });

  return (
    <section
      ref={forwardedRef}
      data-topic-key={topics.map((t) => t.id).join("|")}
      className="relative px-4 pb-4 pt-10 sm:px-6 lg:px-8"
    >
      <div className="mx-auto max-w-5xl">
        <SpineHeading />

        {/* Curve region — SVG overlay sits behind the cards */}
        <div ref={containerRef} className="relative mt-14">
          <svg
            aria-hidden="true"
            className="pointer-events-none absolute inset-0 z-0 h-full w-full"
            width={size.w}
            height={size.h}
            viewBox={`0 0 ${size.w || 1} ${size.h || 1}`}
            fill="none"
          >
            {/* Static track */}
            <path
              d={pathD}
              stroke="var(--border)"
              strokeWidth={2}
              strokeLinecap="round"
              vectorEffect="non-scaling-stroke"
            />
            {/* Drawn curve, revealed on scroll */}
            <motion.path
              ref={pathRef}
              d={pathD}
              stroke="var(--accent)"
              strokeWidth={2.4}
              strokeLinecap="round"
              vectorEffect="non-scaling-stroke"
              style={{ pathLength: draw }}
            />
            {/* Anchor dots under each card */}
            {anchorsRef.current.map((a, i) => (
              <circle
                key={i}
                cx={a.x}
                cy={a.y}
                r={lit[i] ? 5 : 3.5}
                fill={lit[i] ? "var(--accent)" : "var(--surface-strong)"}
                stroke="var(--accent)"
                strokeWidth={1.4}
                vectorEffect="non-scaling-stroke"
                style={{ transition: "r 0.3s ease, fill 0.3s ease" }}
              />
            ))}
            {/* Traveling node */}
            <motion.circle
              cx={nodeX}
              cy={nodeY}
              r={7}
              fill="var(--accent)"
              opacity={0.16}
            />
            <motion.circle cx={nodeX} cy={nodeY} r={3.5} fill="var(--accent)" />
          </svg>

          {/* Cards */}
          <div className="relative z-10">
            {topics.length > 0 ? (
              topics.map((topic, index) => (
                <StoryRow
                  key={topic.id}
                  ref={(el) => {
                    cardRefs.current[index] = el;
                  }}
                  topic={topic}
                  index={index}
                  lit={lit[index] ?? false}
                />
              ))
            ) : (
              <StreamStateCard feed={feed} errorMessage={errorMessage} />
            )}
          </div>
        </div>

        {/* Terminal node into the prompt */}
        <div
          aria-hidden="true"
          className="relative z-10 mx-auto mt-2 flex h-10 w-10 items-center justify-center"
        >
          <span className="absolute h-3 w-3 rounded-full bg-[var(--accent)]" />
          <span className="absolute h-10 w-10 rounded-full border border-[var(--accent)] opacity-40" />
        </div>

        <PromptModule />
      </div>
    </section>
  );
});

function StreamStateCard({
  feed,
  errorMessage,
}: {
  feed: LiveTrendingFeed | null;
  errorMessage: string | null;
}) {
  let title = "Loading hot political news";
  let body = "Fetching the latest published narrative snapshot from the backend.";
  let tone: "neutral" | "error" = "neutral";

  if (errorMessage) {
    title = "Hot political news unavailable";
    body = errorMessage;
    tone = "error";
  } else if (feed?.state === "ready" && feed.topics.length === 0) {
    title = "Hot political news is warming up";
    body =
      feed.warning ??
      "The discovery pipeline is live, but no topic has cleared the publish thresholds yet.";
  } else if (feed && (feed.state === "warming" || feed.state === "stale" || feed.state === "error")) {
    title =
      feed.state === "error"
        ? "Hot political news unavailable"
        : "Hot political news is warming up";
    body =
      feed.warning ??
      "The discovery pipeline has not published a fresh trending snapshot yet.";
    tone = feed.state === "error" ? "error" : "neutral";
  }

  return (
    <div className="py-12">
      <div
        className={
          tone === "error"
            ? "mx-auto max-w-2xl rounded-md border border-[var(--error-border)] bg-[var(--error-surface)] p-6 text-[var(--error-ink)] shadow-[0_0_32px_rgba(255,111,85,0.14)]"
            : "mx-auto max-w-2xl rounded-md border border-[var(--border)] bg-[var(--surface-strong)] p-6 text-[var(--muted)] shadow-[0_0_32px_rgba(0,0,0,0.34)] backdrop-blur-md"
        }
      >
        <p className="eyebrow">{title}</p>
        <p className="mt-4 text-base leading-7">{body}</p>
      </div>
    </div>
  );
}

function SpineHeading() {
  return (
    <div className="text-center">
      <p className="eyebrow">Live narrative radar</p>
      <h2 className="section-title mt-5">Breaking into the conversation</h2>
      <p className="section-copy mx-auto mt-4">
        The thread runs under each story, lighting it up in turn — then ends where
        you can start your own investigation.
      </p>
    </div>
  );
}

/* ── Story row: a card the curve passes beneath ── */

const StoryRow = forwardRef<
  HTMLDivElement,
  { topic: LiveTrendingTopic; index: number; lit: boolean }
>(function StoryRow({ topic, index, lit }, ref) {
  const navigate = useNavigate();
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isStarting, setIsStarting] = useState(false);
  const isLeft = index % 2 === 0;
  const label = String(index + 1).padStart(2, "0");

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
    <div className="grid grid-cols-1 py-10 md:grid-cols-2 md:gap-x-24 md:py-14">
      <div
        ref={ref}
        className={isLeft ? "md:col-start-1" : "md:col-start-2"}
      >
        <StoryCard
          topic={topic}
          label={label}
          alignRight={isLeft}
          lit={lit}
          disabled={isStarting}
          onClick={handleInvestigate}
        />
        {errorMessage ? (
          <p className="mt-4 rounded-md border border-[var(--error-border)] bg-[var(--error-surface)] px-4 py-3 text-sm leading-6 text-[var(--error-ink)]">
            {errorMessage}
          </p>
        ) : null}
      </div>
    </div>
  );
});

function StoryCard({
  topic,
  label,
  alignRight,
  lit,
  disabled,
  onClick,
}: {
  topic: LiveTrendingTopic;
  label: string;
  alignRight: boolean;
  lit: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <motion.button
      type="button"
      onClick={onClick}
      disabled={disabled}
      animate={{
        opacity: lit ? 1 : 0.4,
        y: lit ? 0 : 16,
        borderColor: lit ? "var(--border)" : "rgba(255, 173, 97, 0.18)",
      }}
      transition={{ duration: 0.5, ease: EASE_OUT }}
      whileHover={{ y: -4 }}
      className="group block w-full rounded-md border bg-[var(--surface-strong)] p-6 text-left shadow-[0_18px_48px_-30px_rgba(0,0,0,0.8)] backdrop-blur-md transition-[border-color,box-shadow] hover:border-[var(--accent)] hover:shadow-[0_0_32px_rgba(255,173,97,0.2)] disabled:cursor-wait disabled:opacity-70"
    >
      <div
        className={`flex items-baseline gap-3 ${alignRight ? "md:flex-row-reverse" : ""}`}
      >
        <span className="font-mono text-sm font-semibold tracking-normal text-[var(--accent)]">
          {label}
        </span>
        <span className="font-mono text-[0.66rem] font-semibold uppercase tracking-normal text-[var(--muted)]">
          {topic.status}
        </span>
      </div>

      <h3 className="mt-3 text-2xl font-semibold tracking-normal text-[var(--ink)]">
        {topic.title}
      </h3>
      <p className="mt-3 text-[0.95rem] leading-7 text-[var(--muted)]">
        {topic.summary}
      </p>

      <div
        className={`mt-5 flex flex-wrap items-center gap-x-5 gap-y-2 text-sm ${
          alignRight ? "md:justify-end" : ""
        }`}
      >
        <Meta label="Spike" value={`${topic.velocity_score.toFixed(1)}x`} emphasized />
        <Meta label="Sources" value={String(topic.source_count)} />
        <Meta label="Confidence" value={topic.confidence_label} />
      </div>

      <span
        className={`mt-5 inline-flex items-center gap-1.5 text-sm font-semibold text-[var(--ink)] transition-colors group-hover:text-[var(--accent)] ${
          alignRight ? "md:flex-row-reverse" : ""
        }`}
      >
        {disabled ? "Starting..." : "Investigate"}
        <span
          aria-hidden="true"
          className="transition-transform group-hover:translate-x-1"
        >
          →
        </span>
      </span>
    </motion.button>
  );
}

function Meta({
  label,
  value,
  emphasized,
}: {
  label: string;
  value: string;
  emphasized?: boolean;
}) {
  return (
    <span className="inline-flex items-baseline gap-1.5">
      <span className="text-[var(--muted)]">{label}</span>
      <span
        className={`font-semibold ${emphasized ? "text-[var(--accent)]" : "text-[var(--ink)]"}`}
      >
        {value}
      </span>
    </span>
  );
}

/* ════════════════════════════════════════════════════════════
   PROMPT — where the thread terminates
════════════════════════════════════════════════════════════ */

const PromptModule = forwardRef<HTMLDivElement>(function PromptModule(_props, ref) {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = query.trim();
    if (!trimmed) {
      inputRef.current?.focus();
      return;
    }
    setErrorMessage(null);
    setIsSubmitting(true);
    try {
      const response = await createInvestigation(trimmed);
      navigate(createInvestigationHref(response.investigation_id, trimmed));
    } catch (error) {
      setErrorMessage(
        error instanceof ApiError
          ? error.message
          : "Unable to start the investigation right now.",
      );
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <motion.div
      ref={ref}
      initial={{ opacity: 0, y: 24 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.4 }}
      transition={{ duration: 0.6, ease: EASE_OUT }}
      className="mx-auto mt-2 max-w-2xl text-center"
    >
      <p className="eyebrow">The thread ends with you</p>
      <h2 className="mt-5 text-4xl font-semibold tracking-normal text-[var(--ink)] sm:text-5xl">
        Start your own investigation.
      </h2>
      <p className="mx-auto mt-4 max-w-lg text-base leading-7 text-[var(--muted)]">
        Paste a headline, claim, article URL, or topic. RhetoriQ traces it back
        to the source.
      </p>

      <form onSubmit={handleSubmit} className="mt-8">
        <div className="flex flex-col gap-3 sm:flex-row">
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Paste a headline, claim, article URL, or topic…"
            disabled={isSubmitting}
            className="w-full rounded-md border border-[var(--border)] bg-[var(--surface-strong)] px-6 py-4 text-base text-[var(--ink)] shadow-[0_18px_40px_-30px_rgba(0,0,0,0.8)] outline-none backdrop-blur-md transition placeholder:text-[color:rgba(230,191,165,0.7)] focus:border-[var(--accent)] focus:ring-4 focus:ring-[rgba(255,173,97,0.2)]"
          />
          <button
            type="submit"
            disabled={isSubmitting}
            className="shrink-0 rounded-md border border-[var(--accent)] bg-[var(--accent)] px-7 py-4 text-base font-semibold text-[#171016] transition hover:-translate-y-0.5 hover:bg-[#ffc184] hover:shadow-[0_0_28px_rgba(255,173,97,0.48)] disabled:opacity-60"
          >
            {isSubmitting ? "Starting…" : "Investigate"}
          </button>
        </div>
      </form>

      {errorMessage ? (
        <p className="mx-auto mt-4 max-w-lg rounded-md border border-[var(--error-border)] bg-[var(--error-surface)] px-4 py-3 text-sm leading-6 text-[var(--error-ink)]">
          {errorMessage}
        </p>
      ) : null}

      <p className="mt-16 pb-10 font-mono text-xs font-medium uppercase tracking-normal text-[var(--muted)]">
        Source-grounded · Nonpartisan by design · Built for human review
      </p>
    </motion.div>
  );
});

function SiteFooter() {
  return (
    <footer className="px-4 pb-12 pt-10 text-center sm:px-6">
      <p className="font-mono text-[0.72rem] font-medium uppercase tracking-normal text-[var(--muted)]">
        UC Berkeley AI Hackathon 2021
      </p>
    </footer>
  );
}
