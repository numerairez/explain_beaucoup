import { ChevronRight, GitBranch } from "lucide-react";
import { useWorkspace } from "../store";
import { cn, Tip } from "../ui";

/** The path through the investigation, always visible. */
export function Crumbs() {
  const { snap, act } = useWorkspace();
  const { path, siblings } = snap.crumbs;
  return (
    <nav className="flex h-11 shrink-0 items-center gap-0.5 overflow-x-auto border-b border-line px-5 whitespace-nowrap">
      {path.map((c, i) => {
        const last = i === path.length - 1;
        return (
          <span key={c.uid} className="flex items-center gap-0.5">
            {i > 0 && <ChevronRight className="size-3.5 shrink-0 text-muted/70" />}
            <Tip label={c.title}>
              <button
                onClick={() => !last && act({ type: "goto", uid: c.uid })}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-md px-1.5 py-1 text-[12.5px] transition-colors",
                  last ? "font-semibold text-ink" : "text-ink-2 hover:bg-sunken hover:text-ink",
                )}
              >
                {c.pinned && <span className="size-1.5 rounded-full bg-accent" />}
                {c.text}
              </button>
            </Tip>
          </span>
        );
      })}
      {siblings.length > 0 && (
        <span className="ml-3 flex items-center gap-1.5 border-l border-line pl-3">
          <GitBranch className="size-3.5 text-muted" />
          {siblings.map((s) => (
            <Tip key={s.uid} label={s.title}>
              <button
                onClick={() => act({ type: "goto", uid: s.uid })}
                className="rounded-full border border-dashed border-line-strong px-2.5 py-0.5 text-[11.5px] text-ink-2 transition-colors hover:border-accent hover:text-accent"
              >
                {s.text}
              </button>
            </Tip>
          ))}
        </span>
      )}
    </nav>
  );
}

export function StatusBar() {
  const { snap, hint } = useWorkspace();
  return (
    <footer className="flex h-7 shrink-0 items-center gap-3 border-t border-line bg-surface px-3 text-[11px] text-muted">
      <span className="truncate font-mono text-[10.5px] tabular">{hint || snap.status}</span>
      <span className="ml-auto hidden shrink-0 items-center gap-3 md:flex">
        <span>Click to select</span>
        <span className="text-line-strong">·</span>
        <span>Right-click for the grammar</span>
        <span className="text-line-strong">·</span>
        <span>Double-click to drill</span>
      </span>
    </footer>
  );
}
