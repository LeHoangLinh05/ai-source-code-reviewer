import { describe, expect, it, vi } from "vitest";

import {
  parseFixJobProgressEvent,
  shouldReconcileFixJobProgress,
  subscribeToFixJobProgress,
  type FixJobProgressConnectionState,
  type FixJobProgressEventSource,
} from "@/lib/fix-job-progress";

class FakeEventSource implements FixJobProgressEventSource {
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
    fix_id: "fix-1",
    review_job_id: "job-1",
    event: "progress_update",
    status: "VALIDATING",
    progress: 80,
    message: "Running validation",
    timestamp: "2026-07-30T10:00:00Z",
    data: { changed_files: ["src/app.py"] },
    ...overrides,
  });
}

describe("fix job progress SSE", () => {
  it("decodes fix progress payloads and rejects invalid values", () => {
    expect(parseFixJobProgressEvent(buildPayload())).toMatchObject({
      progress: 80,
      status: "VALIDATING",
    });
    expect(parseFixJobProgressEvent(buildPayload({ progress: 101 }))).toBeNull();
    expect(parseFixJobProgressEvent(buildPayload({ status: "UNKNOWN" }))).toBeNull();
    expect(parseFixJobProgressEvent("not json")).toBeNull();
  });

  it("reconciles only disconnected active fix jobs", () => {
    expect(shouldReconcileFixJobProgress("reconnecting", "VALIDATING")).toBe(
      true,
    );
    expect(shouldReconcileFixJobProgress("closed", "PREPARING")).toBe(true);
    expect(shouldReconcileFixJobProgress("open", "VALIDATING")).toBe(false);
    expect(shouldReconcileFixJobProgress("closed", "WAITING_APPROVAL")).toBe(
      false,
    );
    expect(
      shouldReconcileFixJobProgress(
        "closed",
        "WAITING_APPROVAL",
        "PUBLISHING",
      ),
    ).toBe(true);
  });

  it("tracks connection states and closes after a ready event", () => {
    const source = new FakeEventSource();
    const states: FixJobProgressConnectionState[] = [];
    const events: string[] = [];
    const cleanup = subscribeToFixJobProgress("/api/fixes/fix-1/stream", {
      eventSourceFactory: () => source,
      onEvent: (event) => events.push(event.event),
      onStateChange: (state) => states.push(state),
    });

    source.emitOpen();
    source.emit("progress_update", buildPayload());
    source.emit(
      "completed",
      buildPayload({
        event: "completed",
        progress: 100,
        status: "WAITING_APPROVAL",
      }),
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
    const cleanup = subscribeToFixJobProgress("/stream", {
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
});
