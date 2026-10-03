import * as DM from "@radix-ui/react-dropdown-menu";
import * as Tabs from "@radix-ui/react-tabs";
import { AnimatePresence, motion } from "motion/react";
import { ArrowRight, ArrowUpRight, ChevronDown, Copy, MessageSquareText, RotateCcw, Sparkles, TrendingDown, TrendingUp, X } from "lucide-react";
import { useEffect, useRef } from "react";
import { toast } from "sonner";
import { api } from "../api";
import type { BeatLine, Evidence } from "../api";
import { Inline, Markdown } from "../md";
import { useWorkspace } from "../store";
import type { RightTab } from "../store";
import { cn, Empty, LandsMark, SectionLabel, Segmented } from "../ui";
import { TabList } from "./LeftPanel";

export function RightPanel() {
  const { rightTab, setRightTab, snap, act } = useWorkspace();
  const beats = snap.commentary.lines.filter((l) => !l.hidden).length;
  return (
    <Tabs.Root value={rightTab} onValueChange={(v) => setRightTab(v as RightTab)} className="flex h-full flex-col bg-surface">
      <TabList
        tabs={[
          { value: "explain", label: "Explain" },
          { value: "commentary", label: "Commentary", count: beats },
          { value: "next", label: "Next" },
          { value: "context", label: "Context" },
          { value: "report", label: "Report" },
        ]}
      />
      <Tabs.Content value="explain" className="min-h-0 flex-1 overflow-y-auto outline-none">
        <ExplainTab />
      </Tabs.Content>
      <Tabs.Content value="commentary" className="flex min-h-0 flex-1 flex-col outline-none">
        <CommentaryTab />
      </Tabs.Content>
      <Tabs.Content value="next" className="min-h-0 flex-1 overflow-y-auto outline-none">
        <NextTab />
      </Tabs.Content>
      <Tabs.Content value="context" className="min-h-0 flex-1 overflow-y-auto outline-none">
        <ContextTab />
      </Tabs.Content>
      <Tabs.Content value="report" className="min-h-0 flex-1 overflow-y-auto p-5 outline-none">
        <Markdown text={snap.report} onNode={(uid) => void act({ type: "raise", uid })} />
      </Tabs.Content>
    </Tabs.Root>
  );
}

// -- explain -----------------------------------------------------------------

const ROLE: Record<Evidence["role"], string> = {
  pos: "var(--pos)",
  neg: "var(--neg)",
  offset: "var(--offset)",
  link: "var(--link)",
  muted: "var(--muted)",
};

