import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { watchResearchEvents } from "./researchStream";

class FakeEventSource extends EventTarget {
  static CLOSED = 2;
  static sources: FakeEventSource[] = [];
  readyState = 0;
  onerror: (() => void) | null = null;
  onopen: (() => void) | null = null;
  close = vi.fn(() => { this.readyState = FakeEventSource.CLOSED; });
  constructor(public url: string) {
    super();
    FakeEventSource.sources.push(this);
  }
}

function watch(refresh = vi.fn(async () => false)) {
  const stop = watchResearchEvents({ investigationId: "inv_test", runId: "run_test",
    eventTypes: ["node.completed"], debounceMs: 10, fallbackMs: 100, refresh });
  return { stop, refresh, source: FakeEventSource.sources.at(-1)! };
}

describe("run-bound stream lifecycle", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeEventSource.sources = [];
    vi.stubGlobal("EventSource", FakeEventSource);
  });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it("binds the URL to a run and debounces event bursts", async () => {
    const { source, refresh, stop } = watch();
    expect(source.url).toContain("/inv_test/events?run_id=run_test");
    source.dispatchEvent(new Event("node.completed"));
    source.dispatchEvent(new Event("node.completed"));
    await vi.advanceTimersByTimeAsync(10);
    expect(refresh).toHaveBeenCalledTimes(1);
    stop();
  });

  it("stops fallback and performs a final refresh on stream.closed", async () => {
    const { source, refresh, stop } = watch();
    source.onerror?.();
    source.dispatchEvent(new Event("stream.closed"));
    await vi.advanceTimersByTimeAsync(1000);
    expect(source.close).toHaveBeenCalled();
    expect(refresh).toHaveBeenCalledTimes(1);
    stop();
  });

  it("does not poll permanently when native reconnects stop", async () => {
    const { source, refresh, stop } = watch();
    source.readyState = FakeEventSource.CLOSED;
    source.onerror?.();
    await vi.advanceTimersByTimeAsync(1000);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(source.close).toHaveBeenCalled();
    stop();
  });

  it("cancels fallback when a connection reopens", async () => {
    const { source, refresh, stop } = watch();
    source.onerror?.();
    await vi.advanceTimersByTimeAsync(100);
    expect(refresh).toHaveBeenCalledTimes(1);
    source.onopen?.();
    await vi.advanceTimersByTimeAsync(1000);
    expect(refresh).toHaveBeenCalledTimes(1);
    stop();
  });

  it("runs the final refresh after an already pending refresh", async () => {
    let resolve!: (value: boolean) => void;
    const refresh = vi.fn(() => new Promise<boolean>(done => { resolve = done; }));
    const { source, stop } = watch(refresh);
    source.dispatchEvent(new Event("node.completed"));
    await vi.advanceTimersByTimeAsync(10);
    source.dispatchEvent(new Event("stream.closed"));
    expect(refresh).toHaveBeenCalledTimes(1);
    resolve(false);
    await vi.advanceTimersByTimeAsync(0);
    expect(refresh).toHaveBeenCalledTimes(2);
    resolve(true);
    await vi.advanceTimersByTimeAsync(0);
    stop();
  });

  it("closes streams on cleanup and opens a new binding for replay", async () => {
    const first = watch();
    first.source.onerror?.();
    first.stop();
    const cleanup = watchResearchEvents({ investigationId: "inv_test", runId: "run_replay",
      eventTypes: [], debounceMs: 10, fallbackMs: 100, refresh: vi.fn(async () => false) });
    await vi.advanceTimersByTimeAsync(1000);
    expect(first.refresh).not.toHaveBeenCalled();
    expect(first.source.close).toHaveBeenCalled();
    expect(FakeEventSource.sources.at(-1)!.url).toContain("run_id=run_replay");
    cleanup();
  });

  it("serializes initial loading and the terminal refresh", async () => {
    let resolve!: (value: boolean) => void;
    const refresh = vi.fn(() => new Promise<boolean>(done => { resolve = done; }));
    const stop = watchResearchEvents({ investigationId: "inv_test", runId: "run_test",
      eventTypes: [], debounceMs: 10, fallbackMs: 100, refresh, refreshImmediately: true });
    FakeEventSource.sources.at(-1)!.dispatchEvent(new Event("stream.closed"));
    expect(refresh).toHaveBeenCalledTimes(1);
    resolve(false);
    await vi.advanceTimersByTimeAsync(0);
    expect(refresh).toHaveBeenCalledTimes(2);
    resolve(true);
    await vi.advanceTimersByTimeAsync(0);
    stop();
  });

  it("stops the stream when refreshed state is terminal", async () => {
    const { source, refresh, stop } = watch(vi.fn(async () => true));
    source.dispatchEvent(new Event("node.completed"));
    await vi.advanceTimersByTimeAsync(10);
    source.onerror?.();
    await vi.advanceTimersByTimeAsync(1000);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(source.close).toHaveBeenCalled();
    stop();
  });
});
