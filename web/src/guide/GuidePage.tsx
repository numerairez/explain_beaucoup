import { ArrowLeft, ArrowRight, Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api";
import type { Theme } from "../api";
import { applyTheme, readTheme } from "../store";
import { cn, IconButton, Logo, SectionLabel } from "../ui";
import { ContextCard, FIELDS } from "./ContextCard";
import { Lab, Strip } from "./Lab";
import { Card, Code, Mono, Section, STAGES } from "./parts";
import { ExplainSection, Identity, Layers, ModelSection, Registry } from "./Sections";
import { useLab } from "./useLab";

const TOC = [
  { id: "idea", title: "The one idea" },
  { id: "context", title: "Anatomy of a Context" },
  { id: "lab", title: "One move, every stage" },
  { id: "identity", title: "Same question, same id" },
  { id: "model", title: "The model decides what's legal" },
  { id: "explain", title: "Evidence you can open" },
  { id: "extend", title: "Extension points" },
  { id: "layers", title: "Layers" },
];

/** "How it works": a guided, live tour of the framework for developers.
 *  Everything on it runs against the real grammar and engine. */
export function GuidePage({ sid: initialSid, onBack }: { sid: string | null; onBack: (sid: string | null) => void }) {
  const [theme, setThemeState] = useState<Theme>(readTheme());
  const [sid, setSid] = useState<string | null>(initialSid);
  const [openErr, setOpenErr] = useState<string | null>(null);
  const lab = useLab(sid, theme);
  const [active, setActive] = useState("idea");

  useEffect(() => applyTheme(theme), [theme]);

  // Reached without a session: borrow the preloaded one, or open the first
  // dataset. The lab never writes to it.
  useEffect(() => {
    if (sid) return;
    api.boot()
      .then(async (b) => b.session ?? (await api.open(0, { filters: {}, start: null, end: null })).session)
      .then((s) => {
        setSid(s);
        window.history.replaceState(null, "", `#/guide/${s}`);
      })
      .catch((e: unknown) => setOpenErr(String(e instanceof Error ? e.message : e)));
  }, [sid]);

  // The section in view is the last one whose top has passed the header.
  useEffect(() => {
    const scroller = document.getElementById("guide-scroll");
    if (!scroller) return;
    const onScroll = () => {
      let current = TOC[0].id;
      for (const t of TOC) {
        const el = document.getElementById(t.id);
        if (el && el.getBoundingClientRect().top < 140) current = t.id;
      }
      // The last section is too short to reach the top; at the bottom, it is the one in view.
      if (scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 4) current = TOC[TOC.length - 1].id;
      setActive(current);
    };
    onScroll();
    scroller.addEventListener("scroll", onScroll, { passive: true });
    return () => scroller.removeEventListener("scroll", onScroll);
  }, [lab.ready]);

  const scrollTo = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });

  return (
    <div className="h-full overflow-y-auto bg-paper" id="guide-scroll">
      <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-line bg-surface/85 px-4 backdrop-blur-xl">
        <IconButton onClick={() => onBack(initialSid)}>
          <ArrowLeft className="size-4" /> {initialSid ? "Back to the workspace" : "Back"}
        </IconButton>
        <span className="h-5 w-px bg-line" />
        <Logo size={22} />
        <span className="text-[13.5px] font-semibold tracking-[-0.01em] text-ink">How Explain Beaucoup works</span>
        <span className="hidden text-[12px] text-muted md:inline">a live tour for developers</span>
        <div className="ml-auto">
          <IconButton onClick={() => setThemeState(theme === "dark" ? "light" : "dark")}>
            {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
          </IconButton>
        </div>
      </header>

      <div className="mx-auto flex max-w-[1320px] gap-10 px-6">
        <nav className="sticky top-14 hidden h-[calc(100vh-56px)] w-48 shrink-0 py-14 xl:block">
          <SectionLabel>On this page</SectionLabel>
          <ol className="mt-3 space-y-0.5 border-l border-line">
            {TOC.map((t, i) => (
              <li key={t.id}>
                <button
                  onClick={() => scrollTo(t.id)}
                  className={cn(
                    "-ml-px block border-l py-1 pl-3 text-left text-[12.5px] transition-colors",
                    active === t.id ? "border-accent font-medium text-ink" : "border-transparent text-muted hover:text-ink-2",
                  )}
                >
                  <span className="mr-1.5 font-mono text-[10.5px] text-muted">{String(i).padStart(2, "0")}</span>
                  {t.title}
                </button>
              </li>
            ))}
          </ol>
        </nav>

        <main className="min-w-0 flex-1 pb-24">
          <Hero onStart={() => scrollTo("lab")} />
          {openErr && <Card className="p-4 text-[12.5px] text-critical">Could not open a dataset for the lab: {openErr}</Card>}
          {lab.error && <Card className="p-4 text-[12.5px] text-critical">{lab.error}</Card>}
          {!lab.ready && !openErr && !lab.error && <div className="py-20 text-center text-[12.5px] text-muted">Starting the lab against the real engine…</div>}
          {lab.ready && sid && (
            <>
              <Section id="context" n={1} title="Anatomy of a Context" lede={<>
                Every view in the workspace is the reading of one of these. It is a frozen dataclass in <Mono>core/context.py</Mono>, and it is the only thing the layers pass to each other.
                This is the dataset's real landing view. Hover a field to see what it means.
              </>}>
                <Anatomy lab={lab} />
              </Section>

              <Section id="lab" n={2} title="One move, every stage" lede={<>
                Pick a move. The page sends it through the same code the workspace uses and shows what each stage received and returned.
                Each move produces a <em>new</em> Context on the left, highlighting the fields that changed. Try a refused move as well: the
                trace stops at the stage that refused it, before anything is computed.
              </>}>
                <Lab lab={lab} />
              </Section>

              <Section id="identity" n={3} title="Same question, same id" lede={<>
                A Context's id is a hash of its analytical state, <strong>not</strong> of how you got there. Two routes that ask the same question
                share one id, so they share one cache entry and are deduplicated in the stack. The lineage remembers the route; the id ignores it.
              </>}>
                <Identity lab={lab} />
              </Section>

              <Section id="model" n={4} title="The model decides what's legal" lede={<>
                Nothing about the dataset is hardcoded. Hierarchies, metrics, grains and comparisons come from a YAML model, and the same
                declarations generate the menu (<Mono>capabilities</Mono>) and the validator (<Mono>validate</Mono>). The UI cannot offer a move the engine would refuse.
              </>}>
                <ModelSection lab={lab} />
              </Section>

              <Section id="explain" n={5} title="Evidence you can open" lede={<>
                “Explain this” runs registered signals over the node's scope and ranks the evidence by weight × magnitude. Each piece of
                evidence carries a Context, so an explanation is a place you can go, not a claim you have to trust. Open one, and the lab moves there.
              </>}>
                <ExplainSection lab={lab} sid={sid} onOpen={() => scrollTo("lab")} />
              </Section>

              <Section id="extend" n={6} title="Extension points" lede={<>
                Every layer that could grow is a registry, listed here live from this server. Adding to one takes a class or a function plus one call, and
                nothing else in the framework changes.
              </>}>
                <Registry lab={lab} />
              </Section>

              <Section id="layers" n={7} title="Layers" lede="Where the pieces above live, and the one direction the imports go.">
                <Layers />
              </Section>
            </>
          )}
        </main>
      </div>
    </div>
  );
}

