import { useCallback, useEffect, useRef, useState } from "react";
import { BookOpenText, RefreshCw, ServerCrash } from "lucide-react";
import { ApiError, api, isBusy, isTimeout } from "./api/client";
import type {
  HealthResponse,
  Message,
  SessionDetail,
  SessionSummary,
} from "./api/types";
import { ChatView, type ChatError } from "./components/ChatView";
import { ConfirmDialog } from "./components/ConfirmDialog";
import { Sidebar } from "./components/Sidebar";
import { readActiveSession, writeActiveSession } from "./lib/storage";

interface PendingState {
  sessionId: string;
  startedAt: number;
  late: boolean;
}

type ActionError = ChatError & { retry?: () => void };

const POLL_INTERVAL_MS = 10_000;
const MAX_POLL_MS = 2.5 * 60 * 60 * 1000;

function messageOf(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "An unexpected error occurred.";
}

function toSummary(detail: SessionDetail): SessionSummary {
  return {
    id: detail.id,
    title: detail.title,
    created_at: detail.created_at,
    updated_at: detail.updated_at,
    message_count: detail.message_count,
    preview: detail.preview,
  };
}

function optimisticUserMessage(content: string): Message {
  const id =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? `local_${crypto.randomUUID()}`
      : `local_${Date.now()}_${Math.random().toString(16).slice(2)}`;
  return {
    id,
    role: "user",
    content,
    created_at: new Date().toISOString(),
    sources: [],
    grounded: false,
  };
}

