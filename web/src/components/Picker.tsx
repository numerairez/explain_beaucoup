import * as Popover from "@radix-ui/react-popover";
import { AnimatePresence, motion } from "motion/react";
import { ArrowRight, Check, ChevronDown, CircleAlert, Database, Info, Plus, RotateCcw, Search, TriangleAlert, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { CatalogEntry, EntryDetail, Preview } from "../api";
import { cn, Logo, PillSelect, SectionLabel } from "../ui";

type Filter = { id: number; dim: string; members: string[] };
let nextId = 1;

export function Picker({ onOpen }: { onOpen: (session: string) => void }) {
  const [entries, setEntries] = useState<CatalogEntry[] | null>(null);
  const [dir, setDir] = useState("");
  const [index, setIndex] = useState<number | null>(null);
  const [detail, setDetail] = useState<EntryDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filter[]>([]);
  const [range, setRange] = useState<[number, number]>([0, 0]);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [opening, setOpening] = useState(false);

  useEffect(() => {
    api.catalog().then((c) => {
      setEntries(c.entries);
      setDir(c.models_dir);
      const first = c.entries.find((e) => e.ok);
      if (first) setIndex(first.index);
    });
  }, []);

  useEffect(() => {
    if (index === null) return;
    setDetail(null);
    setLoadError(null);
    setFilters([]);
    setPreview(null);
    api
      .entry(index)
      .then((d) => {
        setDetail(d);
        setRange([0, d.periods.length - 1]);
      })
      .catch((e) => setLoadError(String(e.message)));
  }, [index]);

  const scope = useMemo(() => {
    if (!detail) return null;
    const f: Record<string, string[]> = {};
    for (const x of filters) if (x.dim && x.members.length) f[x.dim] = x.members;
    const last = detail.periods.length - 1;
    return {
      filters: f,
      start: range[0] > 0 ? detail.periods[range[0]].value : null,
      end: range[1] < last ? detail.periods[range[1]].value : null,
      dims: filters.map((x) => x.dim),
    };
  }, [detail, filters, range]);

  useEffect(() => {
    if (!detail || !scope) return;
    const t = window.setTimeout(() => {
      api.preview(detail.index, scope).then(setPreview).catch(() => undefined);
    }, 120);
    return () => window.clearTimeout(t);
  }, [detail, scope]);

  const taken = new Set(filters.map((f) => f.dim));
  const free = detail?.filterable.filter((d) => !taken.has(d.value)) ?? [];
  const scoped = !!scope && (Object.keys(scope.filters).length > 0 || scope.start !== null || scope.end !== null);

  const open = async () => {
    if (!detail || !scope) return;
    setOpening(true);
    try {
      const { session } = await api.open(detail.index, scope);
      onOpen(session);
    } catch (e) {
      setLoadError(String((e as Error).message));
      setOpening(false);
    }
  };

  const setPeriod = (which: 0 | 1, i: number) =>
    setRange(([s, e]) => (which === 0 ? [i, Math.max(i, e)] : [Math.min(s, i), i]));

  return (
    <div className="min-h-full bg-paper">
      <div className="mx-auto max-w-[1120px] px-8 pt-10 pb-16">
        <header className="flex items-center gap-3">
          <Logo size={30} />
          <div>
            <div className="text-[15px] font-semibold tracking-[-0.01em] text-ink">Explain Beaucoup</div>
            <div className="text-[12px] text-muted">An explorable graph of analytical contexts</div>
          </div>
          <a href="#/guide" className="ml-auto rounded-lg px-2.5 py-1.5 text-[12.5px] font-medium text-ink-2 hover:bg-sunken hover:text-ink">
            How it works →
          </a>
        </header>

        <motion.h1
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          className="mt-12 text-[34px] leading-tight font-semibold tracking-[-0.03em] text-ink"
        >
          Open a dataset
        </motion.h1>
        <p className="mt-2 max-w-xl text-[14px] leading-relaxed text-ink-2">
          Choose a model, then narrow it if the question is about part of the table. The scope is locked in before the
          workspace opens: every total, share and explanation is computed against it.
        </p>
        <p className="mt-1 font-mono text-[11px] text-muted">{dir}</p>

        <div className="mt-8 grid grid-cols-1 gap-6 lg:grid-cols-[320px_1fr]">
          <div className="space-y-2">
            {entries === null && <div className="h-24 animate-pulse rounded-2xl bg-sunken" />}
            {entries?.length === 0 && (
              <div className="rounded-2xl border border-dashed border-line-strong p-5 text-[12.5px] leading-relaxed text-muted">
                No models here yet. Scaffold one with{" "}
                <code className="rounded bg-sunken px-1 font-mono text-[11.5px]">explain-beaucoup init &lt;data&gt;</code>.
              </div>
            )}
            {entries?.map((e) => (
              <button
                key={e.index}
                disabled={!e.ok}
                onClick={() => setIndex(e.index)}
                title={e.problem || e.path}
                className={cn(
                  "w-full rounded-2xl border p-4 text-left transition-all",
                  index === e.index
                    ? "border-accent/60 bg-surface shadow-md ring-4 ring-accent/10"
                    : "border-line bg-surface shadow-sm enabled:hover:-translate-y-px enabled:hover:shadow-md",
                  !e.ok && "opacity-60",
                )}
              >
                <div className="flex items-center gap-2.5">
                  <span className={cn("grid size-8 place-items-center rounded-lg", e.ok ? "bg-accent-soft text-accent" : "bg-sunken text-critical")}>
                    {e.ok ? <Database className="size-4" /> : <CircleAlert className="size-4" />}
                  </span>
                  <div className="min-w-0">
                    <div className="truncate text-[13.5px] font-semibold text-ink">{e.name}</div>
                    <div className={cn("truncate text-[11.5px]", e.ok ? "text-muted" : "text-critical")}>{e.summary}</div>
                  </div>
                </div>
              </button>
            ))}
          </div>

          <div className="rounded-3xl border border-line bg-surface p-7 shadow-md">
            {loadError && (
              <div className="flex gap-2 rounded-xl bg-critical/10 p-3 text-[12.5px] text-critical">
                <TriangleAlert className="mt-px size-4 shrink-0" />
                {loadError}
              </div>
            )}
            {!detail && !loadError && <DetailSkeleton />}
            {detail && (
              <motion.div key={detail.index} initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <h2 className="text-[22px] font-semibold tracking-[-0.02em] text-ink">{detail.title}</h2>
                    <p className="mt-0.5 font-mono text-[11px] text-muted">{detail.path}</p>
                  </div>
                </div>

                <div className="mt-5 grid grid-cols-3 gap-3">
                  <Stat label="Rows" value={detail.rows.toLocaleString()} />
                  <Stat label={`${detail.grain}s`} value={String(detail.periods.length)} sub={`${detail.periods[0]?.label} – ${detail.periods.at(-1)?.label}`} />
                  <Stat label="Grains" value={String(detail.grains.length)} sub={detail.grains.join(", ")} />
                </div>

                <div className="mt-5 space-y-3">
                  <Chips label="Metrics" items={detail.metrics} />
                  <Chips label="Dimensions" items={detail.dimensions} />
                  {detail.hierarchies.length > 0 && (
                    <div>
                      <SectionLabel className="mb-1.5">Drill paths</SectionLabel>
                      <div className="flex flex-wrap gap-2">
                        {detail.hierarchies.map((h) => (
                          <span key={h.label} className="inline-flex items-center gap-1 rounded-lg bg-sunken px-2 py-1 text-[11.5px] text-ink-2">
                            {h.levels.map((l, i) => (
                              <span key={l} className="inline-flex items-center gap-1">
                                {i > 0 && <ArrowRight className="size-3 text-muted" />}
                                {l.replace(/_/g, " ")}
                              </span>
                            ))}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                <div className="mt-7 border-t border-line pt-6">
                  <div className="flex items-baseline justify-between">
                    <h3 className="text-[14px] font-semibold text-ink">Scope</h3>
                    {scoped && (
                      <button
                        onClick={() => {
                          setFilters([]);
                          setRange([0, detail.periods.length - 1]);
                        }}
                        className="inline-flex items-center gap-1 text-[12px] text-muted hover:text-ink"
                      >
                        <RotateCcw className="size-3" /> Reset
                      </button>
                    )}
                  </div>
                  <p className="mt-1 text-[12px] text-muted">Leave it alone to analyse the whole table.</p>

                  <div className="mt-4 flex items-center gap-2">
                    <span className="w-20 text-[12px] text-muted">Period</span>
                    <div className="flex items-center rounded-xl border border-line bg-raised p-0.5">
                      <PillSelect
                        label="From"
                        value={String(range[0])}
                        options={detail.periods.map((p, i) => ({ value: String(i), label: p.label }))}
                        onChange={(v) => setPeriod(0, Number(v))}
                      />
                      <span className="h-4 w-px bg-line" />
                      <PillSelect
                        label="To"
                        value={String(range[1])}
                        options={detail.periods.map((p, i) => ({ value: String(i), label: p.label }))}
                        onChange={(v) => setPeriod(1, Number(v))}
                      />
                    </div>
                  </div>

                  <div className="mt-2 space-y-2">
                    <AnimatePresence initial={false}>
                      {filters.map((f) => (
                        <motion.div
                          key={f.id}
                          initial={{ opacity: 0, height: 0 }}
                          animate={{ opacity: 1, height: "auto" }}
                          exit={{ opacity: 0, height: 0 }}
                          className="flex items-center gap-2"
                        >
                          <span className="w-20 text-[12px] text-muted">Where</span>
                          <div className="flex items-center rounded-xl border border-line bg-raised p-0.5">
                            <PillSelect
                              label=""
                              value={f.dim}
                              options={detail.filterable.filter((d) => d.value === f.dim || !taken.has(d.value))}
                              onChange={(v) => setFilters((fs) => fs.map((x) => (x.id === f.id ? { ...x, dim: v, members: [] } : x)))}
                            />
                          </div>
                          <span className="text-[12px] text-muted">is</span>
                          <Members
                            all={preview?.members[f.dim] ?? []}
                            picked={f.members}
                            onChange={(m) => setFilters((fs) => fs.map((x) => (x.id === f.id ? { ...x, members: m } : x)))}
                          />
                          <button
                            onClick={() => setFilters((fs) => fs.filter((x) => x.id !== f.id))}
                            className="grid size-7 place-items-center rounded-lg text-muted hover:bg-sunken hover:text-ink"
                          >
                            <X className="size-3.5" />
                          </button>
                        </motion.div>
                      ))}
                    </AnimatePresence>
                  </div>
                  <button
                    disabled={!free.length}
                    onClick={() => setFilters((fs) => [...fs, { id: nextId++, dim: free[0].value, members: [] }])}
                    className="mt-3 ml-22 inline-flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-[12.5px] font-medium text-accent hover:bg-accent-soft disabled:opacity-40"
                  >
                    <Plus className="size-3.5" /> Add a filter
                  </button>
                </div>

                <div className="mt-6 flex flex-wrap items-end gap-6 rounded-2xl bg-sunken/70 p-5">
                  <div className="min-w-0 flex-1">
                    {preview ? <PreviewLine p={preview} /> : <div className="h-10 animate-pulse rounded-lg bg-sunken" />}
                  </div>
                  <button
                    disabled={!preview?.ok || opening}
                    onClick={open}
                    className="inline-flex h-11 items-center gap-2 rounded-xl bg-accent px-5 text-[13.5px] font-semibold text-accent-ink shadow-md transition-all hover:brightness-110 active:scale-[0.98] disabled:opacity-40"
                  >
                    {opening ? "Opening…" : scoped ? "Open scoped workspace" : "Open workspace"}
                    <ArrowRight className="size-4" />
                  </button>
                </div>
                {preview && (preview.problems.length > 0 || preview.notes.length > 0) && (
                  <div className="mt-3 space-y-1.5">
                    {preview.problems.map((m) => (
                      <p key={m} className="flex gap-2 text-[12px] text-critical"><TriangleAlert className="mt-0.5 size-3.5 shrink-0" />{m}</p>
                    ))}
                    {preview.notes.map((m) => (
                      <p key={m} className="flex gap-2 text-[12px] text-muted"><Info className="mt-0.5 size-3.5 shrink-0" />{m}</p>
                    ))}
                  </div>
                )}
              </motion.div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function PreviewLine({ p }: { p: Preview }) {
  const share = p.rows / Math.max(p.total_rows, 1);
  return (
    <div>
      <div className="flex items-baseline gap-2 tabular">
        <span className="text-[22px] font-semibold tracking-[-0.02em] text-ink">{p.rows.toLocaleString()}</span>
        <span className="text-[12.5px] text-muted">of {p.total_rows.toLocaleString()} rows · {Math.round(share * 100)}%</span>
      </div>
      <div className="mt-2 h-1.5 w-full max-w-sm overflow-hidden rounded-full bg-line">
        <motion.div className="h-full rounded-full bg-accent" animate={{ width: `${share * 100}%` }} transition={{ type: "spring", stiffness: 200, damping: 30 }} />
      </div>
      <div className="mt-2 text-[12px] text-ink-2">
        {p.span ? `${p.periods} ${p.grain}s, ${p.span[0]} to ${p.span[1]}` : "No periods"} · {p.breakdowns} dimension{p.breakdowns === 1 ? "" : "s"} to break down by
      </div>
    </div>
  );
}

function Members({ all, picked, onChange }: { all: string[]; picked: string[]; onChange: (m: string[]) => void }) {
  const [q, setQ] = useState("");
  const shown = all.filter((m) => m.toLowerCase().includes(q.toLowerCase()));
  const set = new Set(picked);
  const label = picked.length === 0 ? "any" : picked.length <= 2 ? picked.join(", ") : `${picked.length} of ${all.length}`;
  return (
    <Popover.Root onOpenChange={(o) => !o && setQ("")}>
      <Popover.Trigger className="inline-flex h-9 max-w-64 items-center gap-1.5 rounded-xl border border-line bg-raised px-3 text-[12.5px] font-semibold text-ink shadow-sm hover:border-line-strong data-[state=open]:border-accent/50">
        <span className={cn("truncate", picked.length === 0 && "font-normal text-muted")}>{label}</span>
        <ChevronDown className="size-3.5 shrink-0 text-muted" />
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content align="start" sideOffset={6} className="pop z-50 w-72 overflow-hidden rounded-xl border border-line bg-raised shadow-lg">
          <div className="flex items-center gap-2 border-b border-line px-3">
            <Search className="size-3.5 text-muted" />
            <input
              autoFocus
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={`Search ${all.length} members`}
              className="h-9 flex-1 bg-transparent text-[12.5px] outline-none placeholder:text-muted"
            />
          </div>
          <div className="flex gap-1 border-b border-line px-2 py-1.5 text-[11.5px]">
            <button onClick={() => onChange([])} className="rounded-md px-2 py-0.5 text-muted hover:bg-sunken hover:text-ink">Any</button>
            <button onClick={() => onChange(Array.from(new Set([...picked, ...shown])))} className="rounded-md px-2 py-0.5 text-muted hover:bg-sunken hover:text-ink">
              Select {q ? "matching" : "all"}
            </button>
          </div>
          <div className="max-h-64 overflow-y-auto p-1">
            {shown.length === 0 && <div className="py-6 text-center text-[12px] text-muted">No members match</div>}
            {shown.map((m) => (
              <button
                key={m}
                onClick={() => onChange(set.has(m) ? picked.filter((x) => x !== m) : [...picked, m])}
                className="flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left text-[12.5px] text-ink hover:bg-sunken"
              >
                <span className={cn("grid size-4 place-items-center rounded-[5px] border transition-colors", set.has(m) ? "border-accent bg-accent text-accent-ink" : "border-line-strong")}>
                  {set.has(m) && <Check className="size-3" strokeWidth={3} />}
                </span>
                <span className="truncate">{m}</span>
              </button>
            ))}
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-xl border border-line bg-raised p-3.5">
      <div className="text-[10.5px] font-semibold tracking-[0.08em] text-muted uppercase">{label}</div>
      <div className="mt-1 text-[20px] font-semibold tracking-[-0.02em] text-ink tabular">{value}</div>
      {sub && <div className="mt-0.5 truncate text-[11px] text-muted">{sub}</div>}
    </div>
  );
}

function Chips({ label, items }: { label: string; items: string[] }) {
  return (
    <div>
      <SectionLabel className="mb-1.5">{label}</SectionLabel>
      <div className="flex flex-wrap gap-1.5">
        {items.map((i) => (
          <span key={i} className="rounded-lg bg-sunken px-2 py-1 text-[11.5px] text-ink-2">{i}</span>
        ))}
      </div>
    </div>
  );
}

function DetailSkeleton() {
  return (
    <div className="space-y-4">
      <div className="h-7 w-1/2 animate-pulse rounded-lg bg-sunken" />
      <div className="grid grid-cols-3 gap-3">
        {[0, 1, 2].map((i) => <div key={i} className="h-20 animate-pulse rounded-xl bg-sunken" />)}
      </div>
      <div className="h-24 animate-pulse rounded-xl bg-sunken" />
    </div>
  );
}
