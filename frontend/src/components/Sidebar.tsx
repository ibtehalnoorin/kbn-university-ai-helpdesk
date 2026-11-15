import { useState } from "react";
import {
  BookOpenText,
  Check,
  Loader2,
  MessageSquare,
  MessageSquarePlus,
  Pencil,
  Trash2,
  X,
} from "lucide-react";
import type { HealthResponse, SessionSummary } from "../api/types";
import { relativeTime } from "../lib/format";
import { HealthPill } from "./HealthPill";

export function Sidebar({
  sessions,
  activeId,
  health,
  healthLoading,
  creating,
  open,
  onClose,
  onSelect,
  onCreate,
  onRename,
  onRequestDelete,
}: {
  sessions: SessionSummary[];
  activeId: string | null;
  health: HealthResponse | null;
  healthLoading: boolean;
  creating: boolean;
  open: boolean;
  onClose: () => void;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onRename: (id: string, title: string) => Promise<void>;
  onRequestDelete: (session: SessionSummary) => void;
}) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [renameError, setRenameError] = useState<string | null>(null);

  function startRename(session: SessionSummary) {
    setEditingId(session.id);
    setDraft(session.title === "New conversation" ? "" : session.title);
    setRenameError(null);
  }

  function cancelRename() {
    setEditingId(null);
    setDraft("");
    setRenameError(null);
  }

  async function submitRename(id: string) {
    const title = draft.trim();
    if (!title) {
      setRenameError("Enter a title.");
      return;
    }
    setSaving(true);
    setRenameError(null);
    try {
      await onRename(id, title);
      setEditingId(null);
      setDraft("");
    } catch (error) {
      setRenameError(error instanceof Error ? error.message : "Could not rename.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <div
        className={`fixed inset-0 z-30 bg-kbn-950/45 backdrop-blur-[2px] transition-opacity lg:hidden ${
          open ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
        onClick={onClose}
        aria-hidden="true"
      />

      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-[min(88vw,20rem)] flex-col border-r border-kbn-900/10 bg-white/95 shadow-2xl shadow-kbn-950/10 backdrop-blur transition-transform duration-300 ease-out lg:static lg:z-auto lg:w-[19rem] lg:translate-x-0 lg:shadow-none ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
        aria-label="Conversations"
      >
        <div className="flex items-start gap-3 border-b border-sand-200 px-4 pb-4 pt-4">
          <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl border border-gold-300/70 bg-gradient-to-br from-kbn-800 to-kbn-950 text-gold-300 shadow-sm">
            <BookOpenText className="h-5 w-5" aria-hidden="true" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-[0.6rem] font-bold uppercase tracking-[0.18em] text-gold-600">
              Khaja Bandanawaz
            </p>
            <h1 className="font-display text-lg font-semibold leading-tight text-kbn-950">
              University Helpdesk
            </h1>
            <p className="mt-0.5 text-[0.65rem] text-stone-500">AI research assistant</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-stone-400 hover:bg-sand-100 hover:text-stone-600 lg:hidden"
            aria-label="Close sidebar"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        <div className="px-3 pt-3">
          <button
            type="button"
            onClick={onCreate}
            disabled={creating}
            className="flex w-full items-center justify-center gap-2 rounded-xl bg-kbn-800 px-3 py-2.5 text-sm font-semibold text-white shadow-sm shadow-kbn-900/20 transition-all hover:bg-kbn-700 hover:shadow-md disabled:opacity-60"
          >
            {creating ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <MessageSquarePlus className="h-4 w-4" aria-hidden="true" />
            )}
            {creating ? "Creating…" : "New conversation"}
          </button>
        </div>

        <div className="mt-5 flex items-center justify-between px-4">
          <p className="text-[0.62rem] font-bold uppercase tracking-[0.16em] text-stone-400">
            Conversations
          </p>
          <span className="rounded-full bg-sand-100 px-1.5 py-0.5 text-[0.6rem] font-semibold text-stone-500">
            {sessions.length}
          </span>
        </div>

        <nav className="scroll-slim mt-2 flex-1 overflow-y-auto px-2 pb-3">
          {sessions.length === 0 ? (
            <div className="mx-2 mt-3 rounded-xl border border-dashed border-sand-200 p-4 text-center">
              <MessageSquare className="mx-auto h-5 w-5 text-stone-300" aria-hidden="true" />
              <p className="mt-2 text-xs text-stone-500">
                No conversations yet. Start a new one to ask about KBN University.
              </p>
            </div>
          ) : (
            <ul className="space-y-1">
              {sessions.map((session) => {
                const active = session.id === activeId;
                const editing = editingId === session.id;
                return (
                  <li key={session.id}>
                    <div
                      className={`group relative rounded-xl transition-colors ${
                        active ? "bg-kbn-50" : "hover:bg-sand-100/80"
                      }`}
                    >
                      {active ? (
                        <span className="absolute left-0 top-2.5 h-[calc(100%-1.25rem)] w-0.5 rounded-full bg-gold-400" />
                      ) : null}

                      {editing ? (
                        <form
                          className="px-2.5 py-2"
                          onSubmit={(event) => {
                            event.preventDefault();
                            void submitRename(session.id);
                          }}
                        >
                          <input
                            autoFocus
                            value={draft}
                            maxLength={80}
                            onChange={(event) => setDraft(event.target.value)}
                            onKeyDown={(event) => {
                              if (event.key === "Escape") cancelRename();
                            }}
                            placeholder="Conversation title"
                            className="w-full rounded-lg border border-kbn-300 bg-white px-2 py-1.5 text-xs text-kbn-950 outline-none focus:ring-2 focus:ring-kbn-200"
                            aria-label="Conversation title"
                          />
                          <div className="mt-1.5 flex items-center gap-1.5">
                            <button
                              type="submit"
                              disabled={saving}
                              className="inline-flex items-center gap-1 rounded-md bg-kbn-800 px-2 py-1 text-[0.65rem] font-semibold text-white disabled:opacity-60"
                            >
                              {saving ? (
                                <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
                              ) : (
                                <Check className="h-3 w-3" aria-hidden="true" />
                              )}
                              Save
                            </button>
                            <button
                              type="button"
                              onClick={cancelRename}
                              className="rounded-md px-2 py-1 text-[0.65rem] font-medium text-stone-500 hover:bg-sand-100"
                            >
                              Cancel
                            </button>
                          </div>
                          {renameError ? (
                            <p className="mt-1 text-[0.62rem] text-rose-600">{renameError}</p>
                          ) : null}
                        </form>
                      ) : (
                        <button
                          type="button"
                          onClick={() => onSelect(session.id)}
                          className="w-full rounded-xl px-2.5 py-2.5 pr-[4.4rem] text-left"
                          aria-current={active ? "page" : undefined}
                        >
                          <span
                            className={`line-clamp-2 text-xs font-semibold leading-snug ${
                              active ? "text-kbn-900" : "text-stone-700"
                            }`}
                          >
                            {session.title}
                          </span>
                          <span className="mt-1 flex items-center gap-1.5 text-[0.62rem] text-stone-400">
                            <span>{relativeTime(session.updated_at)}</span>
                            <span aria-hidden="true">·</span>
                            <span>
                              {session.message_count}{" "}
                              {session.message_count === 1 ? "message" : "messages"}
                            </span>
                          </span>
                        </button>
                      )}

                      {!editing ? (
                        <div className="absolute right-1.5 top-1/2 flex -translate-y-1/2 items-center gap-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100 max-lg:opacity-100">
                          <button
                            type="button"
                            onClick={() => startRename(session)}
                            className="rounded-md p-1.5 text-stone-400 hover:bg-white hover:text-kbn-700"
                            title="Rename conversation"
                            aria-label={`Rename ${session.title}`}
                          >
                            <Pencil className="h-3 w-3" aria-hidden="true" />
                          </button>
                          <button
                            type="button"
                            onClick={() => onRequestDelete(session)}
                            className="rounded-md p-1.5 text-stone-400 hover:bg-rose-50 hover:text-rose-600"
                            title="Delete conversation"
                            aria-label={`Delete ${session.title}`}
                          >
                            <Trash2 className="h-3 w-3" aria-hidden="true" />
                          </button>
                        </div>
                      ) : null}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </nav>

        <div className="border-t border-sand-200 bg-sand-50/60 px-4 py-3">
          <HealthPill health={health} loading={healthLoading} />
          <p className="mt-2 text-[0.6rem] leading-relaxed text-stone-400">
            Answers are written by an AI model using only KBN University&apos;s
            published records, then checked against them. Questions may be
            processed by a cloud AI service, so please don&apos;t share personal
            details.
          </p>
        </div>
      </aside>
    </>
  );
}