function ExplainTab() {
  const { snap, act } = useWorkspace();
  const exp = snap.explanation;
  if (!exp) {
    return (
      <Empty title="Nothing explained yet" icon={<Sparkles className="size-4" />}>
        Press <b className="text-ink-2">Explain</b> or <b className="text-ink-2">E</b>, or right-click a mark and choose{" "}
        <b className="text-ink-2">Explain this</b>. Contribution, change, offsets, concentration and anomalies are ranked
        deterministically, with the numbers attached, and every card is a place you can go.
      </Empty>
    );
  }
  const max = Math.max(...exp.evidence.map((e) => e.score), 1);
  return (
    <div className="p-4">
      <motion.div key={exp.context_id} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="px-1 pb-4">
        <div className="text-[30px] leading-none font-semibold tracking-[-0.03em] text-ink tabular">{exp.total}</div>
        {exp.delta && (
          <div
            className="mt-2 inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[12px] font-semibold tabular"
            style={{
              color: exp.delta.tone === "good" ? "var(--good)" : "var(--critical)",
              background: `color-mix(in oklab, ${exp.delta.tone === "good" ? "var(--good)" : "var(--critical)"} 10%, transparent)`,
            }}
          >
            {exp.delta.up ? <TrendingUp className="size-3.5" /> : <TrendingDown className="size-3.5" />}
            {exp.delta.text}
            {exp.delta.pct !== null && ` (${exp.delta.pct >= 0 ? "+" : ""}${(exp.delta.pct * 100).toFixed(1)}%)`}
            <span className="font-medium opacity-70">vs {exp.delta.comparison}</span>
          </div>
        )}
        <div className="mt-2 text-[11.5px] text-muted">{exp.where}</div>
        {exp.best_dimension && (
          <div className="mt-1 text-[12px] text-ink-2">
            Strongest explanatory breakdown: <b className="font-semibold text-ink">{exp.best_dimension}</b>
          </div>
        )}
      </motion.div>
      {exp.evidence.length === 0 && (
        <p className="px-1 text-[12px] text-muted">
          No evidence passed the ranking thresholds for this node. Try a comparison period, or a coarser grain.
        </p>
      )}
      <div className="space-y-2">
        {exp.evidence.map((ev, i) => (
          <motion.button
            key={`${exp.context_id}-${ev.index}`}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.03 * i, duration: 0.25 }}
            disabled={!ev.openable}
            onClick={() => act({ type: "evidence", index: ev.index })}
            className="group block w-full rounded-xl border border-line bg-raised p-3 text-left shadow-sm transition-all enabled:hover:-translate-y-px enabled:hover:border-line-strong enabled:hover:shadow-md"
          >
            <div className="flex items-center gap-2">
              <span className="text-[10px] font-bold tracking-[0.08em] uppercase" style={{ color: ROLE[ev.role] }}>
                {ev.label}
              </span>
              <span className="ml-auto h-1 w-14 overflow-hidden rounded-full bg-sunken" title={`Evidence score ${ev.score.toFixed(2)}`}>
                <span className="block h-full rounded-full" style={{ width: `${Math.max(8, (ev.score / max) * 100)}%`, background: ROLE[ev.role] }} />
              </span>
            </div>
            <div className="mt-1.5 text-[13px] leading-snug font-semibold text-ink">{ev.headline}</div>
            <div className="mt-1 text-[12px] leading-relaxed text-ink-2">{ev.detail}</div>
            {ev.openable && (
              <div className="mt-2 inline-flex items-center gap-1 text-[11px] font-medium text-muted transition-colors group-hover:text-accent">
                Open as a chart <ArrowUpRight className="size-3" />
              </div>
            )}
          </motion.button>
        ))}
      </div>
      <p className="mt-4 px-1 text-[11px] text-muted">From {exp.queries} governed queries.</p>
    </div>
  );
}

// -- commentary --------------------------------------------------------------

