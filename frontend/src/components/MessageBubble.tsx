import { useState } from "react";
import {
  Headset,
  BookOpenCheck,
  Check,
  Clock3,
  Copy,
  UserRound,
} from "lucide-react";
import type { Message } from "../api/types";
import { formatDuration, formatTime } from "../lib/format";
import { useEnquiry } from "../lib/enquiry";
import { Markdown } from "./Markdown";
import { SourceList } from "./SourceList";
import { VerificationBadge, VerificationNote } from "./VerificationBadge";

export function MessageBubble({ message }: { message: Message }) {
  const { openEnquiry } = useEnquiry();
  const [copied, setCopied] = useState(false);
  const isUser = message.role === "user";

  async function copy() {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      // Clipboard unavailable; silently ignore.
    }
  }

  if (isUser) {
    return (
      <div className="animate-fade-up flex justify-end gap-3">
        <div className="max-w-[85%] sm:max-w-[75%]">
          <div className="rounded-2xl rounded-br-md bg-kbn-800 px-4 py-3 text-[0.95rem] leading-relaxed text-white shadow-sm shadow-kbn-950/10">
            <p className="whitespace-pre-wrap break-words">{message.content}</p>
          </div>
          <div className="mt-1.5 flex items-center justify-end gap-1.5 pr-1 text-[0.65rem] text-stone-400">
            <Clock3 className="h-3 w-3" aria-hidden="true" />
            {formatTime(message.created_at)}
          </div>
        </div>
        <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full border border-sand-200 bg-white text-kbn-700 shadow-sm">
          <UserRound className="h-4 w-4" aria-hidden="true" />
          <span className="sr-only">You</span>
        </span>
      </div>
    );
  }

  const responseTime = formatDuration(message.response_time_ms);
  const llmTime =
    message.llm_time_seconds !== null && message.llm_time_seconds !== undefined
      ? formatDuration(message.llm_time_seconds * 1000)
      : null;

  return (
    <div className="animate-fade-up flex gap-3">
      <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full bg-gradient-to-br from-kbn-700 to-kbn-900 text-gold-300 shadow-sm shadow-kbn-950/20 ring-1 ring-kbn-900/20">
        <BookOpenCheck className="h-4 w-4" aria-hidden="true" />
        <span className="sr-only">KBN University Helpdesk</span>
      </span>

      <div className="min-w-0 max-w-[calc(100%-2.75rem)] flex-1 sm:max-w-[85%]">
        <div className="mb-1.5 flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="text-xs font-semibold text-kbn-950">
            KBN University Helpdesk
          </span>
          {message.persona ? (
            <span className="hidden text-[0.65rem] text-stone-400 sm:inline">
              {message.persona}
            </span>
          ) : null}
          {message.verification ? (
            <span className="ml-auto">
              <VerificationBadge verification={message.verification} compact />
            </span>
          ) : null}
        </div>

        <div
          className={`rounded-2xl rounded-tl-md border bg-white px-4 py-3.5 shadow-sm shadow-stone-200/60 ${
            message.verification?.status === "unavailable"
              ? "border-rose-100"
              : "border-sand-200"
          }`}
        >
          <Markdown content={message.content} />
          {message.error ? (
            <p className="mt-2 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">
              {message.error}
            </p>
          ) : null}
          {message.verification ? (
            <VerificationNote verification={message.verification} />
          ) : null}
          {message.verification?.status === "unavailable" ? (
            <button
              type="button"
              onClick={openEnquiry}
              className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-gold-300 bg-gold-200/30 px-3 py-1.5 text-xs font-semibold text-gold-600 transition hover:bg-gold-200/60"
            >
              <Headset className="h-3.5 w-3.5" aria-hidden="true" />
              Not what you needed? Talk to the admissions office
            </button>
          ) : null}
          <SourceList sources={message.sources} />
        </div>

        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 pl-1 text-[0.65rem] text-stone-400">
          <span className="inline-flex items-center gap-1">
            <Clock3 className="h-3 w-3" aria-hidden="true" />
            {formatTime(message.created_at)}
          </span>
          {responseTime ? <span>Answered in {responseTime}</span> : null}
          {llmTime ? (
            <span title="Time spent by the language model">
              Model {llmTime}
            </span>
          ) : null}
          <button
            type="button"
            onClick={copy}
            className="ml-auto inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[0.65rem] font-medium text-stone-500 transition-colors hover:bg-white hover:text-kbn-700"
            title="Copy answer"
          >
            {copied ? (
              <Check className="h-3 w-3 text-kbn-600" aria-hidden="true" />
            ) : (
              <Copy className="h-3 w-3" aria-hidden="true" />
            )}
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
      </div>
    </div>
  );
}
