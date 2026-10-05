import * as DM from "@radix-ui/react-dropdown-menu";
import { motion } from "motion/react";
import { ArrowRight, CircleAlert, Compass, LayoutDashboard, Moon, MoreHorizontal, Pin, Sparkles, Sun, Trash2, TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { api } from "../api";
import type { Board, BoardTile, Theme } from "../api";
import { Chart } from "../charts/registry";
import { applyTheme, readTheme } from "../store";
import { cn, Empty, IconButton, Logo, Tip } from "../ui";

/** Where a dig lands: a session, and the mark to open its menu on. */
export type Dug = { session: string; uid: string; key: string | null };

const ROLE_DOT: Record<string, string> = {
  pos: "bg-pos",
  neg: "bg-neg",
  offset: "bg-offset",
  link: "bg-link",
  muted: "bg-muted",
};

/** The canned dashboard a dataset opens on. Read-mostly: every tile is a
 *  saved starting point, and the way in is to dig - into the tile, or
 *  straight into one of its marks. */
export function Dashboard({ bid, onDig, onLeave }: {
  bid: string;
  onDig: (d: Dug) => void;
  onLeave: () => void;
}) {
  const [board, setBoard] = useState<Board | null>(null);
  const [theme, setThemeState] = useState<Theme>(readTheme);
  const [digging, setDigging] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api
      .board(bid, theme)
      .then((b) => live && setBoard(b))
      .catch((e: Error) => {
        toast.error("Could not open the dashboard", { description: e.message });
        onLeave();
      });
    return () => {
      live = false;
    };
  }, [bid, theme, onLeave]);

  const setTheme = (t: Theme) => {
    applyTheme(t);
    setThemeState(t);
  };

  const dig = useCallback(
    async (tile: string, key: string | null) => {
      if (digging) return;
      setDigging(tile);
      try {
        onDig(await api.investigate(bid, tile, key));
      } catch (e) {
        toast.error("Could not open that tile", { description: String((e as Error).message) });
        setDigging(null);
      }
    },
    [bid, digging, onDig],
  );

  const explore = async () => {
    const { session } = await api.explore(bid);
    onDig({ session, uid: "", key: null });
  };

  const remove = async (t: BoardTile) => {
    try {
      setBoard(await api.removeTile(bid, t.id, theme));
      toast(`Removed “${t.title}”`, { description: `from ${board?.file}` });
    } catch (e) {
      toast.error("Could not remove the tile", { description: String((e as Error).message) });
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className={`relative h-0.5 shrink-0 overflow-hidden ${!board || digging ? "busy-bar" : ""}`} />
      <header className="relative z-20 flex h-14 shrink-0 items-center gap-3 border-b border-line bg-surface/85 px-3 backdrop-blur-xl">
        <Tip label="Choose another dataset">
          <button onClick={onLeave} className="rounded-lg p-1 transition-transform hover:scale-105 active:scale-95">
            <Logo size={26} />
          </button>
        </Tip>
        <div className="min-w-0 leading-tight">
          <div className="flex items-center gap-1.5 truncate text-[13.5px] font-semibold tracking-[-0.01em] text-ink">
            <LayoutDashboard className="size-3.5 text-accent" />
            {board?.title ?? "Dashboard"}
          </div>
          <div className="truncate text-[11px] text-muted">
            {board && (
              <>
                {board.dataset} · {board.period}
                {board.scope_label ? (
                  <span className="ml-1.5 inline-flex items-center gap-1">
                    <span className="size-1.5 rounded-full bg-offset" />
                    Scoped · {board.scope_label}
                  </span>
                ) : (
                  " · whole table"
                )}
              </>
            )}
          </div>
        </div>
        <div className="ml-auto flex items-center gap-1">
          <Tip label={theme === "dark" ? "Light mode" : "Dark mode"}>
            <IconButton onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
              {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
            </IconButton>
          </Tip>
          <Tip label={board?.free ? "Back to your free exploration" : "Open the workspace on the model's landing view"}>
            <IconButton className="px-3" onClick={explore} disabled={!board}>
              <Compass className="size-4" />
              {board?.free ? "Resume exploring" : "Explore freely"}
            </IconButton>
          </Tip>
        </div>
      </header>

      <main className="min-h-0 flex-1 overflow-y-auto bg-paper">
        <div className="mx-auto max-w-[1240px] px-6 pt-6 pb-16">
          {board?.error && (
            <div className="mb-4 flex items-start gap-2 rounded-xl border border-critical/30 bg-critical/5 px-3.5 py-2.5 text-[12.5px] text-critical">
              <CircleAlert className="mt-0.5 size-4 shrink-0" />
              {board.error}
            </div>
          )}
          {!board ? (
            <Skeleton />
          ) : board.tiles.length === 0 ? (
            <Empty title="This dashboard has no tiles yet" icon={<LayoutDashboard className="size-5" />}>
              Explore the data, and use <b>Add to dashboard</b> on any chart worth watching. It is saved to{" "}
              <code className="font-mono text-[11px]">{board.file}</code> beside the model.
            </Empty>
          ) : (
            // Headline numbers read as one strip; charts get the room below.
            [board.tiles.filter((t) => t.view === "kpi"), board.tiles.filter((t) => t.view !== "kpi")].map(
              (group, g) =>
                group.length > 0 && (
                  <div
                    key={g}
                    className={cn(
                      "grid gap-4",
                      g === 0
                        ? "mb-4 grid-cols-[repeat(auto-fill,minmax(min(100%,270px),1fr))]"
                        : "grid-cols-[repeat(auto-fill,minmax(min(100%,360px),1fr))]",
                    )}
                  >
                    {group.map((t) => (
                      <TileCard
                        key={t.id}
                        tile={t}
                        index={board.tiles.indexOf(t)}
                        digging={digging === t.id}
                        onDig={(key) => void dig(t.id, key)}
                        onRemove={() => void remove(t)}
                      />
                    ))}
                  </div>
                ),
            )
          )}
        </div>
      </main>
    </div>
  );
}

function TileCard({ tile, index, digging, onDig, onRemove }: {
  tile: BoardTile;
  index: number;
  digging: boolean;
  onDig: (key: string | null) => void;
  onRemove: () => void;
}) {
  const kpi = tile.view === "kpi";
  const inv = tile.investigation;
  const h = tile.headline;
  return (
    <motion.section
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: Math.min(index, 8) * 0.035, duration: 0.25 }}
      className={cn(
        "group/tile flex min-w-0 flex-col rounded-2xl border border-line bg-surface p-4 pb-0 shadow-sm transition-[border-color,box-shadow] hover:border-line-strong hover:shadow-md",
        tile.size === "wide" && "md:col-span-2",
      )}
    >
      <header className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-[13.5px] font-semibold tracking-[-0.01em] text-ink">{tile.title}</h2>
          {tile.subtitle && <p className="mt-0.5 truncate text-[11.5px] text-muted">{tile.subtitle}</p>}
        </div>
        {!!tile.exceptions && (
          <Tip label={`${tile.exceptions} ${tile.exceptions === 1 ? "member is" : "members are"} unusual against their own history`}>
            <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-offset/10 px-2 py-0.5 text-[10.5px] font-semibold text-offset">
              <TriangleAlert className="size-3" />
              {tile.exceptions} unusual
            </span>
          </Tip>
        )}
        <DM.Root>
          <DM.Trigger asChild>
            <button className="-mr-1 grid size-6 shrink-0 place-items-center rounded-md text-muted opacity-0 transition-opacity group-hover/tile:opacity-100 hover:bg-sunken hover:text-ink data-[state=open]:opacity-100">
              <MoreHorizontal className="size-4" />
            </button>
          </DM.Trigger>
          <DM.Portal>
            <DM.Content align="end" sideOffset={4} className="pop z-50 min-w-48 rounded-xl border border-line bg-raised p-1 shadow-lg">
              <DM.Label className="px-2.5 pt-1.5 pb-1 font-mono text-[10.5px] text-muted">{tile.steps.join(" → ") || "root"}</DM.Label>
              <DM.Item
                onSelect={onRemove}
                className="flex cursor-default items-center gap-2 rounded-lg px-2.5 py-1.5 text-[12.5px] text-critical outline-none data-[highlighted]:bg-sunken"
              >
                <Trash2 className="size-3.5" />
                Remove from dashboard
              </DM.Item>
            </DM.Content>
          </DM.Portal>
        </DM.Root>
      </header>

      {tile.error ? (
        <div className="my-4 flex flex-1 items-start gap-2 rounded-xl bg-sunken px-3 py-2.5 text-[12px] text-ink-2">
          <CircleAlert className="mt-0.5 size-3.5 shrink-0 text-critical" />
          <span>This tile does not open under this scope: {tile.error}</span>
        </div>
      ) : (
        <>
          {h && (
            <div className={cn("flex flex-wrap items-baseline gap-x-2.5 gap-y-1", kpi ? "mt-4" : "mt-2.5")}>
              <span className={cn("tabular font-semibold tracking-[-0.025em] text-ink", kpi ? "text-[34px] leading-none" : "text-[20px]")}>
                {h.value}
              </span>
              {h.delta && (
                <span
                  className={cn(
                    "tabular inline-flex items-center rounded-md px-1.5 py-0.5 text-[11.5px] font-semibold",
                    h.delta.tone === "good" ? "bg-good/10 text-good" : "bg-critical/10 text-critical",
                  )}
                >
                  {h.delta.up ? "▲" : "▼"} {h.delta.pct !== null ? `${(h.delta.pct * 100).toFixed(1)}%` : h.delta.text}
                </span>
              )}
              <span className="text-[11px] text-muted">
                {h.period}
                {h.delta && ` vs ${h.delta.comparison}`}
              </span>
            </div>
          )}

          {tile.spec && tile.renderer && (
            <div className="mt-2 cursor-pointer">
              <Chart
                renderer={tile.renderer}
                spec={tile.spec}
                compact
                handlers={{
                  // Any mark is a way in: the tile's investigation opens on it,
                  // with the governed menu for that mark already up.
                  onSelect: (key) => onDig(key),
                  onActivate: (key) => onDig(key),
                  onMenu: (key) => onDig(key),
                }}
              />
            </div>
          )}

          {tile.why && (
            <div className={cn("flex items-start gap-2 text-[12px] leading-snug text-ink-2", kpi ? "mt-4" : "mt-2")}>
              <Sparkles className="mt-0.5 size-3.5 shrink-0 text-accent" />
              <span className="min-w-0">
                <span className="mr-1.5 inline-flex items-center gap-1 text-[10.5px] font-semibold uppercase tracking-[0.06em] text-muted">
                  <span className={cn("size-1.5 rounded-full", ROLE_DOT[tile.why.role] ?? "bg-muted")} />
                  {tile.why.label}
                </span>
                {tile.why.headline}
              </span>
            </div>
          )}
          <div className="flex-1" />
        </>
      )}

      {inv && inv.pins.length > 0 && (
        <ul className="mt-3 space-y-1 rounded-xl bg-sunken/70 px-3 py-2">
          {inv.pins.slice(0, 3).map((p) => (
            <li key={p.uid} className="flex items-center gap-1.5 text-[11.5px] text-ink-2">
              <Pin className="size-3 shrink-0 text-accent" />
              <span className="truncate">{p.title}</span>
              <span className="tabular ml-auto shrink-0 font-semibold text-ink">{p.value}</span>
            </li>
          ))}
          {inv.pins.length > 3 && <li className="text-[11px] text-muted">+{inv.pins.length - 3} more findings</li>}
        </ul>
      )}

      <footer className="mt-3 -mx-4 flex items-center gap-2 border-t border-line px-4 py-2">
        <span className="min-w-0 flex-1 truncate text-[11px] text-muted">
          {inv
            ? `${inv.views} ${inv.views === 1 ? "view" : "views"} · ${inv.pins.length} ${inv.pins.length === 1 ? "finding" : "findings"}${inv.at ? ` · at ${inv.at}` : ""}`
            : tile.error
              ? ""
              : "Not investigated yet"}
        </span>
        {!tile.error && (
          <button
            onClick={() => onDig(null)}
            disabled={digging}
            className="inline-flex h-7 shrink-0 items-center gap-1 rounded-lg bg-accent-soft px-2.5 text-[12px] font-medium text-accent transition-colors hover:bg-accent hover:text-accent-ink disabled:opacity-50"
          >
            {inv ? "Resume" : "Dig deeper"}
            <ArrowRight className="size-3.5" />
          </button>
        )}
      </footer>
    </motion.section>
  );
}

function Skeleton() {
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,360px),1fr))] gap-4">
      {Array.from({ length: 6 }, (_, i) => (
        <div key={i} className="h-64 animate-pulse rounded-2xl border border-line bg-surface" />
      ))}
    </div>
  );
}
