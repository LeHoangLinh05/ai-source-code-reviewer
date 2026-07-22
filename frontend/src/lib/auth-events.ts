const AUTH_EVENT_CHANNEL = "repoguard-auth-events";
const AUTH_EVENT_STORAGE_KEY = "repoguard-auth-event";

type AuthEventType = "session-cleared" | "session-updated";

type AuthEvent = {
  id: string;
  type: AuthEventType;
};

export function publishAuthEvent(type: AuthEventType) {
  if (typeof window === "undefined") {
    return;
  }

  const event: AuthEvent = {
    id: crypto.randomUUID(),
    type,
  };

  window.localStorage.setItem(AUTH_EVENT_STORAGE_KEY, JSON.stringify(event));

  if ("BroadcastChannel" in window) {
    const channel = new BroadcastChannel(AUTH_EVENT_CHANNEL);
    channel.postMessage(event);
    channel.close();
  }
}

export function subscribeToAuthEvents(
  onEvent: (eventType: AuthEventType) => void,
) {
  if (typeof window === "undefined") {
    return () => {};
  }

  const seenEventIds = new Set<string>();
  const handleEvent = (event: AuthEvent) => {
    if (seenEventIds.has(event.id)) {
      return;
    }

    seenEventIds.add(event.id);
    onEvent(event.type);
  };
  const handleStorageEvent = (event: StorageEvent) => {
    if (event.key !== AUTH_EVENT_STORAGE_KEY || event.newValue === null) {
      return;
    }

    handleEvent(JSON.parse(event.newValue) as AuthEvent);
  };
  const channel =
    "BroadcastChannel" in window
      ? new BroadcastChannel(AUTH_EVENT_CHANNEL)
      : null;

  channel?.addEventListener("message", (event: MessageEvent<AuthEvent>) => {
    handleEvent(event.data);
  });
  window.addEventListener("storage", handleStorageEvent);

  return () => {
    channel?.close();
    window.removeEventListener("storage", handleStorageEvent);
  };
}