function Hero({ onStart }: { onStart: () => void }) {
  const [picked, setPicked] = useState<string | null>("apply");
  const stage = STAGES.find((s) => s.id === picked);
  return (
    <section id="idea" className="scroll-mt-20 pt-14 pb-12">
      <div className="font-mono text-[11px] font-medium text-accent">00</div>
      <h1 className="mt-1 max-w-[22ch] text-[34px] leading-[1.1] font-semibold tracking-[-0.03em] text-ink">
        A click doesn't change a chart. It creates a Context.
      </h1>
      <p className="mt-4 max-w-[64ch] text-[14px] leading-relaxed text-ink-2">
        The whole framework is four ideas stacked on top of each other. Hold these and the code reads itself; the rest of this page lets you watch each one run.
      </p>

      <div className="mt-8 grid gap-3 md:grid-cols-4">
        {[
          { t: "Context", c: "a value", d: "The whole analytical question: metric, scope, axes, time, comparison. Immutable, and identified by a hash of its state." },
          { t: "Verb", c: "Context → Context", d: "A typed operation. It returns a new Context, or refuses by name. Verbs are registered, never hardcoded." },
          { t: "Engine", c: "Context → Result", d: "Every number, computed deterministically from one primitive, aggregate(Query), on any backend." },
          { t: "View", c: "Result → spec", d: "A renderer name plus a spec. Front ends draw it and report marks back by key." },
        ].map((x, i) => (
          <div key={x.t} className="relative">
            <Card className="h-full p-4">
              <div className="flex items-baseline justify-between">
                <span className="text-[15px] font-semibold text-ink">{x.t}</span>
                <span className="font-mono text-[10.5px] text-accent">{x.c}</span>
              </div>
              <p className="mt-1.5 text-[12px] leading-relaxed text-ink-2">{x.d}</p>
            </Card>
            {i < 3 && <ArrowRight className="absolute top-1/2 -right-3 z-10 hidden size-4 -translate-y-1/2 rounded-full bg-paper text-muted md:block" />}
          </div>
        ))}
      </div>

      <Code className="mt-5 text-[12px]">{`ctx2 = apply(model, ctx1, Operation.of("drill_down", dimension="province", member="Luzon"))
res  = engine.execute(ctx2, result_kind(model, ctx2, op))   # -> ResultHandle: rows, totals, provenance
spec = spec_for(model, res, theme)                          # -> {"renderer": "vega-lite", ...}`}</Code>

      <Card className="mt-6 p-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <SectionLabel>Every move passes through the same nine stages. Click one.</SectionLabel>
          <button onClick={onStart} className="inline-flex items-center gap-1 text-[12px] font-medium text-accent hover:underline">
            Watch a real move go through them <ArrowRight className="size-3.5" />
          </button>
        </div>
        <Strip active={picked} onPick={setPicked} stages={STAGES.map((s) => ({ stage: s.id, ok: true }))} />
        {stage && (
          <div className="mt-2 grid gap-1 rounded-lg bg-sunken/60 px-3 py-2.5 sm:grid-cols-[220px_1fr]">
            <div>
              <div className="text-[13px] font-semibold text-ink">{stage.name}</div>
              <div className="font-mono text-[11px] text-muted">{stage.module}</div>
            </div>
            <div>
              <Mono>{stage.call}</Mono>
              <p className="mt-1 text-[12.5px] leading-relaxed text-ink-2">{stage.says}</p>
            </div>
          </div>
        )}
      </Card>
    </section>
  );
}

