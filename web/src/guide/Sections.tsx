import { ArrowDown, ArrowRight, Check, Equal, Play, Sparkles, X } from "lucide-react";
import { useMemo, useState } from "react";
import { cn, Segmented, SectionLabel } from "../ui";
import type { Ctx, Explanation, Trace } from "./labApi";
import { lab as labApi } from "./labApi";
import { Card, Code, Flag, Mono, show } from "./parts";
import type { LabState } from "./useLab";

// -- identity ------------------------------------------------------------------

type Route = { title: string; steps: { label: string; verb: string; params: Record<string, unknown>; member: string | null }[] };

/** Two different routes to one question, built from whatever the model offers
 *  at its landing view. */
function routesFor(lab: LabState): Route[] | null {
  const root = lab.root;
  if (!root || !root.context.grain.length) return null;
  const g = root.context.grain[0];
  const m = root.reading.marks[0];
  const move = root.capabilities.find((c) => c.verb === "drill_down") ?? root.capabilities.find((c) => c.verb === "decompose");
  if (!m || !move) return null;
  const dim = move.params.dimension;
  return [
    { title: "Route A - one move", steps: [{ label: `${move.verb}(${dim}) on ${m}`, verb: move.verb, params: move.params, member: m }] },
    {
      title: "Route B - two moves",
      steps: [
        { label: `focus(${g} = ${m})`, verb: "focus", params: { dimension: g, member: m }, member: null },
        { label: `${move.verb}(${dim})`, verb: move.verb, params: move.params, member: null },
      ],
    },
  ];
}

export function Identity({ lab }: { lab: LabState }) {
  const routes = useMemo(() => routesFor(lab), [lab.root]);
  const [results, setResults] = useState<{ ends: Ctx[]; traces: Trace[][] } | null>(null);
  const [busy, setBusy] = useState(false);
  if (!routes || !lab.root) return <p className="text-[12.5px] text-muted">This model's landing view has nothing to drill, so there's no pair of routes to compare.</p>;

  const go = async () => {
    setBusy(true);
    try {
      const traces: Trace[][] = [];
      const ends: Ctx[] = [];
      for (const r of routes) {
        let ctx = lab.root!.context;
        let kind = lab.root!.reading.kind;
        const ts: Trace[] = [];
        for (const s of r.steps) {
          const t = await lab.runFrom(ctx, kind, s.verb, s.params, s.member);
          ts.push(t);
          if (!t.ok || !t.after) break;
          ctx = t.after;
          kind = t.reading!.kind;
        }
        traces.push(ts);
        ends.push(ctx);
      }
      setResults({ ends, traces });
    } finally {
      setBusy(false);
    }
  };

  const same = results && results.ends[0].id === results.ends[1].id;
  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-2">
        {routes.map((r, i) => {
          const end = results?.ends[i];
          const last = results?.traces[i][results.traces[i].length - 1];
          const engine = last?.stages.find((s) => s.stage === "engine")?.detail as { cache: { hit: boolean } } | undefined;
          return (
            <Card key={r.title} className="p-4">
              <SectionLabel>{r.title}</SectionLabel>
              <ol className="mt-2 space-y-1">
                <li className="font-mono text-[11.5px] text-muted">{lab.root!.context.id} · root</li>
                {r.steps.map((s, j) => (
                  <li key={j} className="flex items-center gap-1.5 font-mono text-[11.5px] text-ink">
                    <ArrowDown className="size-3 text-muted" /> {s.label}
                    {results?.traces[i][j]?.after && <span className="text-muted">→ {results.traces[i][j].after!.id}</span>}
                  </li>
                ))}
              </ol>
              {end && (
                <div className="mt-3 space-y-1.5 border-t border-line pt-3 text-[12px]">
                  <div><span className="text-muted">ends at</span> <Mono className="text-accent">{end.id}</Mono></div>
                  <div className="text-muted">lineage: {end.lineage.map((s) => s.verb).join(" → ")}</div>
                  {engine && (
                    <div>
                      <span className={cn("rounded-md px-1.5 py-px text-[10.5px] font-semibold", engine.cache.hit ? "bg-good/15 text-good" : "bg-sunken text-ink-2")}>
                        cache {engine.cache.hit ? "HIT" : "miss"}
                      </span>
                    </div>
                  )}
                </div>
              )}
            </Card>
          );
        })}
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <button onClick={go} disabled={busy} className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-accent px-3 text-[12.5px] font-medium text-accent-ink disabled:opacity-50">
          <Play className="size-3.5" /> Run both routes
        </button>
        {results && (
          <span className={cn("inline-flex items-center gap-1.5 text-[13px] font-medium", same ? "text-accent" : "text-critical")}>
            {same ? <Equal className="size-4" /> : <X className="size-4" />}
            {same ? "Same id, different lineage: one question, one cache entry." : "Different ids - these routes don't reach the same question on this model."}
          </span>
        )}
      </div>
    </div>
  );
}

