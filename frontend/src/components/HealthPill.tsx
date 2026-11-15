import { CircleHelp, Loader2, ShieldCheck, WifiOff } from "lucide-react";
import type { HealthResponse } from "../api/types";

export function HealthPill({
  health,
  loading,
}: {
  health: HealthResponse | null;
  loading: boolean;
}) {
  if (loading && !health) {
    return (
      <div className="flex items-center gap-2 text-[0.68rem] text-stone-500">
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
        Checking services…
      </div>
    );
  }

  if (!health) {
    return (
      <div className="flex items-center gap-2 text-[0.68rem] font-medium text-rose-600">
        <WifiOff className="h-3.5 w-3.5" aria-hidden="true" />
        Backend not reachable
      </div>
    );
  }

  const chunks = health.components.chromadb.indexed_chunks;
  const expected = health.components.chromadb.expected_chunks;
  const llm = health.components.llm ?? health.components.ollama;
  const llmName = health.components.llm ? "AI model" : "Ollama";
  const detail = [
    `${llmName}: ${llm?.status ?? "unknown"}`,
    `ChromaDB: ${health.components.chromadb.status}${chunks ? ` (${chunks} chunks)` : ""}`,
    `Worker: ${health.worker.alive ? "running" : "idle"}`,
    `Protected files: ${health.protected_files.ok ? "intact" : "CHANGED"}`,
  ].join(" · ");

  const config = {
    ok: {
      dot: "bg-kbn-500",
      text: "text-kbn-800",
      label: "All systems ready",
      Icon: ShieldCheck,
    },
    degraded: {
      dot: "bg-amber-500",
      text: "text-amber-700",
      label: "Limited service",
      Icon: CircleHelp,
    },
    error: {
      dot: "bg-rose-500",
      text: "text-rose-700",
      label: "Service offline",
      Icon: WifiOff,
    },
  }[health.status];

  return (
    <div className={`flex flex-col gap-1 text-[0.68rem] ${config.text}`} title={detail}>
      <span className="flex items-center gap-2 font-semibold">
        <span className={`h-2 w-2 rounded-full ${config.dot} shadow-[0_0_0_3px_rgba(51,67,107,0.14)]`} />
        <config.Icon className="h-3.5 w-3.5" aria-hidden="true" />
        {config.label}
      </span>
      {typeof chunks === "number" ? (
        <span className="pl-4 text-[0.62rem] font-normal text-stone-500">
          {typeof expected === "number" && chunks !== expected
            ? `${chunks}/${expected} records indexed`
            : `${chunks} indexed KBN records · verified`}
        </span>
      ) : null}
    </div>
  );
}
