import { Fingerprint } from "lucide-react";
import { useState } from "react";
import { cn, Tip } from "../ui";
import type { Ctx } from "./labApi";
import { show } from "./parts";

/** What each field of a Context means. Order is the dataclass's. */
export const FIELDS: { key: keyof Ctx; name: string; means: string }[] = [
  { key: "dataset", name: "dataset", means: "Which semantic model the node belongs to. A Context for another model is refused." },
  { key: "metric", name: "metric", means: "One metric name the model declares - never an expression. Ratios are recomputed from their parts at every grain." },
  { key: "filters", name: "filters", means: "The scope: dimension → member. Stored sorted, so two routes to the same scope are the same value." },
  { key: "grain", name: "grain", means: "The axes the value is broken down by. () is the scope total, one name a breakdown or trend, two names a matrix." },
  { key: "time", name: "time", means: "An inclusive window of period keys at a calendar grain. Keys sort as text at every grain, so a window is a string range." },
  { key: "comparison", name: "comparison", means: "A reference period the model declares (previous period, same period last year…), or None." },
];

export function ContextCard({ ctx, changed, annotate, focus, onFocus }: {
  ctx: Ctx;
  changed?: Set<string>;
  annotate?: boolean;
  focus?: string | null;
  onFocus?: (k: string | null) => void;
}) {
  const [showId, setShowId] = useState(false);
  return (
    <div className="font-mono text-[12px]">
      <div className="flex items-center justify-between gap-2 border-b border-line px-4 py-2.5">
        <span className="text-ink-2">
          <span className="text-link">Context</span>(
        </span>
        <Tip label="The fingerprint: a hash of every field below except lineage. Click to see exactly what is hashed.">
          <button
            onClick={() => setShowId((v) => !v)}
            className="inline-flex items-center gap-1 rounded-md bg-accent-soft px-1.5 py-0.5 text-[11px] text-accent hover:brightness-110"
          >
            <Fingerprint className="size-3" />
            {ctx.id}
          </button>
        </Tip>
      </div>
      {showId && (
        <div className="border-b border-line bg-sunken/60 px-4 py-2 text-[10.5px] leading-relaxed text-muted">
          <div className="mb-1 font-sans text-[11px] text-ink-2">
            <span className="font-semibold">id = "ctx_" + sha1(</span>this<span className="font-semibold">)[:16]</span> - lineage is left out on purpose.
          </div>
          <div className="break-all">{ctx.identity}</div>
        </div>
      )}
      <div className="py-1.5">
        {FIELDS.map((f) => {
          const isChanged = changed?.has(f.key);
          const active = focus === f.key;
          return (
            <div
              key={f.key}
              onMouseEnter={() => onFocus?.(f.key)}
              onMouseLeave={() => onFocus?.(null)}
              className={cn(
                "relative px-4 py-1 transition-colors",
                isChanged && "bg-accent-soft",
                active && "bg-hilite",
              )}
            >
              {isChanged && <span className="absolute inset-y-1 left-0 w-0.5 rounded-full bg-accent" />}
              <div className="flex gap-2">
                <span className="w-[86px] shrink-0 text-muted">{f.name}=</span>
                <span className={cn("min-w-0 break-words", isChanged ? "font-medium text-accent" : "text-ink")}>
                  {fieldText(ctx, f.key)}
                </span>
              </div>
              {annotate && <div className="mt-0.5 mb-1 pl-[94px] font-sans text-[11.5px] leading-snug text-muted">{f.means}</div>}
            </div>
          );
        })}
        <div
          onMouseEnter={() => onFocus?.("lineage")}
          onMouseLeave={() => onFocus?.(null)}
          className={cn("px-4 py-1", focus === "lineage" && "bg-hilite")}
        >
          <div className="flex gap-2">
            <span className="w-[86px] shrink-0 text-muted">lineage=</span>
            <span className="text-muted">{ctx.lineage.length ? `${ctx.lineage.length} step${ctx.lineage.length > 1 ? "s" : ""}` : "()"}</span>
          </div>
          {ctx.lineage.length > 0 && (
            <ol className="mt-1 ml-[94px] space-y-0.5 border-l border-line pl-2.5 font-sans text-[11.5px]">
              {ctx.lineage.map((s, i) => (
                <li key={i} className={cn("text-ink-2", i === ctx.lineage.length - 1 && changed?.size && "text-accent")}>
                  <span className="font-mono text-[10.5px] text-muted">{s.verb}</span> {s.summary}
                </li>
              ))}
            </ol>
          )}
          {annotate && (
            <div className="mt-0.5 mb-1 pl-[94px] font-sans text-[11.5px] leading-snug text-muted">
              How the node was reached - an audit trail. Excluded from identity, so two routes to the same question share an id and a cache entry.
            </div>
          )}
        </div>
      </div>
      <div className="border-t border-line px-4 py-2 text-ink-2">)</div>
    </div>
  );
}

function fieldText(ctx: Ctx, key: keyof Ctx): string {
  if (key === "time") {
    const t = ctx.time;
    return t.start === t.end ? `TimeWindow("${t.start}", grain="${t.grain}")` : `TimeWindow("${t.start}" … "${t.end}", grain="${t.grain}")`;
  }
  return show(ctx[key]);
}
