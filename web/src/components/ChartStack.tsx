import { AnimatePresence, LayoutGroup, motion } from "motion/react";
import { ArrowUp, ArrowUpRight, Pin, PinOff, Sparkles, X } from "lucide-react";
import { useEffect, useRef } from "react";
import type { Panel } from "../api";
import { useWorkspace } from "../store";
import { cn, Tip } from "../ui";
import { VegaChart } from "./VegaChart";

const spring = { type: "spring", stiffness: 380, damping: 36, mass: 0.9 } as const;

export function ChartStack() {
  const { snap } = useWorkspace();
  const { panels, scroll_to } = snap.stack;
  const focus = panels[0];
  const kids = panels.slice(1);
  const refs = useRef(new Map<string, HTMLElement>());

  useEffect(() => {
    if (!scroll_to) return;
    const t = window.setTimeout(() => {
      refs.current.get(scroll_to)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }, 180);
    return () => window.clearTimeout(t);
  }, [scroll_to, snap]);

  const register = (uid: string) => (el: HTMLElement | null) => {
    if (el) refs.current.set(uid, el);
    else refs.current.delete(uid);
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-[1180px] px-6 pt-5 pb-16">
        <LayoutGroup>
          {focus && <ChartCard key={focus.uid} panel={focus} innerRef={register(focus.uid)} />}

          <AnimatePresence>
            {kids.length > 0 && (
              <motion.div
                key="kids-head"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="mt-7 mb-3 flex items-baseline gap-3 px-1"
              >
                <span className="text-[10.5px] font-semibold uppercase tracking-[0.09em] text-muted">
                  Drilled from above
                </span>
                <span className="text-[11.5px] text-muted">
                  {kids.length} {kids.length === 1 ? "view" : "views"} · raise one to drill further
                </span>
                <span className="h-px flex-1 translate-y-[-3px] bg-line" />
              </motion.div>
            )}
          </AnimatePresence>

          <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,400px),1fr))] items-start gap-4">
            <AnimatePresence mode="popLayout">
              {kids.map((p) => (
                <ChartCard key={p.uid} panel={p} innerRef={register(p.uid)} />
              ))}
            </AnimatePresence>
          </div>
        </LayoutGroup>
      </div>
    </div>
  );
}

function ChartCard({ panel, innerRef }: { panel: Panel; innerRef: (el: HTMLElement | null) => void }) {
  const { act, openMenu, snap } = useWorkspace();
  const focus = panel.role === "focus";
  const uid = panel.uid;
  const scrolled = snap.stack.scroll_to === uid && !focus;

  const onAction = (action: string) => {
    if (action === "raise") void act({ type: "raise", uid });
    else if (action === "close") void act({ type: "close", uid });
    else if (action === "up") void act({ type: "back" });
    else if (action === "explain") void act({ type: "explain", uid, member: snap.selected });
    else if (action === "pin") void act({ type: "pin", uid });
  };

  return (
    <motion.section
      ref={innerRef}
      layout="position"
      layoutId={uid}
      initial={{ opacity: 0, y: 14, scale: 0.985 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, scale: 0.97, transition: { duration: 0.16 } }}
      transition={spring}
      onContextMenu={(e) => {
        e.preventDefault();
        void openMenu(uid, null, e.clientX, e.clientY);
      }}
      className={cn(
        "group/card relative rounded-2xl border bg-surface transition-[border-color,box-shadow] duration-300",
        focus ? "border-line p-5 pb-4 shadow-md" : "border-line p-3.5 pb-2.5 shadow-sm hover:shadow-md hover:border-line-strong",
        scrolled && "border-accent/50 ring-4 ring-accent/10",
      )}
    >
      <header className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <h2
            className={cn(
              "truncate font-semibold tracking-[-0.015em] text-ink",
              focus ? "text-[17px]" : "text-[13px]",
            )}
          >
            {panel.title}
          </h2>
          {panel.subtitle && (
            <p className={cn("mt-0.5 truncate text-muted", focus ? "text-[12.5px]" : "text-[11.5px]")}>
              {panel.subtitle}
            </p>
          )}
        </div>
        {panel.badge && (
          <span className="mt-0.5 shrink-0 rounded-full bg-sunken px-2 py-0.5 text-[10.5px] font-medium text-muted">
            {panel.badge}
          </span>
        )}
        <div
          className={cn(
            "flex shrink-0 items-center gap-1",
            !focus && "opacity-60 transition-opacity group-hover/card:opacity-100",
          )}
        >
          {panel.actions.map((a) => (
            <PanelButton key={a.action} action={a.action} label={a.label} hint={a.hint} primary={a.primary} focus={focus} onClick={() => onAction(a.action)} />
          ))}
        </div>
      </header>
      <div className={focus ? "mt-3" : "mt-1.5"}>
        <VegaChart
          spec={panel.spec}
          compact={!focus}
          handlers={{
            onSelect: (key) => void act({ type: "select", uid, key }),
            onActivate: (key) => void act({ type: "activate", uid, key }),
            onMenu: (key, x, y) => void openMenu(uid, key, x, y),
          }}
        />
      </div>
    </motion.section>
  );
}

function PanelButton({ action, label, hint, primary, focus, onClick }: {
  action: string;
  label: string;
  hint?: string;
  primary?: boolean;
  focus: boolean;
  onClick: () => void;
}) {
  const icon = {
    raise: <ArrowUpRight className="size-3.5" />,
    close: <X className="size-3.5" />,
    up: <ArrowUp className="size-3.5" />,
    explain: <Sparkles className="size-3.5" />,
    pin: label === "Unpin" ? <PinOff className="size-3.5" /> : <Pin className="size-3.5" />,
  }[action];
  const text = action === "close" ? null : action === "up" ? "Parent" : label;
  const btn = (
    <button
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      className={cn(
        "inline-flex items-center gap-1 rounded-lg font-medium transition-colors",
        focus ? "h-7 px-2.5 text-[12px]" : "h-6 px-2 text-[11.5px]",
        action === "close" && "w-6 justify-center px-0",
        primary
          ? "bg-accent-soft text-accent hover:bg-accent hover:text-accent-ink"
          : action === "explain"
            ? "bg-ink text-paper hover:opacity-85"
            : "text-ink-2 hover:bg-sunken hover:text-ink",
      )}
    >
      {icon}
      {text}
    </button>
  );
  return hint ? <Tip label={hint}>{btn}</Tip> : btn;
}
