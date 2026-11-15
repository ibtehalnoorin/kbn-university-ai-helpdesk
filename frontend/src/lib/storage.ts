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
