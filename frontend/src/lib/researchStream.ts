import { getResearchEventsUrl } from "./api";

export function isResearchActive(status: string | undefined) {
  return status === "queued" || status === "running";
}

type WatchOptions = {
  investigationId: string;
  runId: string;
  eventTypes: string[];
  refresh: () => Promise<boolean>; // true when the loaded run is terminal
  debounceMs: number;
  fallbackMs: number;
  refreshImmediately?: boolean;
};

// Both stream consumers use the same terminal/reconnect cleanup rules.
export function watchResearchEvents(options: WatchOptions) {
  const source = new EventSource(getResearchEventsUrl(options.investigationId, options.runId));
  let disposed = false;
  let stopped = false;
  let refreshing = false;
  let finalRefreshPending = false;
  let refreshTimer: ReturnType<typeof setTimeout> | null = null;
  let fallbackTimer: ReturnType<typeof setInterval> | null = null;

  const stop = () => {
    stopped = true;
    source.close();
    if (refreshTimer) clearTimeout(refreshTimer);
    if (fallbackTimer) clearInterval(fallbackTimer);
    refreshTimer = null;
    fallbackTimer = null;
  };
  const refresh = async (final = false) => {
    if (disposed) return;
    if (refreshing) {
      finalRefreshPending ||= final;
      return;
    }
    refreshing = true;
    try {
      if (await options.refresh()) stop();
    } catch {
      // Transient errors retain native reconnects and conservative fallback.
    } finally {
      refreshing = false;
      if (finalRefreshPending && !disposed) {
        finalRefreshPending = false;
        void refresh(true);
      }
    }
  };
  const scheduleRefresh = () => {
    if (disposed || stopped || refreshTimer) return;
    refreshTimer = setTimeout(() => {
      refreshTimer = null;
      void refresh();
    }, options.debounceMs);
  };
  options.eventTypes.forEach(type => source.addEventListener(type, scheduleRefresh));
  source.addEventListener("stream.closed", () => {
    if (disposed) return;
    stop();
    void refresh(true);
  });
  source.onerror = () => {
    if (disposed || stopped) return;
    if (source.readyState === EventSource.CLOSED) {
      // HTTP 204 stops native reconnects; don't start permanent fallback polls.
      stop();
      void refresh(true);
    } else if (!fallbackTimer) {
      fallbackTimer = setInterval(() => void refresh(), options.fallbackMs);
    }
  };
  source.onopen = () => {
    if (fallbackTimer) clearInterval(fallbackTimer);
    fallbackTimer = null;
  };
  if (options.refreshImmediately) void refresh();
  return () => {
    disposed = true;
    stop();
  };
}
