import { refreshAuthenticatedSession } from "@/lib/api";
import type {
  JobProgressEvent,
  JobProgressEventType,
  ReviewJobStatus,
} from "@/types/review-job";

export type JobProgressConnectionState =
  | "connecting"
  | "open"
  | "reconnecting"
  | "closed";

export type JobProgressEventSource = {
  onerror: ((event: Event) => void) | null;
  onopen: ((event: Event) => void) | null;
  addEventListener: (
    type: string,
    listener: (event: MessageEvent<string>) => void,
  ) => void;
  removeEventListener: (
    type: string,
    listener: (event: MessageEvent<string>) => void,
  ) => void;
  close: () => void;
};

type JobProgressEventSourceFactory = (
  url: string,
  init: EventSourceInit,
) => JobProgressEventSource;

type SubscribeToJobProgressOptions = {
  onEvent: (event: JobProgressEvent) => void;
  onStateChange?: (state: JobProgressConnectionState) => void;
  refreshSession?: () => Promise<void>;
  eventSourceFactory?: JobProgressEventSourceFactory;
};

const EVENT_TYPES: readonly JobProgressEventType[] = [
  "status_change",
  "progress_update",
  "log",
  "completed",
  "failed",
];

const JOB_STATUSES: readonly ReviewJobStatus[] = [
  "PENDING",
  "CLONING",
  "ANALYZING_STRUCTURE",
  "GENERATING_SUMMARY",
  "RUNNING_STATIC_ANALYSIS",
  "CHUNKING_CODE",
  "AI_REVIEWING",
  "GENERATING_REPORT",
  "COMPLETED",
  "FAILED",
];

const TERMINAL_JOB_STATUSES: ReadonlySet<ReviewJobStatus> = new Set([
  "COMPLETED",
  "FAILED",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseJobProgressEvent(
  rawPayload: string,
): JobProgressEvent | null {
  let payload: unknown;
  try {
    payload = JSON.parse(rawPayload);
  } catch {
    return null;
  }

  if (!isRecord(payload)) {
    return null;
  }

  const event = payload.event;
  const progress = payload.progress;
  const status = payload.status;
  if (
    typeof payload.job_id !== "string" ||
    typeof event !== "string" ||
    !EVENT_TYPES.includes(event as JobProgressEventType) ||
    typeof status !== "string" ||
    !JOB_STATUSES.includes(status as ReviewJobStatus) ||
    typeof progress !== "number" ||
    !Number.isInteger(progress) ||
    progress < 0 ||
    progress > 100 ||
    typeof payload.message !== "string" ||
    typeof payload.timestamp !== "string" ||
    !isRecord(payload.data)
  ) {
    return null;
  }

  return {
    job_id: payload.job_id,
    event: event as JobProgressEventType,
    status: status as ReviewJobStatus,
    progress,
    message: payload.message,
    timestamp: payload.timestamp,
    data: payload.data,
  };
}

export function shouldPollJobProgress(status: ReviewJobStatus): boolean {
  return !TERMINAL_JOB_STATUSES.has(status);
}

export function subscribeToJobProgress(
  streamUrl: string,
  {
    onEvent,
    onStateChange,
    refreshSession,
    eventSourceFactory,
  }: SubscribeToJobProgressOptions,
): () => void {
  const createEventSource =
    eventSourceFactory ??
    ((url, init) => new EventSource(url, init) as JobProgressEventSource);
  const refresh = refreshSession ?? refreshAuthenticatedSession;
  let isClosed = false;
  let refreshInFlight: Promise<void> | null = null;
  const source = createEventSource(streamUrl, { withCredentials: true });
  const setState = (state: JobProgressConnectionState) => {
    if (!isClosed || state === "closed") {
      onStateChange?.(state);
    }
  };

  const eventListeners = EVENT_TYPES.map((eventType) => {
    source.addEventListener(eventType, handleMessage);
    return eventType;
  });

  const closeConnection = () => {
    if (isClosed) {
      return;
    }
    isClosed = true;
    eventListeners.forEach((eventType) => {
      source.removeEventListener(eventType, handleMessage);
    });
    source.onerror = null;
    source.onopen = null;
    source.close();
    setState("closed");
  };

  function handleMessage(event: MessageEvent<string>) {
    const progressEvent = parseJobProgressEvent(event.data);
    if (!progressEvent) {
      return;
    }

    onEvent(progressEvent);
    if (progressEvent.event === "completed" || progressEvent.event === "failed") {
      closeConnection();
    }
  }

  source.onopen = () => setState("open");
  source.onerror = () => {
    if (isClosed) {
      return;
    }
    setState("reconnecting");
    refreshInFlight ??= refresh()
      .catch(() => {
        closeConnection();
      })
      .finally(() => {
        refreshInFlight = null;
      });
  };
  setState("connecting");

  return () => {
    closeConnection();
  };
}