function CommentaryTab() {
  const { snap, act, layout, setLayout } = useWorkspace();
  const { lines, newest, removed } = snap.commentary;
  const scroller = useRef<HTMLDivElement>(null);
  const seen = useRef<number | null>(null);

  useEffect(() => {
    // Follow a new move; an edit keeps the reader where they were.
    if (newest !== null && newest !== seen.current) {
      seen.current = newest;
      scroller.current?.querySelector(`[data-seq="${newest}"]`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [newest]);

  const copy = async (fmt: "formatted" | "plain" | "json") => {
    try {
      if (fmt === "formatted") {
        const [md, html] = await Promise.all([
          api.commentary(snap.session, "markdown", layout),
          api.commentary(snap.session, "html", layout),
        ]);
        await navigator.clipboard.write([
          new ClipboardItem({
            "text/plain": new Blob([md], { type: "text/plain" }),
            "text/html": new Blob([html], { type: "text/html" }),
          }),
        ]);
      } else {
        await navigator.clipboard.writeText(await api.commentary(snap.session, fmt === "json" ? "json" : "plain", layout));
      }
      const n = lines.filter((l) => !l.hidden).length;
      toast(`Copied ${n} step${n === 1 ? "" : "s"} of commentary`, { description: fmt === "formatted" ? "Rich text, with markdown alongside" : fmt === "json" ? "Nested JSON" : "Plain text" });
    } catch (e) {
      toast.error("Could not copy", { description: String((e as Error).message) });
    }
  };

  return (
    <>
      <div className="flex shrink-0 items-center gap-2 border-b border-line px-4 py-2.5">
        <Segmented
          value={layout}
          onChange={setLayout}
          options={[
            { value: "tree", label: "Tree" },
            { value: "timeline", label: "Timeline" },
          ]}
        />
        {removed > 0 && (
          <button
            onClick={() => act({ type: "restore_beat", index: null })}
            className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[11.5px] text-muted hover:bg-sunken hover:text-ink"
          >
            <RotateCcw className="size-3" /> Restore {removed}
          </button>
        )}
        <DM.Root>
          <div className="ml-auto flex items-center rounded-lg border border-line bg-raised shadow-sm">
            <button onClick={() => copy("formatted")} className="inline-flex h-7 items-center gap-1.5 pr-2 pl-2.5 text-[12px] font-medium text-ink-2 hover:text-ink">
              <Copy className="size-3.5" /> Copy
            </button>
            <DM.Trigger className="grid h-7 w-6 place-items-center border-l border-line text-muted hover:text-ink">
              <ChevronDown className="size-3.5" />
            </DM.Trigger>
          </div>
          <DM.Portal>
            <DM.Content align="end" sideOffset={6} className="pop z-50 min-w-60 rounded-xl border border-line bg-raised p-1 shadow-lg">
              {([
                ["formatted", "Formatted", "Docs and email, markdown alongside"],
                ["plain", "Plain text", "Indentation carries the tree"],
                ["json", "JSON", "Nested like the map, with state as data"],
              ] as const).map(([k, label, hint]) => (
                <DM.Item key={k} onSelect={() => copy(k)} className="flex cursor-default flex-col rounded-lg px-2.5 py-1.5 outline-none data-[highlighted]:bg-sunken">
                  <span className="text-[12.5px] text-ink">{label}</span>
                  <span className="text-[11px] text-muted">{hint}</span>
                </DM.Item>
              ))}
            </DM.Content>
          </DM.Portal>
        </DM.Root>
      </div>
      <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
        {lines.length === 0 ? (
          <Empty title="Nothing investigated yet" icon={<MessageSquareText className="size-4" />} />
        ) : (
          <AnimatePresence initial={false}>
            {lines.map((l) => (
              <Beat key={l.seq} line={l} newest={l.seq === newest} timeline={layout === "timeline"} />
            ))}
          </AnimatePresence>
        )}
      </div>
    </>
  );
}

function Beat({ line, newest, timeline }: { line: BeatLine; newest: boolean; timeline: boolean }) {
  const { act } = useWorkspace();
  const onNode = (uid: string) => void act({ type: "raise", uid });
  const pad = timeline ? 0 : line.depth * 16;
  return (
    <motion.div
      data-seq={line.seq}
      layout="position"
      initial={{ opacity: 0, x: -6 }}
      animate={{ opacity: 1, x: 0 }}
      className={cn(
        "group relative rounded-lg py-1.5 pr-7 pl-2 transition-colors",
        line.head && !timeline ? "mt-2.5" : "mt-0.5",
        newest && "bg-hilite",
      )}
      style={{ marginLeft: pad }}
    >
      {!timeline && line.depth > 0 && (
        <span className={cn("absolute top-0 -left-2 w-px bg-line", line.head ? "h-4" : "h-full")} />
      )}
      {line.hidden ? (
        <div className="flex items-baseline gap-2 text-[12px] text-muted">
          <s className="truncate">{line.move.replace(/\[([^\]]+)\]\([^)]+\)/g, "$1").replace(/\*\*/g, "")}</s>
          <button onClick={() => act({ type: "restore_beat", index: line.seq })} className="shrink-0 text-[11.5px] font-medium text-accent hover:underline">
            restore
          </button>
        </div>
      ) : (
        <>
          <div className="text-[12.5px] leading-snug">
            <span className="mr-1.5 text-[11px] font-medium text-muted tabular">#{line.number}</span>
            <span className={line.head ? "font-semibold text-ink" : "text-ink"}>
              <Inline text={line.move} onNode={onNode} />
            </span>
            {line.selected && (
              <span className="text-ink-2">
                {" "}
                <Inline text={line.selected} onNode={onNode} />
              </span>
            )}
          </div>
          {(line.question || line.answer) && (
            <div className="mt-1 pl-5 text-[12px] leading-relaxed text-ink-2">
              {line.question && <em className="mr-1 text-muted italic"><Inline text={line.question} onNode={onNode} /></em>}
              <Inline text={line.answer} onNode={onNode} />
            </div>
          )}
          <button
            onClick={() => act({ type: "hide_beat", index: line.seq })}
            title="Remove from the commentary"
            className="absolute top-1.5 right-1.5 grid size-5 place-items-center rounded-md text-muted opacity-0 transition-opacity group-hover:opacity-100 hover:bg-sunken hover:text-critical"
          >
            <X className="size-3" />
          </button>
        </>
      )}
    </motion.div>
  );
}

// -- next ------------------------------------------------------------------------

function NextTab() {
  const { snap, act } = useWorkspace();
  return (
    <div className="p-4">
      <p className="mb-3 px-1 text-[11.5px] leading-relaxed text-muted">
        Generated from the current metric, grain, hierarchy and comparison. Each one runs the operation it names.
      </p>
      <div className="space-y-1.5">
        {snap.questions.map((q, i) => (
          <motion.button
            key={`${snap.current}-${i}`}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.025 * i }}
            onClick={() => act({ type: "run", verb: q.verb, params: q.params, source: "next" })}
            className="group flex w-full items-center gap-3 rounded-xl border border-line bg-raised px-3.5 py-2.5 text-left shadow-sm transition-all hover:-translate-y-px hover:border-line-strong hover:shadow-md"
          >
            <span className="flex-1">
              <span className="block text-[12.5px] leading-snug text-ink">{q.text}</span>
              {q.lands && (
                <span className="mt-0.5 block">
                  <LandsMark lands={q.lands} label />
                </span>
              )}
            </span>
            <ArrowRight className="size-3.5 shrink-0 text-muted transition-transform group-hover:translate-x-0.5 group-hover:text-accent" />
          </motion.button>
        ))}
      </div>
    </div>
  );
}

