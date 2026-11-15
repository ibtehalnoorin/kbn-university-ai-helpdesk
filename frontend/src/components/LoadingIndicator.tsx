import { useEffect, useState } from "react";
import { Loader2, Sparkles } from "lucide-react";
import { formatClock } from "../lib/format";

export function LoadingIndicator({
  startedAt,
  late = false,
}: {
  startedAt: number;
  late?: boolean;
}) {
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
    <div className="animate-fade-up flex gap-3">
      <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full bg-gradient-to-br from-kbn-700 to-kbn-900 text-gold-300 ring-1 ring-kbn-900/20">
        <Sparkles className="h-4 w-4 animate-pulse-soft" aria-hidden="true" />
      </span>
      <div className="min-w-0 flex-1 sm:max-w-[85%]">
        <div className="rounded-2xl rounded-tl-md border border-sand-200 bg-white px-4 py-4 shadow-sm shadow-stone-200/60">
          <div className="flex items-center gap-2 text-sm font-semibold text-kbn-900">
            <Loader2 className="h-4 w-4 animate-spin text-kbn-600" aria-hidden="true" />
            {late ? "Finishing this answer in the background" : "Looking through KBN records"}
            <span className="ml-auto rounded-full bg-kbn-50 px-2 py-0.5 font-mono text-[0.65rem] font-medium text-kbn-700">
              {formatClock(elapsed)}
            </span>
          </div>

          <div className="mt-3 space-y-2" aria-hidden="true">
            <div className="h-2.5 w-[88%] animate-shimmer rounded-full bg-[linear-gradient(90deg,var(--color-sand-100),var(--color-kbn-50),var(--color-sand-100))] bg-[length:200%_100%]" />
            <div className="h-2.5 w-[72%] animate-shimmer rounded-full bg-[linear-gradient(90deg,var(--color-sand-100),var(--color-kbn-50),var(--color-sand-100))] bg-[length:200%_100%] [animation-delay:120ms]" />
            <div className="h-2.5 w-[54%] animate-shimmer rounded-full bg-[linear-gradient(90deg,var(--color-sand-100),var(--color-kbn-50),var(--color-sand-100))] bg-[length:200%_100%] [animation-delay:240ms]" />
          </div>

          <p className="mt-3 text-xs leading-relaxed text-stone-500">
            The private Qwen3:8B model is preparing a grounded answer. On CPU
            this can take several minutes — the answer is saved when it finishes.
          </p>
        </div>
      </div>
    </div>
  );
}
