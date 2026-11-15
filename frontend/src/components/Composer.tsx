import { useEffect, useRef } from "react";
import { ArrowUp, Loader2, LockKeyhole } from "lucide-react";

const MAX_LENGTH = 4000;

export function Composer({
  value,
  onChange,
  onSend,
  busy,
  disabled,
}: {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  busy: boolean;
  disabled?: boolean;
}) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const element = textareaRef.current;
    if (!element) return;
    element.style.height = "0px";
    element.style.height = `${Math.min(element.scrollHeight, 180)}px`;
  }, [value]);

  const trimmed = value.trim();
  const canSend = Boolean(trimmed) && !busy && !disabled;

  function submit() {
    if (!canSend) return;
    onSend();
  }

  return (
    <div className="border-t border-sand-200 bg-sand-50/80 px-3 pb-3 pt-3 backdrop-blur sm:px-6 sm:pb-4">
      <form
        className="mx-auto max-w-3xl"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <div
          className={`rounded-2xl border bg-white p-2 shadow-sm transition-shadow focus-within:shadow-md ${
            disabled
              ? "border-sand-200 opacity-70"
              : "border-sand-200 focus-within:border-kbn-300"
          }`}
        >
          <label htmlFor="helpdesk-input" className="sr-only">
            Ask the KBN University Helpdesk
          </label>
          <textarea
            id="helpdesk-input"
            ref={textareaRef}
            rows={1}
            value={value}
            maxLength={MAX_LENGTH}
            disabled={disabled}
            onChange={(event) => onChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                if (event.nativeEvent.isComposing) return;
                event.preventDefault();
                submit();
              }
            }}
            placeholder={
              busy
                ? "Waiting for the current answer to finish…"
                : "Ask about KBN University admissions, courses, people or contacts…"
            }
            className="max-h-[180px] w-full resize-none bg-transparent px-2 py-1.5 text-[0.94rem] leading-relaxed text-kbn-950 outline-none placeholder:text-stone-400 disabled:cursor-not-allowed"
          />

          <div className="flex items-end justify-between gap-3 px-1 pt-1">
            <div className="min-w-0">
              <p className="flex items-center gap-1.5 text-[0.65rem] text-stone-400">
                <LockKeyhole className="h-3 w-3 shrink-0" aria-hidden="true" />
                <span className="truncate">
                  AI assistant · answers grounded in KBN records
                </span>
              </p>
              <p className="mt-0.5 hidden text-[0.62rem] text-stone-400 sm:block">
                Enter to send · Shift + Enter for a new line
                {value.length > 3400 ? (
                  <span className="ml-2 font-mono text-amber-600">
                    {value.length}/{MAX_LENGTH}
                  </span>
                ) : null}
              </p>
            </div>

            <button
              type="submit"
              disabled={!canSend}
              className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-kbn-800 text-white shadow-sm transition-all hover:bg-kbn-700 hover:shadow-md disabled:bg-stone-300 disabled:shadow-none"
              title={busy ? "Waiting for the current answer" : "Send message"}
              aria-label="Send message"
            >
              {busy ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <ArrowUp className="h-4 w-4" aria-hidden="true" />
              )}
            </button>
          </div>
        </div>

        <p className="mt-2 text-center text-[0.62rem] leading-relaxed text-stone-400">
          The helpdesk never invents KBN University facts. Unverified information
          is reported as unavailable. Please don&apos;t share personal details.
        </p>
      </form>
    </div>
  );
}
