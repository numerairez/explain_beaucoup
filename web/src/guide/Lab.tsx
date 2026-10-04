import { AnimatePresence, motion } from "motion/react";
import { ArrowRight, CornerDownRight, FlaskConical, MousePointerClick, RotateCcw, TriangleAlert } from "lucide-react";
import { useMemo, useState } from "react";
import { VegaChart } from "../components/VegaChart";
import { cn, LandsMark, SectionLabel, Tip } from "../ui";
import { ContextCard } from "./ContextCard";
import type { Capability, Stage, Trace } from "./labApi";
import { Card, Code, Flag, Mono, show, STAGES, statusOf, StatusDot } from "./parts";
import type { LabState } from "./useLab";

const GROUP_ORDER = ["Explain", "Composition", "Change", "Trend", "Exception", "Distribution", "Metric"];

export function Lab({ lab }: { lab: LabState }) {
  const node = lab.node!;
  const changed = useMemo(() => {
    const apply = lab.trace?.ok ? lab.trace.stages.find((s) => s.stage === "apply") : undefined;
    const diff = (apply?.detail?.diff as { field: string }[] | undefined) ?? [];
    return new Set(diff.map((d) => d.field));
  }, [lab.trace]);

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(320px,380px)_1fr]">
      <div className="space-y-4 lg:sticky lg:top-20 lg:self-start">
        <Card>
          <div className="flex items-center justify-between px-4 pt-3">
            <SectionLabel>The node you are on</SectionLabel>
            {lab.index > 0 && (
              <button onClick={lab.reset} className="inline-flex items-center gap-1 text-[11px] text-muted hover:text-ink">
                <RotateCcw className="size-3" /> Start over
              </button>
            )}
          </div>
          <ContextCard ctx={node.context} changed={changed} />
        </Card>
        <PathCard lab={lab} />
      </div>

      <div className="min-w-0 space-y-4">
        <Moves lab={lab} />
        <TraceCard trace={lab.trace} />
        <Drawn lab={lab} />
      </div>
    </div>
  );
}

// -- the path: Contexts held as values --------------------------------------

function PathCard({ lab }: { lab: LabState }) {
  return (
    <Card className="p-4">
      <SectionLabel>Path - every node is still here</SectionLabel>
      <ol className="mt-2.5 space-y-1">
        {lab.path.map((n, i) => {
          const step = n.context.lineage[n.context.lineage.length - 1];
          return (
            <li key={`${i}-${n.context.id}`}>
              <button
                onClick={() => lab.goTo(i)}
                className={cn(
                  "flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left transition-colors",
                  i === lab.index ? "bg-accent-soft" : "hover:bg-sunken",
                )}
              >
                <span className={cn("mt-0.5 font-mono text-[10.5px]", i === lab.index ? "text-accent" : "text-muted")}>{i}</span>
                <span className="min-w-0">
                  <span className="block truncate text-[12px] text-ink">{i === 0 ? "Root - the model's landing view" : step?.summary}</span>
                  <span className="block font-mono text-[10.5px] text-muted">{n.context.id} · {n.reading.kind}</span>
                </span>
              </button>
            </li>
          );
        })}
      </ol>
      <p className="mt-3 text-[11.5px] leading-relaxed text-muted">
        Clicking an older node doesn't undo anything. Contexts are immutable values, so going back just means pointing at an
        older one. This is why the workspace can keep competing branches open side by side.
      </p>
    </Card>
  );
}

// -- moves -------------------------------------------------------------------

