import { useEffect, useRef, useState } from "react";

import {
  subscribeToJobProgress,
  type JobProgressConnectionState,
} from "@/lib/job-progress";
import type { JobProgressEvent } from "@/types/review-job";

type UseJobProgressOptions = {
  streamUrl: string | null;
  enabled?: boolean;
  onEvent?: (event: JobProgressEvent) => void;
};

export function useJobProgress({
  streamUrl,
  enabled = true,
  onEvent,
}: UseJobProgressOptions): {
  connectionState: JobProgressConnectionState;
  progressEvent: JobProgressEvent | null;
} {
  const [connectionState, setConnectionState] =
    useState<JobProgressConnectionState>("closed");
  const [progressEvent, setProgressEvent] = useState<JobProgressEvent | null>(
    null,
  );
  const onEventRef = useRef(onEvent);

  useEffect(() => {
    onEventRef.current = onEvent;
  }, [onEvent]);

  useEffect(() => {
    if (!enabled || !streamUrl) {
      setConnectionState("closed");
      return;
    }

    setProgressEvent(null);
    const cleanup = subscribeToJobProgress(streamUrl, {
      onEvent: (event) => {
        setProgressEvent(event);
        onEventRef.current?.(event);
      },
      onStateChange: setConnectionState,
    });

    return cleanup;
  }, [enabled, streamUrl]);

  return { connectionState, progressEvent };
}
