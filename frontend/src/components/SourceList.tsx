import { ChevronDown, ExternalLink, FileText, Layers3 } from "lucide-react";
import type { SourceReference } from "../api/types";
import { formatScore } from "../lib/format";

export function SourceList({ sources }: { sources: SourceReference[] }) {
  if (!sources.length) return null;

  return (
    <details className="group mt-3 rounded-xl border border-sand-200 bg-sand-50/70">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3.5 py-2.5 text-xs font-semibold text-kbn-800 transition-colors hover:bg-kbn-50/70 [&::-webkit-details-marker]:hidden">
        <Layers3 className="h-4 w-4 text-kbn-600" aria-hidden="true" />
        <span>
          {sources.length} {sources.length === 1 ? "source" : "sources"} from KBN records
        </span>
        <ChevronDown
          className="ml-auto h-4 w-4 text-stone-400 transition-transform group-open:rotate-180"
          aria-hidden="true"
        />
      </summary>

      <div className="space-y-2 border-t border-sand-200 px-3 py-3">
        {sources.map((source, index) => {
          const score = formatScore(source.score);
          return (
            <article
              key={`${source.id}-${index}`}
              className="rounded-lg border border-sand-200 bg-white p-3"
            >
              <div className="flex items-start gap-2.5">
                <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-md bg-kbn-50 text-kbn-700">
                  <FileText className="h-3.5 w-3.5" aria-hidden="true" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <h4 className="truncate text-xs font-semibold text-kbn-950">
                      {source.title}
                    </h4>
                    {score ? (
                      <span className="rounded-full bg-kbn-50 px-1.5 py-0.5 text-[0.62rem] font-medium text-kbn-700">
                        {score}
                      </span>
                    ) : null}
                  </div>
                  <p className="mt-0.5 truncate font-mono text-[0.62rem] text-stone-500">
                    {source.file}
                    {typeof source.chunk_index === "number"
                      ? ` · chunk ${source.chunk_index + 1}`
                      : ""}
                  </p>
                </div>
                {source.url ? (
                  <a
                    href={source.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex shrink-0 items-center gap-1 rounded-md px-1.5 py-1 text-[0.65rem] font-medium text-kbn-700 hover:bg-kbn-50"
                    title="Open the official page"
                  >
                    Source
                    <ExternalLink className="h-3 w-3" aria-hidden="true" />
                  </a>
                ) : null}
              </div>
              {source.snippet ? (
                <p className="mt-2 line-clamp-3 border-l-2 border-kbn-100 pl-2.5 text-[0.72rem] leading-relaxed text-stone-600">
                  {source.snippet}
                </p>
              ) : null}
            </article>
          );
        })}
      </div>
    </details>
  );
}
