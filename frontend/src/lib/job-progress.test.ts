import { describe, expect, it, vi } from "vitest";

import {
  parseJobProgressEvent,
  shouldPollJobProgress,
  subscribeToJobProgress,
  type JobProgressConnectionState,
  type JobProgressEventSource,
} from "@/lib/job-progress";

class FakeEventSource implements JobProgressEventSource {
  onerror: ((event: Event) => void) | null = null;
  onopen: ((event: Event) => void) | null = null;
  closed = false;
  listeners = new Map<string, Set<(event: MessageEvent<string>) => void>>();

  addEventListener(
    type: string,
    listener: (event: MessageEvent<string>) => void,
  ) {
    const listeners = this.listeners.get(type) ?? new Set();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(
    type: string,
    listener: (event: MessageEvent<string>) => void,
  ) {
    this.listeners.get(type)?.delete(listener);
  }

  close() {
    this.closed = true;
  }

  emitOpen() {
    this.onopen?.({} as Event);
  }

  emitError() {
    this.onerror?.({} as Event);
  }

  emit(type: string, payload: string) {
    const event = { data: payload } as MessageEvent<string>;
    this.listeners.get(type)?.forEach((listener) => listener(event));
  }
}

function buildPayload(overrides: Record<string, unknown> = {}) {
  return JSON.stringify({
    job_id: "job-1",
    event: "progress_update",
    status: "AI_REVIEWING",
    progress: 72,
    message: "Reviewing code",
    timestamp: "2026-07-28T10:00:00Z",
    data: { chunk: 2 },
    ...overrides,
  });
}

describe("job progress SSE", () => {
  it("decodes the normalized payload and rejects invalid values", () => {
    expect(parseJobProgressEvent(buildPayload())).toMatchObject({
      event: "progress_update",
      progress: 72,
    });
    expect(parseJobProgressEvent(buildPayload({ progress: 101 }))).toBeNull();
    expect(parseJobProgressEvent(buildPayload({ status: "UNKNOWN" }))).toBeNull();
    expect(parseJobProgressEvent("not json")).toBeNull();
  });

  it("polls every non-terminal review status", () => {
    expect(shouldPollJobProgress("PENDING")).toBe(true);
    expect(shouldPollJobProgress("AI_REVIEWING")).toBe(true);
    expect(shouldPollJobProgress("GENERATING_REPORT")).toBe(true);
    expect(shouldPollJobProgress("COMPLETED")).toBe(false);
    expect(shouldPollJobProgress("FAILED")).toBe(false);
  });

  it("tracks connection states and closes after a terminal event", () => {
    const source = new FakeEventSource();
    const states: JobProgressConnectionState[] = [];
    const events: string[] = [];
    const cleanup = subscribeToJobProgress("/api/review-jobs/job-1/stream", {
      eventSourceFactory: () => source,
      onEvent: (event) => events.push(event.event),
      onStateChange: (state) => states.push(state),
    });

    source.emitOpen();
    source.emit("progress_update", buildPayload());
    source.emit(
      "completed",
      buildPayload({ event: "completed", status: "COMPLETED", progress: 100 }),
    );
    cleanup();

    expect(states).toEqual(["connecting", "open", "closed"]);
    expect(events).toEqual(["progress_update", "completed"]);
    expect(source.closed).toBe(true);
    expect(source.listeners.get("completed")).toHaveLength(0);
  });

  it("refreshes the session once while EventSource reconnects", async () => {
    const source = new FakeEventSource();
    let resolveRefresh!: () => void;
    const refreshPromise = new Promise<void>((resolve) => {
      resolveRefresh = resolve;
    });
    const refreshSession = vi.fn(() => refreshPromise);
    const cleanup = subscribeToJobProgress("/stream", {
      eventSourceFactory: () => source,
      onEvent: () => undefined,
      refreshSession,
    });

    source.emitError();
    source.emitError();
    expect(refreshSession).toHaveBeenCalledTimes(1);

    resolveRefresh();
    await refreshPromise;
    cleanup();
  });

  it("closes the stream when the shared session refresh fails", async () => {
    const source = new FakeEventSource();
    const refreshSession = vi.fn(async () => {
      throw new Error("refresh failed");
    });
    subscribeToJobProgress("/stream", {
      eventSourceFactory: () => source,
      onEvent: () => undefined,
      refreshSession,
    });

    source.emitError();
    await Promise.resolve();
    await Promise.resolve();

    expect(refreshSession).toHaveBeenCalledOnce();
    expect(source.closed).toBe(true);
  });

  it("removes listeners and closes the source on cleanup", () => {
    const source = new FakeEventSource();
    const cleanup = subscribeToJobProgress("/stream", {
      eventSourceFactory: () => source,
      onEvent: () => undefined,
    });

    cleanup();

    expect(source.closed).toBe(true);
    expect(
      [...source.listeners.values()].every((listeners) => listeners.size === 0),
    ).toBe(true);
  });
});
