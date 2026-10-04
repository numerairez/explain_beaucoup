import { Check, Circle, X } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../ui";
import type { Stage, StageId } from "./labApi";

/** The nine stages one gesture passes through, in order. The same list
 *  drives the overview diagram and the live trace, so the two never drift. */
export const STAGES: {
  id: StageId;
  name: string;
  call: string;
  module: string;
  says: string;
}[] = [
  { id: "operation", name: "Operation", call: "Operation.of(verb, source, **params)", module: "core/operations.py",
    says: "A click, a menu item, a toolbar change or an agent's tool call all become the same typed value. The UI never acts on a chart." },
  { id: "verb", name: "Verb", call: "VERBS[name]", module: "core/operations.py", says:
    "The registry entry owns everything about the verb: how it changes a Context, what it advertises, and how the session treats its result." },
  { id: "apply", name: "apply", call: "verb.apply(model, ctx, params, op)", module: "core/verbs.py", says:
    "Returns a new Context. Nothing is mutated; the lineage gains one step. A verb can refuse here, by name." },
  { id: "validate", name: "validate", call: "model.validate(ctx)", module: "semantic/specs.py", says:
    "The semantic model blocks meaningless nodes - unknown grains, overlapping comparisons, invalid metrics - before any number exists." },
  { id: "kind", name: "Result kind", call: "result_kind(model, ctx, op)", module: "core/operations.py", says:
    "Two axes read as a matrix, a time axis as a timeseries; otherwise the verb says how the node should be read." },
  { id: "engine", name: "Engine", call: "engine.execute(ctx, kind)", module: "engine/engine.py", says:
    "Cached on fingerprint + kind. Looks up the Analysis registered for the kind and runs it." },
  { id: "backend", name: "Backend", call: "backend.aggregate(Query)", module: "engine/backend.py", says:
    "The only primitive: sum these components, in this scope, grouped by these keys. Ratios are divided afterwards, never summed." },
  { id: "view", name: "View", call: "VIEWS[kind].build(result)", module: "view/vega.py", says:
    "A renderer name plus a spec. Each front end picks its component by renderer; marks report back by key." },
  { id: "place", name: "Placement", call: "verb.placement", module: "core/session.py", says:
    "Re-framing verbs replace the chart on top; narrowing verbs open below it. The session reads this off the verb." },
];

export type StageStatus = "idle" | "ok" | "fail" | "skipped";

export function statusOf(stages: Stage[] | undefined, id: StageId): StageStatus {
  if (!stages) return "idle";
  const s = stages.find((x) => x.stage === id);
  if (!s) return stages.some((x) => !x.ok) ? "skipped" : "idle";
  return s.ok ? "ok" : "fail";
}

export function StatusDot({ status, size = 16 }: { status: StageStatus; size?: number }) {
  const cls = "shrink-0 rounded-full grid place-items-center";
  const style = { width: size, height: size };
  if (status === "ok")
    return <span className={cn(cls, "bg-accent text-accent-ink")} style={style}><Check className="size-[70%]" strokeWidth={3} /></span>;
  if (status === "fail")
    return <span className={cn(cls, "bg-critical text-white")} style={style}><X className="size-[70%]" strokeWidth={3} /></span>;
  return <span className={cn(cls, "border border-line-strong text-muted")} style={style}><Circle className="size-[40%]" /></span>;
}

export function Code({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <pre
      className={cn(
        "overflow-x-auto rounded-lg border border-line bg-sunken px-3 py-2 font-mono text-[11.5px] leading-relaxed whitespace-pre-wrap break-words text-ink-2",
        className,
      )}
    >
      {children}
    </pre>
  );
}

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <code className={cn("rounded bg-sunken px-1 py-px font-mono text-[11.5px] text-ink", className)}>{children}</code>;
}

export function Flag({ on, children }: { on: boolean; children: ReactNode }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md px-1.5 py-px text-[10.5px] font-medium",
        on ? "bg-accent-soft text-accent" : "bg-sunken text-muted line-through decoration-muted/50",
      )}
    >
      {children}
    </span>
  );
}

export function Section({ id, n, title, lede, children }: {
  id: string;
  n: number;
  title: string;
  lede: ReactNode;
  children: ReactNode;
}) {
  return (
    <section id={id} className="scroll-mt-20 border-t border-line py-14 first:border-t-0">
      <div className="mb-6 max-w-[68ch]">
        <div className="font-mono text-[11px] font-medium text-accent">{String(n).padStart(2, "0")}</div>
        <h2 className="mt-1 text-[22px] leading-tight font-semibold tracking-[-0.02em] text-ink">{title}</h2>
        <div className="mt-2 text-[13.5px] leading-relaxed text-ink-2">{lede}</div>
      </div>
      {children}
    </section>
  );
}

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("rounded-2xl border border-line bg-surface shadow-sm", className)}>{children}</div>;
}

/** Render a JSON-ish value compactly - the way a Context field reads. */
export function show(v: unknown): string {
  if (v === null || v === undefined) return "None";
  if (Array.isArray(v)) return v.length ? `(${v.map((x) => JSON.stringify(x)).join(", ")}${v.length === 1 ? "," : ""})` : "()";
  if (typeof v === "object") {
    const e = Object.entries(v as Record<string, unknown>);
    return e.length ? `{${e.map(([k, x]) => `${k}: ${JSON.stringify(x)}`).join(", ")}}` : "{}";
  }
  return JSON.stringify(v);
}
