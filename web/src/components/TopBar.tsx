import * as DM from "@radix-ui/react-dropdown-menu";
import { ArrowLeft, BookOpen, ChevronLeft, ChevronRight, CornerLeftUp, Download, GitBranch, LayoutDashboard, Moon, Pin, PinOff, Search, Sparkles, Sun } from "lucide-react";
import { toast } from "sonner";
import { api } from "../api";
import { useWorkspace } from "../store";
import { IconButton, Kbd, Logo, PillSelect, Tip } from "../ui";

export function TopBar() {
  const { snap, act, theme, setTheme, setPalette, leave, toDashboard, reload } = useWorkspace();
  const t = snap.toolbar;

  // The chart on top becomes a tile: its lineage, replayed from the root.
  const addToDashboard = async () => {
    try {
      const r = await api.addTile(snap.session, snap.current);
      await reload();
      toast(`Added “${r.title}” to the dashboard`, {
        description: `${r.tiles} ${r.tiles === 1 ? "tile" : "tiles"} · saved to ${r.file}`,
      });
    } catch (e) {
      toast.error("Could not add it to the dashboard", { description: String((e as Error).message) });
    }
  };

  const exportReport = async () => {
    const text = await api.exportReport(snap.session);
    const url = URL.createObjectURL(new Blob([text], { type: "text/markdown" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: "investigation.md" });
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <header className="relative z-20 flex h-14 shrink-0 items-center gap-3 border-b border-line bg-surface/85 px-3 backdrop-blur-xl">
      <div className="flex min-w-0 items-center gap-2.5">
        <Tip label="Choose another dataset">
          <button onClick={leave} className="rounded-lg p-1 transition-transform hover:scale-105 active:scale-95">
            <Logo size={26} />
          </button>
        </Tip>
        {toDashboard && (
          <Tip label="Back to the dashboard - this investigation stays open on its tile">
            <button
              onClick={toDashboard}
              className="inline-flex h-8 shrink-0 items-center gap-1 rounded-lg border border-line bg-raised pr-2 pl-1.5 text-[12px] font-medium text-ink-2 shadow-sm transition-colors hover:border-line-strong hover:text-ink 2xl:pr-2.5"
            >
              <ChevronLeft className="size-3.5" />
              <LayoutDashboard className="size-3.5 text-accent" />
              <span className="hidden 2xl:inline">Dashboard</span>
            </button>
          </Tip>
        )}
        <div className="min-w-0 leading-tight">
          <div className="truncate text-[13.5px] font-semibold tracking-[-0.01em] text-ink">
            {snap.board.tile_title ?? snap.title}
          </div>
          <div className="truncate text-[11px] text-muted">
            {snap.board.tile_title && "Dashboard tile · "}
            {snap.scope_label ? (
              <span className="inline-flex items-center gap-1">
                <span className="size-1.5 rounded-full bg-offset" />
                Scoped · {snap.scope_label}
              </span>
            ) : (
              "Whole table"
            )}
          </div>
        </div>
      </div>

      <div className="mx-auto flex min-w-0 items-center rounded-xl border border-line bg-raised p-0.5 shadow-sm">
        <PillSelect label="Metric" value={t.metric} options={t.metrics} onChange={(v) => act({ type: "metric", value: v })} />
        <Divider />
        <PillSelect label="By" value={t.grain} options={t.grains} onChange={(v) => act({ type: "time_grain", value: v })} />
        <Divider />
        <PillSelect label="Period" value={t.period} options={t.periods} onChange={(v) => act({ type: "period", value: v })} />
        <Divider />
        <PillSelect label="vs" value={t.comparison} options={t.comparisons} onChange={(v) => act({ type: "comparison", value: v })} />
        <Divider />
        <PillSelect
          label="Lens"
          value={t.lens ?? "Composition"}
          options={t.lenses}
          onChange={(v) => act({ type: "lens", value: v })}
        />
      </div>

      <div className="flex shrink-0 items-center gap-0.5">
        <Tip label="Drill up" keys="U">
          <IconButton disabled={!t.can_up} onClick={() => act({ type: "run", verb: "drill_up" })}>
            <CornerLeftUp className="size-4" />
          </IconButton>
        </Tip>
        <Tip label="Back to the parent chart" keys="[">
          <IconButton disabled={!t.can_back} onClick={() => act({ type: "back" })}>
            <ArrowLeft className="size-4" />
          </IconButton>
        </Tip>
        <DM.Root>
          <Tip label="Branch into a competing hypothesis" keys="B">
            <DM.Trigger asChild>
              <IconButton disabled={!t.branch_options.length}>
                <GitBranch className="size-4" />
              </IconButton>
            </DM.Trigger>
          </Tip>
          <DM.Portal>
            <DM.Content align="end" sideOffset={6} className="pop z-50 min-w-56 rounded-xl border border-line bg-raised p-1 shadow-lg">
              <DM.Label className="px-2.5 pt-1.5 pb-1 text-[11px] text-muted">Break the focus down by…</DM.Label>
              {t.branch_options.map((o) => (
                <DM.Item
                  key={o.value}
                  onSelect={() => act({ type: "branch", value: o.value })}
                  className="flex cursor-default items-center justify-between rounded-lg px-2.5 py-1.5 text-[12.5px] outline-none data-[highlighted]:bg-sunken"
                >
                  {o.label}
                  <ChevronRight className="size-3.5 text-muted" />
                </DM.Item>
              ))}
            </DM.Content>
          </DM.Portal>
        </DM.Root>
        <Tip label={t.pinned ? "Unpin this chart" : "Pin for comparison and the report"} keys="P">
          <IconButton active={t.pinned} onClick={() => act({ type: "pin" })}>
            {t.pinned ? <PinOff className="size-4" /> : <Pin className="size-4" />}
          </IconButton>
        </Tip>
        <Tip label="Ask the grammar" keys="⌘K">
          <IconButton className="2xl:hidden" onClick={() => setPalette(true)}>
            <Search className="size-4" />
          </IconButton>
        </Tip>
        <Tip label="Add this chart to the dashboard">
          <IconButton onClick={addToDashboard}>
            <LayoutDashboard className="size-4" />
          </IconButton>
        </Tip>
        <Tip label="Export the investigation as markdown">
          <IconButton onClick={exportReport}>
            <Download className="size-4" />
          </IconButton>
        </Tip>
        <Tip label="How the framework works - a live tour for developers">
          <IconButton onClick={() => (window.location.hash = `/guide/${snap.session}`)}>
            <BookOpen className="size-4" />
          </IconButton>
        </Tip>
        <Tip label={theme === "dark" ? "Light mode" : "Dark mode"} keys="D">
          <IconButton onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
            {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
          </IconButton>
        </Tip>
        <span className="mx-1.5 h-5 w-px bg-line" />
        <button
          onClick={() => setPalette(true)}
          className="hidden h-8 items-center gap-2 rounded-lg border border-line bg-raised pr-1.5 pl-2.5 text-[12px] text-muted shadow-sm transition-colors hover:border-line-strong hover:text-ink-2 2xl:inline-flex"
        >
          <Search className="size-3.5" />
          Ask the grammar…
          <Kbd>⌘K</Kbd>
        </button>
        <Tip label="Explain this view" keys="E">
          <IconButton tone="accent" className="ml-1.5 px-3" onClick={() => act({ type: "explain", member: snap.selected })}>
            <Sparkles className="size-4" />
            Explain
          </IconButton>
        </Tip>
      </div>
    </header>
  );
}

function Divider() {
  return <span className="h-4 w-px bg-line" />;
}
