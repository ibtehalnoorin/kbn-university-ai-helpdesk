import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowDown,
  CircleAlert,
  Eraser,
  Loader2,
  Menu,
  MessageSquareText,
  ShieldAlert,
  X,
} from "lucide-react";
import type { SessionDetail } from "../api/types";
import { formatClock, formatDateTime } from "../lib/format";
import { Composer } from "./Composer";
import { EmptyState } from "./EmptyState";
import { LoadingIndicator } from "./LoadingIndicator";
import { MessageBubble } from "./MessageBubble";

export interface ChatError {
  title: string;
  message: string;
}

function LateNotice({ startedAt }: { startedAt: number }) {
  const [elapsed, setElapsed] = useState(() =>
    Math.max(0, (Date.now() - startedAt) / 1000),
  );

  useEffect(() => {
    const id = window.setInterval(() => {
      setElapsed(Math.max(0, (Date.now() - startedAt) / 1000));
    }, 1000);
    return () => window.clearInterval(id);
  }, [startedAt]);

  return (
    <div className="animate-fade-up rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-amber-900">
      <div className="flex items-start gap-2.5">
        <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-amber-600" aria-hidden="true" />
        <div className="min-w-0 flex-1 text-xs leading-relaxed">
          <p className="font-semibold">
            Still working — {formatClock(elapsed)} elapsed
          </p>
          <p className="mt-0.5 text-amber-800/90">
            The local model needs more time on CPU. The backend keeps generating
            and will save the verified answer to this conversation automatically.
            You can switch conversations and come back.
          </p>
        </div>
      </div>
    </div>
  );
}