// -- context -----------------------------------------------------------------

function ContextTab() {
  const { snap } = useWorkspace();
  const c = snap.context;
  return (
    <div className="space-y-5 p-4">
      <dl className="grid grid-cols-[96px_1fr] gap-x-3 gap-y-2 px-1">
        {c.rows.map((r) => (
          <div key={r.key} className="contents">
            <dt className="text-[11.5px] text-muted">{r.key}</dt>
            <dd className={cn("min-w-0 text-[12px] break-words text-ink", /id$/.test(r.key) && "font-mono text-[11px] text-ink-2")}>{r.value}</dd>
          </div>
        ))}
      </dl>
      <div>
        <SectionLabel className="mb-2 px-1">Provenance</SectionLabel>
        <pre className="overflow-x-auto rounded-xl border border-line bg-sunken p-3 font-mono text-[10.5px] leading-relaxed whitespace-pre-wrap text-ink-2 select-text">
          {c.provenance}
        </pre>
      </div>
      {c.notes.length > 0 && (
        <div>
          <SectionLabel className="mb-2 px-1">Notes</SectionLabel>
          <ul className="space-y-1 px-1 text-[12px] text-ink-2">
            {c.notes.map((n, i) => (
              <li key={i}>· {n}</li>
            ))}
          </ul>
        </div>
      )}
      <div>
        <SectionLabel className="mb-2 px-1">Lineage</SectionLabel>
        {c.lineage.length === 0 ? (
          <p className="px-1 text-[12px] text-muted">Root node.</p>
        ) : (
          <ol className="relative ml-2 space-y-2.5 border-l border-line pl-4">
            {c.lineage.map((s, i) => (
              <li key={i} className="relative text-[12px] leading-snug">
                <span className="absolute top-1.5 -left-[20.5px] size-2 rounded-full border-2 border-surface bg-accent" />
                <span className="font-semibold text-ink">{s.verb.replace(/_/g, " ")}</span>
                <span className="text-ink-2"> — {s.summary}</span>
                {s.source && <span className="ml-1 text-[11px] text-muted">({s.source})</span>}
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}