function Moves({ lab }: { lab: LabState }) {
  const node = lab.node!;
  const groups = useMemo(() => {
    const by = new Map<string, Capability[]>();
    for (const c of node.capabilities) {
      if (c.verb === "explain") continue;
      by.set(c.group, [...(by.get(c.group) ?? []), c]);
    }
    return GROUP_ORDER.filter((g) => by.has(g)).map((g) => [g, by.get(g)!] as const);
  }, [node]);
  const marks = node.reading.marks;

  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <SectionLabel>Moves the model offers from this node</SectionLabel>
        <span className="text-[11px] text-muted">
          from <Mono>model.capabilities(ctx)</Mono>, which asks every registered verb to <Mono>advertise</Mono>
        </span>
      </div>

      {marks.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          <span className="mr-1 inline-flex items-center gap-1 text-[11.5px] text-ink-2">
            <MousePointerClick className="size-3.5 text-muted" /> Scope to a mark:
          </span>
          <MarkChip active={lab.member === null} onClick={() => lab.setMember(null)}>none</MarkChip>
          {marks.slice(0, 10).map((m) => (
            <MarkChip key={m} active={lab.member === m} onClick={() => lab.setMember(m)}>{m}</MarkChip>
          ))}
          {marks.length > 10 && <span className="text-[11px] text-muted">… or click any mark in the chart below</span>}
        </div>
      )}

      <div className="mt-3 grid gap-x-5 gap-y-3 sm:grid-cols-2">
        {groups.map(([g, caps]) => (
          <div key={g}>
            <div className="mb-1 text-[11px] font-medium text-muted">{g}</div>
            <div className="flex flex-col gap-0.5">
              {caps.map((c, i) => {
                const scoped = c.member_scoped && lab.member !== null;
                return (
                  <Tip key={i} side="left" label={<span><span className="font-mono">{c.verb}</span>{Object.keys(c.params).length ? ` ${show(c.params)}` : ""}{c.hint ? ` - ${c.hint}` : ""}</span>}>
                    <button
                      disabled={lab.busy}
                      onClick={() => void lab.run(c.verb, c.params)}
                      className="group flex items-center justify-between gap-2 rounded-lg px-2 py-1 text-left text-[12.5px] text-ink transition-colors hover:bg-sunken disabled:opacity-50"
                    >
                      <span className="min-w-0 truncate">
                        {c.label}
                        {scoped && <span className="ml-1 text-accent">· {lab.member}</span>}
                      </span>
                      <LandsMark lands={c.lands} />
                    </button>
                  </Tip>
                );
              })}
            </div>
          </div>
        ))}
      </div>
      <Refusals lab={lab} />
    </Card>
  );
}

function MarkChip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "max-w-40 truncate rounded-md border px-1.5 py-0.5 text-[11.5px] transition-colors",
        active ? "border-accent bg-accent-soft text-accent" : "border-line text-ink-2 hover:border-line-strong",
      )}
    >
      {children}
    </button>
  );
}