export function ChatView({
  session,
  loadingSession,
  busy,
  pendingStartedAt,
  pendingLate,
  error,
  draft,
  onDraftChange,
  onSend,
  onPickSuggestion,
  onOpenSidebar,
  onClear,
  onDismissError,
  onRetryError,
}: {
  session: SessionDetail | null;
  loadingSession: boolean;
  busy: boolean;
  pendingStartedAt: number | null;
  pendingLate: boolean;
  error: ChatError | null;
  draft: string;
  onDraftChange: (value: string) => void;
  onSend: () => void;
  onPickSuggestion: (prompt: string) => void;
  onOpenSidebar: () => void;
  onClear: () => void;
  onDismissError: () => void;
  onRetryError?: () => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [atBottom, setAtBottom] = useState(true);
  const sessionIdRef = useRef<string | null>(null);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "smooth") => {
    const element = scrollRef.current;
    if (!element) return;
    element.scrollTo({ top: element.scrollHeight, behavior });
    setAtBottom(true);
  }, []);

  const messages = session?.messages ?? [];
  const messageCount = messages.length;

  useEffect(() => {
    if (sessionIdRef.current !== (session?.id ?? null)) {
      sessionIdRef.current = session?.id ?? null;
      requestAnimationFrame(() => scrollToBottom("auto"));
      return;
    }
    if (atBottom) {
      requestAnimationFrame(() => scrollToBottom("smooth"));
    }
  }, [session?.id, messageCount, pendingLate, busy, atBottom, scrollToBottom]);

  const lastMessage = messages[messages.length - 1];
  const showLoading =
    busy && (!lastMessage || lastMessage.role === "user");

  return (
    <section className="flex min-w-0 flex-1 flex-col bg-sand-50/40">
      <header className="glass sticky top-0 z-20 flex items-center gap-2 border-b border-sand-200 px-3 py-2.5 sm:px-5">
        <button
          type="button"
          onClick={onOpenSidebar}
          className="rounded-lg p-2 text-stone-500 hover:bg-sand-100 hover:text-kbn-800 lg:hidden"
          aria-label="Open conversations"
        >
          <Menu className="h-5 w-5" aria-hidden="true" />
        </button>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h2 className="truncate text-sm font-semibold text-kbn-950">
              {session?.title ?? "KBN University Helpdesk"}
            </h2>
            {session ? (
              <span className="hidden shrink-0 rounded-full border border-kbn-100 bg-kbn-50 px-2 py-0.5 text-[0.6rem] font-semibold text-kbn-700 sm:inline">
                {session.message_count} {session.message_count === 1 ? "message" : "messages"}
              </span>
            ) : null}
          </div>
          <p className="truncate text-[0.65rem] text-stone-500">
            {session
              ? `Updated ${formatDateTime(session.updated_at)}`
              : "Verified answers from KBN University records"}
          </p>
        </div>

        {session && session.message_count > 0 ? (
          <button
            type="button"
            onClick={onClear}
            disabled={busy}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-sand-200 bg-white px-2.5 py-1.5 text-xs font-medium text-stone-600 transition-colors hover:border-rose-200 hover:bg-rose-50 hover:text-rose-600 disabled:opacity-50"
            title={
              busy
                ? "Wait for the current answer before clearing"
                : "Clear this conversation"
            }
          >
            <Eraser className="h-3.5 w-3.5" aria-hidden="true" />
            <span className="hidden sm:inline">Clear</span>
          </button>
        ) : null}
      </header>

      <div
        ref={scrollRef}
        onScroll={(event) => {
          const element = event.currentTarget;
          const distance =
            element.scrollHeight - element.scrollTop - element.clientHeight;
          setAtBottom(distance < 140);
        }}
        className="scroll-slim relative flex-1 overflow-y-auto"
      >
        <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col px-3 py-4 sm:px-6 sm:py-6">
          {error ? (
            <div
              role="alert"
              className="mb-4 animate-fade-up rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-rose-800"
            >
              <div className="flex items-start gap-2.5">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                <div className="min-w-0 flex-1">
                  <p className="text-xs font-semibold">{error.title}</p>
                  <p className="mt-0.5 text-xs leading-relaxed text-rose-700/90">
                    {error.message}
                  </p>
                  {onRetryError ? (
                    <button
                      type="button"
                      onClick={onRetryError}
                      className="mt-2 rounded-md border border-rose-200 bg-white px-2.5 py-1 text-[0.65rem] font-semibold text-rose-700 hover:bg-rose-100"
                    >
                      Try again
                    </button>
                  ) : null}
                </div>
                <button
                  type="button"
                  onClick={onDismissError}
                  className="rounded-md p-1 text-rose-400 hover:bg-rose-100 hover:text-rose-600"
                  aria-label="Dismiss error"
                >
                  <X className="h-3.5 w-3.5" aria-hidden="true" />
                </button>
              </div>
            </div>
          ) : null}

          {loadingSession && !session ? (
            <div className="flex flex-1 items-center justify-center text-sm text-stone-500">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
              Loading conversation…
            </div>
          ) : messageCount === 0 && !busy ? (
            <EmptyState onPick={onPickSuggestion} disabled={busy} />
          ) : (
            <div className="flex-1 space-y-5 pb-2">
              {messages.map((message) => (
                <MessageBubble key={message.id} message={message} />
              ))}

              {pendingLate && pendingStartedAt ? (
                <LateNotice startedAt={pendingStartedAt} />
              ) : null}

              {showLoading && pendingStartedAt && !pendingLate ? (
                <LoadingIndicator startedAt={pendingStartedAt} />
              ) : null}
            </div>
          )}

          {messageCount === 0 && busy ? (
            <div className="pt-2">
              {pendingStartedAt ? (
                <LoadingIndicator startedAt={pendingStartedAt} late={pendingLate} />
              ) : null}
            </div>
          ) : null}

          {!session && !loadingSession ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-2 py-16 text-center text-sm text-stone-500">
              <MessageSquareText className="h-6 w-6 text-stone-300" aria-hidden="true" />
              Select or create a conversation to begin.
            </div>
          ) : null}
        </div>

        {!atBottom ? (
          <button
            type="button"
            onClick={() => scrollToBottom("smooth")}
            className="sticky bottom-3 left-1/2 z-10 flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-sand-200 bg-white px-3 py-1.5 text-[0.65rem] font-medium text-stone-600 shadow-md hover:bg-sand-50"
          >
            <ArrowDown className="h-3 w-3" aria-hidden="true" />
            Latest
          </button>
        ) : null}
      </div>

      {session ? (
        <Composer
          value={draft}
          onChange={onDraftChange}
          onSend={onSend}
          busy={busy}
          disabled={loadingSession}
        />
      ) : (
        <div className="border-t border-sand-200 bg-sand-50/80 px-6 py-4">
          <div className="mx-auto flex max-w-3xl items-center gap-2 text-xs text-stone-400">
            <ShieldAlert className="h-4 w-4" aria-hidden="true" />
            Conversation unavailable. Create a new conversation to continue.
          </div>
        </div>
      )}

      {session && session.message_count > 0 && !busy ? (
        <div className="sr-only" aria-live="polite">
          <CircleAlert aria-hidden="true" />
          Conversation loaded with {session.message_count} messages.
        </div>
      ) : null}
    </section>
  );
}