function Anatomy({ lab }: { lab: ReturnType<typeof useLab> }) {
  const [focus, setFocus] = useState<string | null>(null);
  const root = lab.root!;
  const field = FIELDS.find((f) => f.key === focus);
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(320px,420px)_1fr]">
      <Card>
        <ContextCard ctx={root.context} focus={focus} onFocus={setFocus} />
      </Card>
      <div className="space-y-3">
        <Card className="min-h-[120px] p-4">
          {field ? (
            <>
              <div className="font-mono text-[12px] text-accent">{field.name}</div>
              <p className="mt-1 text-[13px] leading-relaxed text-ink">{field.means}</p>
            </>
          ) : focus === "lineage" ? (
            <>
              <div className="font-mono text-[12px] text-accent">lineage</div>
              <p className="mt-1 text-[13px] leading-relaxed text-ink">
                How the node was reached: one <Mono>LineageStep</Mono> per verb. It's kept for provenance and the commentary, but it is
                <strong> excluded from identity</strong>, so two routes to the same question are the same node.
              </p>
            </>
          ) : (
            <p className="text-[13px] leading-relaxed text-muted">Hover a field on the left.</p>
          )}
        </Card>
        <Card className="p-4 text-[12.5px] leading-relaxed text-ink-2">
          <SectionLabel>Three properties do most of the work</SectionLabel>
          <ul className="mt-2 space-y-2">
            <li><strong className="text-ink">Immutable.</strong> <Mono>evolve()</Mono> returns a copy with one more lineage step. Nothing is ever changed in place, so every earlier view is still valid and can be returned to.</li>
            <li><strong className="text-ink">Identified by content.</strong> <Mono>id = sha1(state without lineage)</Mono>. The engine caches on it, and the stack deduplicates on it.</li>
            <li><strong className="text-ink">Plain data.</strong> <Mono>to_dict()</Mono> / <Mono>from_dict()</Mono>. A Context can go to a browser, a file or an LLM agent and come back unchanged. This page depends on that.</li>
          </ul>
        </Card>
        <div className="text-[12px] text-muted">
          It reads as: <span className="font-mono text-ink">{root.context.label}</span> → drawn as <Mono>{root.reading.kind}</Mono>, totalling <span className="text-ink">{root.reading.result.value_txt}</span>.
        </div>
      </div>
    </div>
  );
}