// -- the semantic model ----------------------------------------------------------

export function ModelSection({ lab }: { lab: LabState }) {
  const m = lab.registry!.model;
  const offered = new Set(lab.node!.capabilities.map((c) => c.verb));
  return (
    <div className="grid gap-4 lg:grid-cols-[1.2fr_1fr]">
      <Card className="p-4">
        <SectionLabel>{m.title || m.dataset} - what the YAML declares</SectionLabel>
        <div className="mt-3 space-y-3 text-[12.5px]">
          <div>
            <div className="mb-1 text-[11px] font-medium text-muted">Hierarchies: structural drill follows these, and nothing else</div>
            {m.hierarchies.map((h) => (
              <div key={h.name} className="flex flex-wrap items-center gap-1">
                <span className="mr-1 w-20 text-muted">{h.label}</span>
                {h.labels.map((l, i) => (
                  <span key={l} className="inline-flex items-center gap-1">
                    <span className="rounded-md bg-sunken px-1.5 py-0.5 text-ink">
                      {l} <span className="text-muted tabular">{m.dimensions.find((d) => d.name === h.levels[i])?.members ?? ""}</span>
                    </span>
                    {i < h.labels.length - 1 && <ArrowRight className="size-3 text-muted" />}
                  </span>
                ))}
              </div>
            ))}
          </div>
          <div>
            <div className="mb-1 text-[11px] font-medium text-muted">Standalone dimensions: lateral breakdowns via decompose</div>
            <div className="flex flex-wrap gap-1">
              {m.dimensions.filter((d) => !d.hierarchy).map((d) => (
                <span key={d.name} className="rounded-md bg-sunken px-1.5 py-0.5 text-ink">{d.label} <span className="text-muted tabular">{d.members ?? ""}</span></span>
              ))}
            </div>
          </div>
          <div>
            <div className="mb-1 text-[11px] font-medium text-muted">Metrics: how each one aggregates</div>
            <table className="w-full text-[12px]">
              <tbody>
                {m.metrics.map((x) => (
                  <tr key={x.name} className="border-t border-line first:border-t-0">
                    <td className="py-1 pr-2 text-ink">{x.label}</td>
                    <td className="py-1 pr-2"><span className={cn("rounded-md px-1.5 py-px text-[10.5px] font-medium", x.kind === "ratio" ? "bg-offset/15 text-offset" : "bg-sunken text-ink-2")}>{x.kind}</span></td>
                    <td className="py-1 font-mono text-[11px] text-muted">{x.rule}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex flex-wrap gap-x-6 gap-y-1 text-[12px]">
            <span><span className="text-muted">time</span> <Mono>{m.time_column}</Mono> at {m.grains.join(" / ")}</span>
            <span><span className="text-muted">comparisons</span> {m.comparisons.map((c) => c.name).join(", ")}</span>
          </div>
        </div>
      </Card>
      <Card className="p-4">
        <SectionLabel>From the lab's node, which verbs advertise?</SectionLabel>
        <p className="mt-1 text-[11.5px] text-muted">
          These are recomputed whenever the lab moves. A verb that isn't offered here would be refused by <Mono>apply</Mono> or <Mono>validate</Mono>. The menu and the validator read the same declarations, so they can't drift apart.
        </p>
        <ul className="mt-3 grid grid-cols-2 gap-1">
          {lab.registry!.verbs.map((v) => {
            const on = offered.has(v.name);
            return (
              <li key={v.name} className={cn("flex items-center gap-1.5 rounded-md px-1.5 py-1 font-mono text-[11.5px]", on ? "text-ink" : "text-muted")}>
                {on ? <Check className="size-3.5 text-accent" /> : <X className="size-3.5 text-muted/60" />}
                {v.name}
                {!v.advertises && <span className="font-sans text-[10px] text-muted">(never in menu)</span>}
              </li>
            );
          })}
        </ul>
        <p className="mt-3 text-[11.5px] text-muted">
          A few verbs never advertise themselves: the toolbar, the stack or a mark's own menu reaches them instead (<Mono>focus</Mono>, <Mono>set_time</Mono>, <Mono>drill_up</Mono>…).
        </p>
      </Card>
    </div>
  );
}

// -- explain -----------------------------------------------------------------------

export function ExplainSection({ lab, sid, onOpen }: { lab: LabState; sid: string; onOpen: () => void }) {
  const [exp, setExp] = useState<Explanation | null>(null);
  const [forId, setForId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const node = lab.node!;
  const run = async () => {
    setBusy(true);
    try {
      setExp(await labApi.explain(sid, node.context));
      setForId(node.context.id);
    } finally {
      setBusy(false);
    }
  };
  const max = exp && exp.ok ? Math.max(...exp.evidence.map((e) => e.score), 0.0001) : 1;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <button onClick={run} disabled={busy} className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-accent px-3 text-[12.5px] font-medium text-accent-ink disabled:opacity-50">
          <Sparkles className="size-3.5" /> Explain the lab's node
        </button>
        <span className="font-mono text-[11.5px] text-muted">{node.context.label}</span>
        {exp && forId !== node.context.id && <span className="text-[11.5px] text-offset">The lab has moved on since. Run it again.</span>}
      </div>
      {exp && !exp.ok && <Card className="p-4 text-[12.5px] text-critical">{exp.error}</Card>}
      {exp && exp.ok && (
        <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
          <Card className="space-y-2 p-4 text-[12px]">
            <SectionLabel>What it did</SectionLabel>
            <div><span className="text-muted">explained</span> the scope total, {exp.total} {exp.delta && <span>({exp.delta} vs <Mono>{exp.comparison}</Mono>)</span>}</div>
            <div><span className="text-muted">tested</span> {exp.dimensions_tested.join(", ") || "-"}</div>
            <div><span className="text-muted">most lopsided</span> <span className="font-medium text-ink">{exp.best_dimension ?? "-"}</span></div>
            <div><span className="text-muted">ran</span> {exp.queries} governed queries</div>
            <p className="border-t border-line pt-2 text-[11.5px] text-muted">
              If the node had no comparison, the model's first valid one was attached, since explaining a value means explaining its movement. Each signal ran once per tested dimension, or once for the scope.
            </p>
          </Card>
          <Card className="p-2">
            <ol>
              {exp.evidence.map((e, i) => (
                <li key={i} className="flex items-start gap-3 rounded-lg px-2 py-2 hover:bg-sunken/60">
                  <div className="w-28 shrink-0 pt-0.5">
                    <div className="font-mono text-[10.5px] text-muted">{e.kind}</div>
                    <div className="mt-1 h-1.5 rounded-full bg-sunken">
                      <div className="h-1.5 rounded-full bg-accent" style={{ width: `${(e.score / max) * 100}%` }} />
                    </div>
                    <div className="mt-0.5 font-mono text-[10px] text-muted tabular">
                      {e.score.toFixed(2)} {e.weight != null && <span>· w {e.weight}</span>}
                    </div>
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-[12.5px] font-medium text-ink">{e.headline}</div>
                    {e.detail && <div className="text-[11.5px] text-muted">{e.detail}</div>}
                  </div>
                  {e.target && (
                    <button
                      onClick={() => { void lab.load(e.target!, e.target_kind); onOpen(); }}
                      className="shrink-0 rounded-md border border-line px-1.5 py-0.5 text-[11px] text-ink-2 hover:border-accent hover:text-accent"
                    >
                      Open in lab
                    </button>
                  )}
                </li>
              ))}
            </ol>
          </Card>
        </div>
      )}
    </div>
  );
}

// -- extension points -----------------------------------------------------------------

type Tab = "verbs" | "analyses" | "views" | "signals" | "backends";

const SNIPPETS: Record<Tab, string> = {
  verbs: `from explain_beaucoup.core.operations import Verb, register_verb
from explain_beaucoup.semantic.specs import Capability

@register_verb
class Top3(Verb):
    """Keep the grain, read only the top three members."""
    name = "top3"
    placement = "replace"          # re-frames: replaces the chart on top
    preserves_kind = True

    def apply(self, model, ctx, p, op):
        return ctx.evolve(self.step("Top three", p, op))

    def advertise(self, model, ctx):
        return [Capability(self.name, "Top three", (), "Composition")]

# optional - how the commentary tells it
register_phrase("top3", lambda say: Told("Kept the top three."))`,
  analyses: `from explain_beaucoup.engine.engine import register_analysis, ResultHandle

@register_analysis("pareto", title=lambda model, ctx: "Pareto")
def pareto(engine, ctx) -> ResultHandle:
    metric = engine.model.metric(ctx.metric)
    rows = engine.aggregate(ctx, ctx.time, metric, ctx.grain)  # the primitive
    ...                                                      # build Rows
    return ResultHandle(...)`,
  views: `from explain_beaucoup.view.vega import register_view

# Same renderer: a Vega-Lite spec
register_view("pareto", build_pareto_spec)

# Another library entirely - the client registers a component under the
# same name in web/src/charts/registry.tsx
register_view("matrix", build_deck_spec, renderer="deck.gl")`,
  signals: `from explain_beaucoup.engine.explain import Evidence, register_signal

def seasonality(probe):          # probe.res = this dimension's breakdown
    ...
    return [Evidence("seasonality", probe.dim, member, headline, detail,
                     score, magnitude, direction, target=ctx)]

register_signal("seasonality", seasonality, per="dimension", weight=0.85)`,
  backends: `class WarehouseBackend:            # satisfies the Backend protocol
    name, time_column, native_grain = "sales", "order_date", "day"

    def aggregate(self, q: Query) -> pd.DataFrame:
        # SUM(q.components) WHERE q.filters AND period in q.window
        # GROUP BY q.group_by   (PERIOD = the period key at q.window.grain)
        ...
    def periods(self, grain): ...
    def latest(self, grain): ...
    def time_bounds(self): ...
    def distinct_counts(self, columns): ...
    def sql(self, q): ...            # what provenance shows

Session(WarehouseBackend(), model)`,
};

export function Registry({ lab }: { lab: LabState }) {
  const reg = lab.registry!;
  const [tab, setTab] = useState<Tab>("verbs");
  return (
    <div className="space-y-3">
      <Segmented<Tab>
        value={tab}
        onChange={setTab}
        options={[
          { value: "verbs", label: `Verbs ${reg.verbs.length}` },
          { value: "analyses", label: `Analyses ${reg.analyses.length}` },
          { value: "views", label: `Views ${reg.views.length}` },
          { value: "signals", label: `Signals ${reg.signals.length}` },
          { value: "backends", label: "Backend" },
        ]}
      />
      <div className="grid gap-4 lg:grid-cols-[1.25fr_1fr]">
        <Card className="overflow-hidden">
          {tab === "verbs" && (
            <table className="w-full text-[12px]">
              <thead className="bg-sunken/60 text-left text-[10.5px] uppercase tracking-[0.06em] text-muted">
                <tr><th className="px-3 py-2 font-medium">verb</th><th className="px-2 py-2 font-medium">lands</th><th className="px-2 py-2 font-medium">flags the session reads</th></tr>
              </thead>
              <tbody>
                {reg.verbs.map((v) => (
                  <tr key={v.name} className="border-t border-line align-top">
                    <td className="px-3 py-1.5">
                      <div className="font-mono text-ink">{v.name}</div>
                      <div className="text-[11px] text-muted">{v.doc.split("\n")[0]}</div>
                    </td>
                    <td className="px-2 py-1.5 text-[11.5px] text-ink-2">{v.placement === "replace" ? "replace" : "below"}</td>
                    <td className="px-2 py-1.5">
                      <div className="flex flex-wrap gap-1">
                        <Flag on={v.member_scoped}>member</Flag>
                        <Flag on={v.preserves_kind}>keeps kind</Flag>
                        <Flag on={v.hierarchy_scoped}>hierarchy</Flag>
                        <Flag on={v.advertises}>advertises</Flag>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {tab === "analyses" && (
            <table className="w-full text-[12px]">
              <tbody>
                {reg.analyses.map((a) => (
                  <tr key={a.kind} className="border-t border-line first:border-t-0 align-top">
                    <td className="w-40 px-3 py-2 font-mono text-ink">{a.kind}</td>
                    <td className="px-2 py-2 text-[11.5px] text-muted">{a.doc || <Mono>{a.compute}</Mono>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {tab === "views" && (
            <table className="w-full text-[12px]">
              <tbody>
                {reg.views.map((v) => (
                  <tr key={v.kind} className="border-t border-line first:border-t-0">
                    <td className="w-40 px-3 py-2 font-mono text-ink">{v.kind}</td>
                    <td className="px-2 py-2"><span className="rounded-md bg-sunken px-1.5 py-px font-mono text-[11px] text-ink-2">{v.renderer}</span></td>
                    <td className="px-2 py-2 font-mono text-[11px] text-muted">{v.builder}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {tab === "signals" && (
            <table className="w-full text-[12px]">
              <tbody>
                {reg.signals.map((s) => (
                  <tr key={s.name} className="border-t border-line first:border-t-0 align-top">
                    <td className="w-44 px-3 py-2 font-mono text-ink">{s.name}</td>
                    <td className="px-2 py-2 text-[11px] text-muted">once per {s.per}</td>
                    <td className="px-2 py-2">
                      {s.emits.map((e) => (
                        <div key={e.kind} className="flex items-center gap-2 text-[11.5px]">
                          <span className="w-36 font-mono text-ink-2">{e.kind}</span>
                          <span className="h-1.5 w-16 rounded-full bg-sunken"><span className="block h-1.5 rounded-full bg-accent" style={{ width: `${(e.weight ?? 0) * 100}%` }} /></span>
                          <span className="font-mono text-[10.5px] text-muted">{e.weight ?? "-"}</span>
                        </div>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {tab === "backends" && (
            <div className="space-y-2 p-4 text-[12.5px]">
              <div>This session runs on <Mono>{reg.backend.class}</Mono> (native grain {reg.backend.native_grain}).</div>
              <div className="text-muted">{reg.backend.doc}</div>
              <p className="text-[12px] text-ink-2">
                Every analysis is built from <Mono>backend.aggregate(Query)</Mono>. Ratios are divided by the engine, so a backend only ever sums.
                <Mono>PandasBackend</Mono> and <Mono>DuckDBBackend</Mono> are tested to agree on every result family.
              </p>
            </div>
          )}
        </Card>
        <div>
          <Code className="text-[11px]">{SNIPPETS[tab]}</Code>
          <p className="mt-2 text-[11.5px] text-muted">
            {tab === "verbs" && "One class and one call. The menu, the session's stack rules, the commentary and every front end pick it up."}
            {tab === "analyses" && "A result family: compute it from the aggregate primitive and give it a title. The engine caches it like any other."}
            {tab === "views" && "How a kind is drawn. The renderer name is a contract with the client, not a library choice."}
            {tab === "signals" && "Evidence for “Explain this”. The weight ranks your family against the built-in ones."}
            {tab === "backends" && "Anything that can sum, group and answer a few calendar questions can hold the data."}
          </p>
        </div>
      </div>
    </div>
  );
}

// -- layers ---------------------------------------------------------------------------

const LAYERS: { name: string; modules: string; says: string; tone?: "front" | "core" | "data" }[] = [
  { name: "Front ends", modules: "web/src · ui/ (Qt) · a notebook · an agent", says: "Forward gestures, draw what they are told. No analytical logic.", tone: "front" },
  { name: "Session", modules: "core/session.py · view/stack.py", says: "Interaction rules: where a result lands, the menu, selection, the journal and commentary." },
  { name: "Grammar", modules: "core/context.py · core/operations.py · core/verbs.py", says: "Context values and the verbs that map one to another.", tone: "core" },
  { name: "Engine", modules: "engine/engine.py · engine/explain.py", says: "Analyses and signals: every number, deterministically.", tone: "core" },
  { name: "Semantic model", modules: "semantic/specs.py · semantic/loader.py", says: "What exists and what is legal - generated menus, validation." },
  { name: "Backend", modules: "engine/backend.py", says: "One primitive: aggregate(Query). pandas or SQL.", tone: "data" },
];

export function Layers() {
  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_300px]">
      <div className="space-y-1.5">
        {LAYERS.map((l, i) => (
          <div key={l.name}>
            <div className={cn(
              "grid grid-cols-[130px_1fr] items-baseline gap-3 rounded-xl border px-4 py-2.5 sm:grid-cols-[150px_1fr_1.2fr]",
              l.tone === "core" ? "border-accent/30 bg-accent-soft" : "border-line bg-surface",
            )}>
              <span className="text-[13px] font-semibold text-ink">{l.name}</span>
              <span className="font-mono text-[11px] text-muted">{l.modules}</span>
              <span className="col-span-2 text-[12px] text-ink-2 sm:col-span-1">{l.says}</span>
            </div>
            {i < LAYERS.length - 1 && <div className="flex justify-center py-0.5 text-muted"><ArrowDown className="size-3.5" /></div>}
          </div>
        ))}
      </div>
      <Card className="self-start p-4 text-[12px] leading-relaxed text-ink-2">
        <SectionLabel>The one rule</SectionLabel>
        <p className="mt-2">
          Imports point down only. The engine has no idea a UI exists, which is why the whole framework is tested without opening a window, and why a second front end behaves exactly like the first.
        </p>
        <p className="mt-2">
          Contexts travel up and down as plain values. That's how this page can hand the browser a Context, let you click around, and send it back to the engine unchanged.
        </p>
        <p className="mt-2 text-muted">Example: <Mono>{show({ grain: ["region"] })}</Mono> crosses every layer without changing.</p>
      </Card>
    </div>
  );
}
