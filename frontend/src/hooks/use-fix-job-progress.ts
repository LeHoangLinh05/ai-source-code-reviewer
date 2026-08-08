import { useEffect, useRef, useState } from "react";

import {
  subscribeToFixJobProgress,
  type FixJobProgressConnectionState,
} from "@/lib/fix-job-progress";
import type { FixJobProgressEvent } from "@/types/fix-job";

type UseFixJobProgressOptions = {
  streamUrl: string | null;
  enabled?: boolean;
  onEvent?: (event: FixJobProgressEvent) => void;
};

export function useFixJobProgress({
  streamUrl,
  enabled = true,
  onEvent,
}: UseFixJobProgressOptions): {
  connectionState: FixJobProgressConnectionState;
  progressEvent: FixJobProgressEvent | null;
} {
  const [connectionState, setConnectionState] =
    useState<FixJobProgressConnectionState>("closed");
  const [progressEvent, setProgressEvent] =
    useState<FixJobProgressEvent | null>(null);
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
    const cleanup = subscribeToFixJobProgress(streamUrl, {
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
