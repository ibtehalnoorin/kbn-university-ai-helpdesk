const ACTIVE_SESSION_KEY = "kbn-helpdesk:active-session";

export function readActiveSession(): string | null {
  try {
    return window.localStorage.getItem(ACTIVE_SESSION_KEY);
  } catch {
    return null;
  }
}

export function writeActiveSession(sessionId: string | null): void {
  try {
    if (sessionId) {
      window.localStorage.setItem(ACTIVE_SESSION_KEY, sessionId);
    } else {
      window.localStorage.removeItem(ACTIVE_SESSION_KEY);
    }
  } catch {
    // Private browsing or storage disabled: state still lives in memory.
  }
}

const CLIENT_ID_KEY = "kbn-helpdesk:client-id";
let memoryClientId: string | null = null;

/** Anonymous per-browser id sent as X-Client-Id so visitors only see their own chats. */
export function getClientId(): string {
  try {
    const stored = window.localStorage.getItem(CLIENT_ID_KEY);
    if (stored) return stored;
  } catch {
    // Storage disabled: fall back to an id kept for this page load only.
  }
  if (!memoryClientId) {
    memoryClientId = crypto.randomUUID().replace(/-/g, "");
    try {
      window.localStorage.setItem(CLIENT_ID_KEY, memoryClientId);
    } catch {
      // ignore
    }
  }
  return memoryClientId;
}
