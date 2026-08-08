import { refreshAuthenticatedSession } from "@/lib/api";
import type {
  FixJobProgressEvent,
  FixJobProgressEventType,
  FixJobStatus,
  FixPublishStatus,
} from "@/types/fix-job";

export type FixJobProgressConnectionState =
  | "connecting"
  | "open"
  | "reconnecting"
  | "closed";

export type FixJobProgressEventSource = {
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

type FixJobProgressEventSourceFactory = (
  url: string,
  init: EventSourceInit,
) => FixJobProgressEventSource;

type SubscribeToFixJobProgressOptions = {
  onEvent: (event: FixJobProgressEvent) => void;
  onStateChange?: (state: FixJobProgressConnectionState) => void;
  refreshSession?: () => Promise<void>;
  eventSourceFactory?: FixJobProgressEventSourceFactory;
};

const EVENT_TYPES: readonly FixJobProgressEventType[] = [
  "status_change",
  "progress_update",
  "log",
  "completed",
  "failed",
];

const FIX_JOB_STATUSES: readonly FixJobStatus[] = [
  "PENDING",
  "PREPARING",
  "GENERATING_PATCH",
  "VALIDATING",
  "WAITING_APPROVAL",
  "APPROVED",
  "FAILED",
];

const TERMINAL_FIX_JOB_STATUSES: ReadonlySet<FixJobStatus> = new Set([
  "WAITING_APPROVAL",
  "APPROVED",
  "FAILED",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseFixJobProgressEvent(
  rawPayload: string,
): FixJobProgressEvent | null {
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
    typeof payload.fix_id !== "string" ||
    typeof payload.review_job_id !== "string" ||
    typeof event !== "string" ||
    !EVENT_TYPES.includes(event as FixJobProgressEventType) ||
    typeof status !== "string" ||
    !FIX_JOB_STATUSES.includes(status as FixJobStatus) ||
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
    fix_id: payload.fix_id,
    review_job_id: payload.review_job_id,
    event: event as FixJobProgressEventType,
    status: status as FixJobStatus,
    progress,
    message: payload.message,
    timestamp: payload.timestamp,
    data: payload.data,
  };
}

export function shouldReconcileFixJobProgress(
  connectionState: FixJobProgressConnectionState,
  status: FixJobStatus,
  publishStatus?: FixPublishStatus,
): boolean {
  const isDisconnected =
    connectionState === "reconnecting" || connectionState === "closed";
  return (
    isDisconnected &&
    (publishStatus === "PUBLISHING" || !TERMINAL_FIX_JOB_STATUSES.has(status))
  );
}

export function subscribeToFixJobProgress(
  streamUrl: string,
  {
    onEvent,
    onStateChange,
    refreshSession,
    eventSourceFactory,
  }: SubscribeToFixJobProgressOptions,
): () => void {
  const createEventSource =
    eventSourceFactory ??
    ((url, init) => new EventSource(url, init) as FixJobProgressEventSource);
  const refresh = refreshSession ?? refreshAuthenticatedSession;
  let isClosed = false;
  let refreshInFlight: Promise<void> | null = null;
  const source = createEventSource(streamUrl, { withCredentials: true });
  const setState = (state: FixJobProgressConnectionState) => {
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
    const progressEvent = parseFixJobProgressEvent(event.data);
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
