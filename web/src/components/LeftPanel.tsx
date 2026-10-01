import * as Tabs from "@radix-ui/react-tabs";
import { Network, Pin } from "lucide-react";
import type { TreeItem } from "../api";
import { useWorkspace } from "../store";
import { cn, Empty } from "../ui";

// Branches take the categorical palette in its fixed order (view/theme.py).
const BRANCH = ["var(--accent)", "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#4a3aa7", "#e34948"];

export function TabList({ tabs }: { tabs: { value: string; label: string; count?: number }[] }) {
  return (
    <Tabs.List className="flex h-11 shrink-0 items-end gap-4 border-b border-line px-4">
      {tabs.map((t) => (
        <Tabs.Trigger
          key={t.value}
          value={t.value}
          className="relative -mb-px flex h-full items-center gap-1.5 border-b-2 border-transparent text-[12.5px] font-medium text-muted transition-colors outline-none hover:text-ink-2 data-[state=active]:border-ink data-[state=active]:text-ink"
        >
          {t.label}
          {t.count ? (
            <span className="rounded-full bg-sunken px-1.5 text-[10.5px] text-muted tabular">{t.count}</span>
          ) : null}
        </Tabs.Trigger>
      ))}
    </Tabs.List>
  );
}

export function LeftPanel() {
  const { snap } = useWorkspace();
  const count = (items: TreeItem[]): number => items.reduce((n, i) => n + 1 + count(i.children), 0);
  return (
    <Tabs.Root defaultValue="map" className="flex h-full flex-col bg-surface">
      <TabList
        tabs={[
          { value: "map", label: "Map", count: count(snap.tree) },
          { value: "pins", label: "Pins", count: snap.pins.length },
        ]}
      />
      <Tabs.Content value="map" className="min-h-0 flex-1 overflow-y-auto px-2 py-3 outline-none">
        {snap.tree.map((item) => (
          <Node key={item.uid} item={item} depth={0} />
        ))}
      </Tabs.Content>
      <Tabs.Content value="pins" className="min-h-0 flex-1 overflow-y-auto p-3 outline-none">
        <Pins />
      </Tabs.Content>
    </Tabs.Root>
  );
}

function Node({ item, depth }: { item: TreeItem; depth: number }) {
  const { act } = useWorkspace();
  const color = BRANCH[item.branch % BRANCH.length];
  return (
    <div className="relative">
      {depth > 0 && (
        <span className="absolute top-0 bottom-0 w-px bg-line" style={{ left: depth * 14 - 4 }} />
      )}
      <button
        title={item.title}
        onClick={() => !item.current && act({ type: "goto", uid: item.uid })}
        className={cn(
          "group relative flex w-full items-center gap-2 rounded-lg py-1.5 pr-2 text-left transition-colors",
          item.current ? "bg-accent-soft" : "hover:bg-sunken",
        )}
        style={{ paddingLeft: depth * 14 + 8 }}
      >
        <span
          className={cn("size-2 shrink-0 rounded-full ring-2 ring-surface", item.current && "scale-125")}
          style={{ background: item.branch || depth === 0 ? color : "var(--line-strong)" }}
        />
        <span className={cn("min-w-0 flex-1 truncate text-[12.5px]", item.current ? "font-semibold text-ink" : "text-ink-2")}>
          {item.text}
        </span>
        {item.pinned && <Pin className="size-3 shrink-0 text-accent" />}
        <span className="shrink-0 text-[10.5px] text-muted opacity-0 transition-opacity group-hover:opacity-100">
          {item.verb}
        </span>
      </button>
      {item.children.map((c) => (
        <Node key={c.uid} item={c} depth={depth + 1} />
      ))}
    </div>
  );
}

function Pins() {
  const { snap, act } = useWorkspace();
  if (!snap.pins.length) {
    return (
      <Empty title="No pins yet" icon={<Pin className="size-4" />}>
        Pin a chart to keep a finding for comparison and for the report.
      </Empty>
    );
  }
  return (
    <div className="space-y-2">
      {snap.pins.map((p) => (
        <button
          key={p.uid}
          onClick={() => act({ type: "goto", uid: p.uid })}
          className="w-full rounded-xl border border-line bg-raised p-3 text-left shadow-sm transition-all hover:-translate-y-px hover:shadow-md"
        >
          <div className="flex items-center gap-1.5 text-[11px] text-muted">
            <Network className="size-3" />
            <span className="truncate">{p.title}</span>
          </div>
          <div className="mt-1 flex items-baseline gap-2">
            <span className="text-[18px] font-semibold tracking-tight text-ink tabular">{p.value}</span>
            <span className="text-[11.5px] text-muted">{p.metric}</span>
          </div>
        </button>
      ))}
    </div>
  );
}