export default function App() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [activeSession, setActiveSession] = useState<SessionDetail | null>(null);
  const [bootLoading, setBootLoading] = useState(true);
  const [bootError, setBootError] = useState<string | null>(null);
  const [bootNonce, setBootNonce] = useState(0);
  const [sessionLoading, setSessionLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [sending, setSending] = useState(false);
  const [pending, setPending] = useState<PendingState | null>(null);
  const [error, setError] = useState<ActionError | null>(null);
  const [draft, setDraft] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<SessionSummary | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [healthLoading, setHealthLoading] = useState(true);

  const activeIdRef = useRef<string | null>(null);
  const pendingRef = useRef<PendingState | null>(null);
  const sendingRef = useRef(false);
  const selectTokenRef = useRef(0);
  const pollTimerRef = useRef<number | null>(null);
  const pollTokenRef = useRef(0);

  useEffect(() => {
    activeIdRef.current = activeId;
  }, [activeId]);

  useEffect(() => {
    pendingRef.current = pending;
  }, [pending]);

  const cancelPolling = useCallback(() => {
    pollTokenRef.current += 1;
    if (pollTimerRef.current !== null) {
      window.clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  useEffect(() => () => cancelPolling(), [cancelPolling]);

  const refreshHealth = useCallback(async () => {
    try {
      const result = await api.health();
      setHealth(result);
    } catch {
      setHealth(null);
    } finally {
      setHealthLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (!cancelled) setHealthLoading(true);
      try {
        const result = await api.health();
        if (!cancelled) setHealth(result);
      } catch {
        if (!cancelled) setHealth(null);
      } finally {
        if (!cancelled) setHealthLoading(false);
      }
    }
    void load();
    const id = window.setInterval(load, 60_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  const refreshSessions = useCallback(async () => {
    const list = await api.listSessions();
    setSessions(list.sessions);
    return list.sessions;
  }, []);

  const activate = useCallback((detail: SessionDetail) => {
    setActiveSession(detail);
    setActiveId(detail.id);
    activeIdRef.current = detail.id;
    writeActiveSession(detail.id);
  }, []);

  const boot = useCallback(async () => {
    setBootLoading(true);
    setBootError(null);
    try {
      let list: SessionSummary[];
      try {
        list = (await api.listSessions()).sessions;
      } catch (listError) {
        throw listError;
      }

      const stored = readActiveSession();
      let targetId: string | null =
        stored && list.some((session) => session.id === stored)
          ? stored
          : (list[0]?.id ?? null);

      let detail: SessionDetail;
      if (targetId) {
        try {
          detail = await api.getSession(targetId);
        } catch {
          targetId = null;
          detail = await api.createSession();
        }
      } else {
        detail = await api.createSession();
      }

      setSessions(
        list.some((session) => session.id === detail.id)
          ? list.map((session) =>
              session.id === detail.id ? toSummary(detail) : session,
            )
          : [toSummary(detail), ...list],
      );
      activate(detail);
    } catch (bootFailure) {
      setBootError(messageOf(bootFailure));
    } finally {
      setBootLoading(false);
    }
  }, [activate]);

  useEffect(() => {
    void boot();
  }, [boot, bootNonce]);

  const selectSession = useCallback(
    async (id: string) => {
      setSidebarOpen(false);
      setError(null);
      if (id === activeIdRef.current && activeSession) return;

      cancelPolling();
      setPending(null);
      const token = ++selectTokenRef.current;
      setSessionLoading(true);
      setActiveSession(null);
      setActiveId(id);
      activeIdRef.current = id;
      writeActiveSession(id);

      try {
        const detail = await api.getSession(id);
        if (token !== selectTokenRef.current) return;
        setActiveSession(detail);
        setSessions((current) =>
          current.map((session) =>
            session.id === id ? toSummary(detail) : session,
          ),
        );
      } catch (selectionError) {
        if (token !== selectTokenRef.current) return;
        setError({
          title: "Could not open this conversation",
          message: messageOf(selectionError),
          retry: () => void selectSession(id),
        });
        setActiveId(null);
        activeIdRef.current = null;
        setActiveSession(null);
      } finally {
        if (token === selectTokenRef.current) setSessionLoading(false);
      }
    },
    [activeSession, cancelPolling],
  );

  const createNew = useCallback(async () => {
    if (creating) return;
    setCreating(true);
    setError(null);
    cancelPolling();
    setPending(null);
    try {
      const detail = await api.createSession();
      setSessions((current) => [toSummary(detail), ...current]);
      activate(detail);
      setDraft("");
      setSidebarOpen(false);
      selectTokenRef.current += 1;
      setSessionLoading(false);
    } catch (creationError) {
      setError({
        title: "Could not start a new conversation",
        message: messageOf(creationError),
        retry: () => void createNew(),
      });
    } finally {
      setCreating(false);
    }
  }, [activate, cancelPolling, creating]);

  const startPolling = useCallback(
    (sessionId: string, baselineIds: Set<string>, startedAt: number) => {
      cancelPolling();
      const token = pollTokenRef.current;

      const tick = async () => {
        if (token !== pollTokenRef.current) return;
        if (Date.now() - startedAt > MAX_POLL_MS) {
          setPending(null);
          setError({
            title: "The answer did not arrive",
            message:
              "The assistant did not finish in time. Please try the question again.",
          });
          return;
        }

        let detail: SessionDetail;
        try {
          detail = await api.getSession(sessionId);
        } catch {
          pollTimerRef.current = window.setTimeout(tick, POLL_INTERVAL_MS);
          return;
        }
        if (token !== pollTokenRef.current) return;

        const hasNewAnswer = detail.messages.some(
          (message) => message.role === "assistant" && !baselineIds.has(message.id),
        );

        if (hasNewAnswer) {
          setPending(null);
          pollTokenRef.current += 1;
          if (activeIdRef.current === sessionId) {
            setActiveSession(detail);
          }
          setSessions((current) =>
            current.map((session) =>
              session.id === sessionId ? toSummary(detail) : session,
            ),
          );
          void refreshSessions();
          void refreshHealth();
          return;
        }

        if (activeIdRef.current === sessionId) {
          setActiveSession((current) =>
            current && current.id === sessionId ? detail : current,
          );
        }
        pollTimerRef.current = window.setTimeout(tick, POLL_INTERVAL_MS);
      };

      pollTimerRef.current = window.setTimeout(tick, 8_000);
    },
    [cancelPolling, refreshHealth, refreshSessions],
  );

  const sendMessage = useCallback(
    async (override?: string) => {
      const text = (override ?? draft).trim();
      if (!text || sendingRef.current) return;
      const sessionId = activeIdRef.current;
      if (!sessionId) return;
      if (pendingRef.current?.sessionId === sessionId) return;

      const baselineIds = new Set(
        activeSession?.messages.map((message) => message.id) ?? [],
      );
      const optimistic = optimisticUserMessage(text);

      sendingRef.current = true;
      setSending(true);
      setError(null);
      setDraft("");
      setPending({ sessionId, startedAt: Date.now(), late: false });
      setActiveSession((current) => {
        if (!current || current.id !== sessionId) return current;
        return { ...current, messages: [...current.messages, optimistic] };
      });

      try {
        const response = await api.sendChat(sessionId, text);
        if (activeIdRef.current === sessionId) {
          let detail: SessionDetail | null = null;
          try {
            detail = await api.getSession(sessionId);
          } catch {
            detail = null;
          }
          setActiveSession((current) => {
            if (!current || current.id !== sessionId) return current;
            if (detail) return detail;
            return {
              ...current,
              messages: [
                ...current.messages.filter(
                  (message) => message.id !== optimistic.id,
                ),
                response.user_message,
                response.assistant_message,
              ],
            };
          });
        }
        setPending(null);
        void refreshSessions();
        void refreshHealth();
      } catch (sendError) {
        if (isTimeout(sendError) || isBusy(sendError)) {
          setPending({
            sessionId,
            startedAt: Date.now(),
            late: true,
          });
          startPolling(sessionId, baselineIds, Date.now());
        } else {
          if (activeIdRef.current === sessionId) {
            setActiveSession((current) => {
              if (!current || current.id !== sessionId) return current;
              return {
                ...current,
                messages: current.messages.filter(
                  (message) => message.id !== optimistic.id,
                ),
              };
            });
          }
          setPending(null);
          setDraft((current) => current || text);
          setError({
            title: "The message could not be answered",
            message: messageOf(sendError),
            retry: () => void sendMessage(text),
          });
        }
      } finally {
        sendingRef.current = false;
        setSending(false);
      }
    },
    [activeSession, draft, refreshHealth, refreshSessions, startPolling],
  );

  const clearConversation = useCallback(async () => {
    const id = activeIdRef.current;
    if (!id) return;
    if (pendingRef.current?.sessionId === id) {
      setError({
        title: "Answer in progress",
        message:
          "Wait for the current answer to finish before clearing this conversation.",
      });
      return;
    }
    cancelPolling();
    setPending(null);
    try {
      const detail = await api.clearSession(id);
      if (activeIdRef.current === id) setActiveSession(detail);
      setDraft("");
      setError(null);
      void refreshSessions();
    } catch (clearError) {
      setError({
        title: "Could not clear this conversation",
        message: messageOf(clearError),
      });
    }
  }, [cancelPolling, refreshSessions]);

  const renameSession = useCallback(
    async (id: string, title: string) => {
      const detail = await api.renameSession(id, title);
      setSessions((current) =>
        current.map((session) => (session.id === id ? toSummary(detail) : session)),
      );
      setActiveSession((current) =>
        current && current.id === id ? { ...current, title: detail.title } : current,
      );
    },
    [],
  );

  const confirmDelete = useCallback(async () => {
    const target = deleteTarget;
    if (!target) return;
    if (pendingRef.current?.sessionId === target.id) {
      setDeleteTarget(null);
      setError({
        title: "Answer in progress",
        message:
          "This conversation is still waiting for an answer and cannot be deleted yet.",
      });
      return;
    }

    setDeleting(true);
    try {
      await api.deleteSession(target.id);
      const remaining = sessions.filter((session) => session.id !== target.id);
      setSessions(remaining);
      setDeleteTarget(null);

      if (activeIdRef.current === target.id) {
        cancelPolling();
        setPending(null);
        setActiveSession(null);
        setActiveId(null);
        activeIdRef.current = null;
        writeActiveSession(null);
        if (remaining.length > 0) {
          await selectSession(remaining[0].id);
        } else {
          await createNew();
        }
      }
    } catch (deleteError) {
      setDeleteTarget(null);
      setError({
        title: "Could not delete the conversation",
        message: messageOf(deleteError),
      });
    } finally {
      setDeleting(false);
    }
  }, [cancelPolling, createNew, deleteTarget, selectSession, sessions]);

  const busy = sending || (pending !== null && pending.sessionId === activeId);

  if (bootLoading) {
    return (
      <div className="helpdesk-bg flex min-h-dvh items-center justify-center px-6">
        <div className="text-center">
          <span className="mx-auto grid h-14 w-14 animate-pulse-soft place-items-center rounded-2xl border border-gold-300/70 bg-gradient-to-br from-kbn-800 to-kbn-950 text-gold-300 shadow-lg shadow-kbn-950/20">
            <BookOpenText className="h-7 w-7" aria-hidden="true" />
          </span>
          <h1 className="mt-4 font-display text-xl font-semibold text-kbn-950">
            KBN University Helpdesk
          </h1>
          <p className="mt-1 text-sm text-stone-500">Connecting to the helpdesk…</p>
        </div>
      </div>
    );
  }

  if (bootError) {
    return (
      <div className="helpdesk-bg flex min-h-dvh items-center justify-center px-6 py-10">
        <div className="w-full max-w-md rounded-2xl border border-rose-200 bg-white p-6 text-center shadow-xl shadow-kbn-950/10">
          <span className="mx-auto grid h-12 w-12 place-items-center rounded-full bg-rose-50 text-rose-600">
            <ServerCrash className="h-6 w-6" aria-hidden="true" />
          </span>
          <h1 className="mt-4 font-display text-xl font-semibold text-kbn-950">
            The helpdesk backend is unavailable
          </h1>
          <p className="mt-2 text-sm leading-relaxed text-stone-600">{bootError}</p>
          <div className="mt-4 rounded-lg bg-sand-100 px-3 py-2 text-left font-mono text-[0.68rem] leading-relaxed text-stone-600">
            .venv\Scripts\python.exe backend\run_server.py
          </div>
          <button
            type="button"
            onClick={() => setBootNonce((value) => value + 1)}
            className="mt-5 inline-flex items-center gap-2 rounded-xl bg-kbn-800 px-4 py-2.5 text-sm font-semibold text-white hover:bg-kbn-700"
          >
            <RefreshCw className="h-4 w-4" aria-hidden="true" />
            Retry connection
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="helpdesk-bg flex h-dvh w-full overflow-hidden">
      <Sidebar
        sessions={sessions}
        activeId={activeId}
        health={health}
        healthLoading={healthLoading}
        creating={creating}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        onSelect={(id) => void selectSession(id)}
        onCreate={() => void createNew()}
        onRename={renameSession}
        onRequestDelete={(session) => setDeleteTarget(session)}
      />

      <ChatView
        session={activeSession}
        loadingSession={sessionLoading}
        busy={busy}
        pendingStartedAt={pending?.startedAt ?? null}
        pendingLate={Boolean(pending?.late)}
        error={error}
        draft={draft}
        onDraftChange={setDraft}
        onSend={() => void sendMessage()}
        onPickSuggestion={(prompt) => void sendMessage(prompt)}
        onOpenSidebar={() => setSidebarOpen(true)}
        onClear={() => void clearConversation()}
        onDismissError={() => setError(null)}
        onRetryError={error?.retry}
      />

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete this conversation?"
        message={
          deleteTarget
            ? `"${deleteTarget.title}" and its transcript will be permanently removed from this device.`
            : ""
        }
        confirmLabel="Delete conversation"
        busy={deleting}
        onConfirm={() => void confirmDelete()}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