/** Moves the menu would never offer - sent anyway, to watch them refused. */
function Refusals({ lab }: { lab: LabState }) {
  const reg = lab.registry!;
  const [verb, setVerb] = useState("drill_down");
  const [params, setParams] = useState('{"dimension": ""}');
  const [bad, setBad] = useState<string | null>(null);

  const presets = useMemo(() => {
    const out: { label: string; verb: string; params: Record<string, unknown> }[] = [];
    const ctx = lab.node!.context;
    const h = reg.model.hierarchies.find((x) => x.levels.length >= 2 && x.levels.includes(ctx.grain[0]));
    if (h) {
      // Any level but the next one down breaks the hierarchy: two below if
      // there is one, otherwise back above.
      const at = h.levels.indexOf(ctx.grain[0]);
      const to = h.levels[at + 2] !== undefined ? at + 2 : at - 1;
      if (to >= 0) {
        const how = to > at ? "Skip a level" : "“Drill down” upwards";
        out.push({ label: `${how}: ${h.labels[at]} → ${h.labels[to]}`, verb: "drill_down", params: { dimension: h.levels[to] } });
      }
    }
    out.push({ label: "A metric the model doesn't declare", verb: "switch_metric", params: { metric: "profit" } });
    const missing = ["day", "week", "month", "quarter", "year"].find((g) => !reg.model.grains.includes(g));
    if (missing) out.push({ label: `A calendar grain finer than the data (${missing})`, verb: "set_grain", params: { grain: missing } });
    if (ctx.grain.length === 2) out.push({ label: "Cross a matrix again", verb: "cross", params: { dimension: reg.model.dimensions[0].name } });
    return out;
  }, [lab.node, reg]);

  const send = () => {
    try {
      const p = JSON.parse(params || "{}");
      setBad(null);
      void lab.run(verb, p);
    } catch {
      setBad("Params must be JSON, e.g. {\"dimension\": \"city\"}");
    }
  };

  return (
    <div className="mt-4 border-t border-line pt-3">
      <div className="flex items-center gap-1.5 text-[11.5px] font-medium text-ink-2">
        <FlaskConical className="size-3.5 text-muted" /> Send a move the menu would never offer
      </div>
      <p className="mt-0.5 text-[11.5px] text-muted">
        The menu only lists what is legal, but the grammar still checks every move itself. Send one of these and watch which stage refuses it.
      </p>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {presets.map((p) => (
          <button
            key={p.label}
            disabled={lab.busy}
            onClick={() => {
              setVerb(p.verb);
              setParams(JSON.stringify(p.params));
              void lab.run(p.verb, p.params, { member: null });
            }}
            className="rounded-lg border border-dashed border-line-strong px-2 py-1 text-[11.5px] text-ink-2 hover:border-critical/60 hover:text-ink"
          >
            {p.label}
          </button>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <select
          value={verb}
          onChange={(e) => setVerb(e.target.value)}
          className="h-7 rounded-lg border border-line bg-raised px-1.5 font-mono text-[11.5px] text-ink outline-none"
        >
          {reg.verbs.map((v) => <option key={v.name} value={v.name}>{v.name}</option>)}
        </select>
        <input
          value={params}
          onChange={(e) => setParams(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          spellCheck={false}
          className="h-7 min-w-0 flex-1 rounded-lg border border-line bg-raised px-2 font-mono text-[11.5px] text-ink outline-none focus:border-accent"
        />
        <button onClick={send} disabled={lab.busy} className="inline-flex h-7 items-center gap-1 rounded-lg bg-ink px-2.5 text-[11.5px] font-medium text-paper disabled:opacity-50">
          Send <ArrowRight className="size-3" />
        </button>
      </div>
      {bad && <div className="mt-1 text-[11px] text-critical">{bad}</div>}
    </div>
  );
}

// -- the trace ---------------------------------------------------------------

function TraceCard({ trace }: { trace: Trace | null }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <SectionLabel>The move, stage by stage</SectionLabel>
        {trace && (
          <span className={cn("text-[11.5px] font-medium", trace.ok ? "text-accent" : "text-critical")}>
            {trace.ok ? "Landed: a new node" : "Refused - no node, no chart, no number"}
          </span>
        )}
      </div>
      <Strip stages={trace?.stages} />
      {!trace && (
        <p className="mt-3 text-[12px] text-muted">
          Pick a move above. Each stage below lights up as the move passes through it, showing what that stage was given and what it returned.
        </p>
      )}
      <AnimatePresence mode="popLayout">
        {trace && (
          <motion.ol key={JSON.stringify(trace.stages.map((s) => s.stage + s.ok)) + trace.before.id} initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mt-3 space-y-px">
            {STAGES.map((meta, i) => {
              const st = trace.stages.find((s) => s.stage === meta.id);
              const status = statusOf(trace.stages, meta.id);
              const expanded = open === meta.id || (status === "fail") || (open === null && ["apply", "backend"].includes(meta.id) && status === "ok");
              return (
                <motion.li
                  key={meta.id}
                  initial={{ opacity: 0, x: -6 }}
                  animate={{ opacity: status === "skipped" ? 0.4 : 1, x: 0 }}
                  transition={{ delay: i * 0.045 }}
                  className="relative pl-7"
                >
                  {i < STAGES.length - 1 && <span className="absolute top-5 bottom-[-4px] left-[7px] w-px bg-line" />}
                  <span className="absolute top-1.5 left-0"><StatusDot status={status} /></span>
                  <button
                    onClick={() => setOpen(open === meta.id ? "__none__" : meta.id)}
                    disabled={!st}
                    className="flex w-full flex-wrap items-baseline gap-x-2 rounded-md py-1 text-left"
                  >
                    <span className="text-[12.5px] font-semibold text-ink">{meta.name}</span>
                    <span className="font-mono text-[11px] text-muted">{meta.call}</span>
                    <span className="ml-auto font-mono text-[10.5px] text-muted/70">{meta.module}</span>
                  </button>
                  {st && expanded && <StageDetail stage={st} />}
                </motion.li>
              );
            })}
          </motion.ol>
        )}
      </AnimatePresence>
    </Card>
  );
}

/** The nine stages as a strip - the shape of every move. */
export function Strip({ stages, active, onPick }: { stages?: Stage[]; active?: string | null; onPick?: (id: string) => void }) {
  return (
    <div className="mt-3 flex items-start overflow-x-auto pb-1">
      {STAGES.map((s, i) => {
        const status = statusOf(stages, s.id);
        return (
          <div key={s.id} className="flex min-w-[64px] flex-1 items-start">
            <button
              onClick={() => onPick?.(s.id)}
              className={cn("flex w-full flex-col items-center gap-1 rounded-lg px-0.5 py-1", onPick && "hover:bg-sunken", active === s.id && "bg-accent-soft")}
            >
              <StatusDot status={status} size={18} />
              <span className={cn("text-center text-[10.5px] leading-tight", status === "fail" ? "text-critical" : active === s.id ? "text-accent" : "text-ink-2")}>{s.name}</span>
            </button>
            {i < STAGES.length - 1 && <span className="mt-[13px] h-px w-3 shrink-0 bg-line-strong" />}
          </div>
        );
      })}
    </div>
  );
}

function StageDetail({ stage }: { stage: Stage }) {
  const d = (stage.detail ?? {}) as Record<string, any>;
  const box = "mb-2 mt-0.5 rounded-lg border border-line bg-paper/60 px-3 py-2 text-[12px] text-ink-2";
  if (!stage.ok) {
    return (
      <div className={cn(box, "border-critical/30 bg-critical/5")}>
        <div className="flex items-start gap-2">
          <TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-critical" />
          <div>
            <div className="font-medium text-ink">{stage.error}</div>
            <div className="mt-0.5 text-[11.5px] text-muted">
              Raised as <Mono>SemanticError</Mono>. The session records refused moves in the journal, so they show up in the commentary.
            </div>
          </div>
        </div>
      </div>
    );
  }
  switch (stage.stage) {
    case "operation":
      return (
        <div className={box}>
          <Code className="bg-sunken/70">{`Operation(verb="${d.verb}", params=${show(d.params)}, source="${d.source}")`}</Code>
          {d.member_dropped && <div className="mt-1 text-[11.5px] text-muted">The selected mark was dropped: this verb isn't member-scoped.</div>}
        </div>
      );
    case "verb":
      return (
        <div className={box}>
          <div><Mono>{d.class}</Mono> <span className="text-muted">- {d.doc || "no docstring"}</span></div>
          <div className="mt-1.5 flex flex-wrap gap-1">
            <Flag on={d.member_scoped}>member_scoped</Flag>
            <Flag on={d.preserves_kind}>preserves_kind</Flag>
            <Flag on={d.hierarchy_scoped}>hierarchy_scoped</Flag>
            <span className="inline-flex items-center rounded-md bg-sunken px-1.5 py-px text-[10.5px] font-medium text-ink-2">placement="{d.placement}"</span>
          </div>
        </div>
      );
    case "apply": {
      const diff = d.diff as { field: string; before: unknown; after: unknown }[];
      return (
        <div className={box}>
          {diff.length === 0 ? (
            <div>No analytical field changed: <span className="text-ink">same id</span>. Only the lineage grew.</div>
          ) : (
            <table className="w-full font-mono text-[11.5px]">
              <tbody>
                {diff.map((r) => (
                  <tr key={r.field} className="align-top">
                    <td className="w-24 py-0.5 pr-2 text-muted">{r.field}</td>
                    <td className="py-0.5 pr-2 text-ink-2 line-through decoration-muted/60">{show(r.before)}</td>
                    <td className="py-0.5 pr-2 text-muted">→</td>
                    <td className="py-0.5 font-medium text-accent">{show(r.after)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {d.lineage && (
            <div className="mt-1.5 text-[11.5px] text-muted">
              lineage += <Mono>{d.lineage.verb}</Mono> "{d.lineage.summary}"
            </div>
          )}
        </div>
      );
    }
    case "validate":
      return <div className={box}>Every axis, filter, metric, grain and comparison checked against the model. Nothing refused.</div>;
    case "kind":
      return (
        <div className={box}>
          Read as <Mono>{d.kind}</Mono>
          {d.preserved && <span className="text-muted"> - kept from the node before, because the verb only re-reads the same node (preserves_kind)</span>}
        </div>
      );
    case "engine":
      return (
        <div className={box}>
          <div className="flex flex-wrap items-center gap-2">
            <span className={cn("rounded-md px-1.5 py-px text-[10.5px] font-semibold", d.cache.hit ? "bg-good/15 text-good" : "bg-sunken text-ink-2")}>
              cache {d.cache.hit ? "HIT" : "miss"}
            </span>
            <span className="font-mono text-[11px] text-muted">key {d.cache.key}</span>
            <span className="font-mono text-[11px] text-muted">{d.cache.ms} ms · {d.cache.size} results cached</span>
          </div>
          <div className="mt-1">ANALYSES[<Mono>{d.analysis.kind}</Mono>] → <span className="text-ink">"{d.analysis.title}"</span></div>
          {d.cache.hit && <div className="mt-1 text-[11.5px] text-muted">Something already computed this exact analytical state, possibly by a different route. The lineage doesn't matter.</div>}
        </div>
      );
    case "backend":
      return (
        <div className={box}>
          <div className="mb-1.5 flex flex-wrap gap-x-4 gap-y-0.5 font-mono text-[11px]">
            <span><span className="text-muted">backend</span> {d.backend}</span>
            <span><span className="text-muted">sum</span> {show(d.components)}</span>
            <span><span className="text-muted">group_by</span> {show(d.group_by)}</span>
            <span><span className="text-muted">window</span> {d.window.start}…{d.window.end} ({d.window.grain})</span>
            {d.ratio && <span><span className="text-muted">ratio</span> {d.ratio[1]} / {d.ratio[2]}, divided after summing</span>}
          </div>
          <Code className="bg-sunken/70">{d.sql}</Code>
        </div>
      );
    case "view":
      return (
        <div className={box}>
          VIEWS → renderer <Mono>"{d.renderer}"</Mono> built by <Mono>{d.builder}</Mono>. The client looks up the component in <Mono>RENDERERS</Mono>.
        </div>
      );
    case "place":
      return (
        <div className={box}>
          <span className="inline-flex items-center gap-1.5">
            {d.lands === "replace" ? "Replaces the chart on top of the stack" : <><CornerDownRight className="size-3.5" /> Opens below the chart on top, which stays put</>}
          </span>
        </div>
      );
  }
  return null;
}

// -- what was drawn ----------------------------------------------------------

function Drawn({ lab }: { lab: LabState }) {
  const node = lab.node!;
  const r = node.reading;
  const pickDefault = (key: string) => {
    const drill = node.capabilities.find((c) => c.verb === "drill_down") ?? node.capabilities.find((c) => c.verb === "decompose");
    if (drill) void lab.run(drill.verb, drill.params, { member: key });
  };
  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <SectionLabel>What the view drew</SectionLabel>
        <span className="text-[11px] text-muted">click a mark to scope the next move · double-click to drill</span>
      </div>
      <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-[12px]">
        <span><span className="text-muted">kind</span> <span className="font-medium text-ink">{r.kind}</span></span>
        <span><span className="text-muted">total</span> <span className="font-medium text-ink tabular">{r.result.value_txt}</span></span>
        <span><span className="text-muted">rows</span> <span className="text-ink tabular">{r.result.row_count}</span></span>
        <span><span className="text-muted">fact rows read</span> <span className="text-ink tabular">{r.result.fact_rows.toLocaleString()}</span></span>
        {lab.member && <span><span className="text-muted">selected</span> <span className="text-accent">{lab.member}</span></span>}
      </div>
      <div className="mt-3">
        <VegaChart
          spec={r.view.spec}
          handlers={{
            onSelect: (k) => lab.setMember(k),
            onActivate: pickDefault,
            onMenu: (k) => lab.setMember(k),
          }}
        />
      </div>
      {r.result.notes.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-[11.5px] text-muted">
          {r.result.notes.map((n) => <li key={n}>· {n}</li>)}
        </ul>
      )}
    </Card>
  );
}
