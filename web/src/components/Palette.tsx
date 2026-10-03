import * as Dialog from "@radix-ui/react-dialog";
import { Command } from "cmdk";
import { Compass, Database, GitBranch, Layers, Moon, Search, Sparkles, Sun } from "lucide-react";
import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { api } from "../api";
import type { Menu, Move, TreeItem } from "../api";
import { useWorkspace } from "../store";
import { Kbd, LandsMark } from "../ui";

const itemCls =
  "flex cursor-default items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] text-ink outline-none select-none data-[selected=true]:bg-sunken";
const groupCls =
  "[&_[cmdk-group-heading]]:px-2.5 [&_[cmdk-group-heading]]:pt-3 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:text-[10.5px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-[0.08em] [&_[cmdk-group-heading]]:text-muted";

/** ⌘K: the same grammar the right-click menu offers, plus everywhere to go. */
export function Palette() {
  const { palette, setPalette, snap, act, theme, setTheme, leave } = useWorkspace();
  const [menu, setMenu] = useState<Menu | null>(null);

  useEffect(() => {
    if (!palette) return;
    setMenu(null);
    api.menu(snap.session, snap.current, snap.selected).then(setMenu).catch(() => setMenu(null));
  }, [palette, snap.session, snap.current, snap.selected]);

  const go = (move: Move) => {
    setPalette(false);
    void act(move);
  };
  const flat = (items: TreeItem[]): TreeItem[] => items.flatMap((i) => [i, ...flat(i.children)]);
  const t = snap.toolbar;

  return (
    <Dialog.Root open={palette} onOpenChange={setPalette}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/25 backdrop-blur-[2px] data-[state=open]:animate-[pop-in_120ms]" />
        <Dialog.Content data-floating className="pop fixed top-[14vh] left-1/2 z-50 w-[min(640px,calc(100vw-32px))] -translate-x-1/2 overflow-hidden rounded-2xl border border-line bg-raised shadow-lg outline-none">
          <Dialog.Title className="sr-only">Command palette</Dialog.Title>
          <Command loop className="flex max-h-[min(560px,70vh)] flex-col">
            <div className="flex items-center gap-2.5 border-b border-line px-4">
              <Search className="size-4 shrink-0 text-muted" />
              <Command.Input
                autoFocus
                placeholder={snap.selected ? `What next for ${snap.selected}?` : "Drill, compare, explain, go to…"}
                className="h-12 flex-1 bg-transparent text-[14px] text-ink outline-none placeholder:text-muted"
              />
              <Kbd>esc</Kbd>
            </div>
            <Command.List className="overflow-y-auto p-1.5">
              <Command.Empty className="py-10 text-center text-[12.5px] text-muted">Nothing in the grammar matches.</Command.Empty>

              {menu && (
                <Command.Group heading={menu.heading} className={groupCls}>
                  {menu.groups.flat().filter((i) => i.action === "run").map((i, n) => (
                    <Command.Item
                      key={n}
                      value={`${i.label} ${i.verb}`}
                      onSelect={() => go({ type: "invoke", uid: menu.uid, item: i })}
                      className={itemCls}
                    >
                      {i.verb === "explain" ? <Sparkles className="size-4 text-accent" /> : <Compass className="size-4 text-muted" />}
                      <span className="flex-1">{i.submenu ? `Switch metric to ${i.label}` : i.label}</span>
                      {i.hint && <span className="max-w-56 truncate text-[11px] text-muted">{i.hint}</span>}
                      <LandsMark lands={i.lands} />
                    </Command.Item>
                  ))}
                </Command.Group>
              )}

              <Command.Group heading="Lens" className={groupCls}>
                {t.lenses.map((l) => (
                  <Command.Item key={l.value} value={`lens ${l.label}`} onSelect={() => go({ type: "lens", value: l.value })} className={itemCls}>
                    <Layers className="size-4 text-muted" />
                    <span className="flex-1">Read as {l.label.toLowerCase()}</span>
                    {t.lens === l.value && <Current />}
                    <LandsMark lands="below" />
                  </Command.Item>
                ))}
              </Command.Group>

              <Command.Group heading="Compare" className={groupCls}>
                {t.comparisons.map((c) => (
                  <Command.Item key={String(c.value)} value={`compare ${c.label} ${c.hint ?? ""}`} onSelect={() => go({ type: "comparison", value: c.value })} className={itemCls}>
                    <Compass className="size-4 text-muted" />
                    <span className="flex-1">{c.value ? `Compare with ${c.hint}` : "No comparison"}</span>
                    {t.comparison === c.value && <Current />}
                    <LandsMark lands="replace" />
                  </Command.Item>
                ))}
              </Command.Group>

              <Command.Group heading="Branch" className={groupCls}>
                {t.branch_options.map((o) => (
                  <Command.Item key={o.value} value={`branch ${o.label}`} onSelect={() => go({ type: "branch", value: o.value })} className={itemCls}>
                    <GitBranch className="size-4 text-muted" />
                    Branch: break down by {o.label}
                  </Command.Item>
                ))}
              </Command.Group>

              <Command.Group heading="Go to chart" className={groupCls}>
                {flat(snap.tree).map((n) => (
                  <Command.Item key={n.uid} value={`go ${n.title} ${n.text} ${n.uid}`} onSelect={() => go({ type: "goto", uid: n.uid })} className={itemCls}>
                    <span className="grid size-4 place-items-center">
                      <span className={`size-1.5 rounded-full ${n.current ? "bg-accent" : "bg-muted"}`} />
                    </span>
                    <span className="flex-1 truncate">{n.title}</span>
                    <span className="text-[11px] text-muted">{n.uid}</span>
                  </Command.Item>
                ))}
              </Command.Group>

              <Command.Group heading="Workspace" className={groupCls}>
                <Action icon={theme === "dark" ? <Sun className="size-4 text-muted" /> : <Moon className="size-4 text-muted" />} label={theme === "dark" ? "Light mode" : "Dark mode"} keys="D" onSelect={() => { setPalette(false); setTheme(theme === "dark" ? "light" : "dark"); }} />
                <Action icon={<Database className="size-4 text-muted" />} label="Choose another dataset" onSelect={() => { setPalette(false); leave(); }} />
              </Command.Group>
            </Command.List>
          </Command>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function Action({ icon, label, keys, onSelect }: { icon: ReactNode; label: string; keys?: string; onSelect: () => void }) {
  return (
    <Command.Item value={label} onSelect={onSelect} className={itemCls}>
      {icon}
      <span className="flex-1">{label}</span>
      {keys && <Kbd>{keys}</Kbd>}
    </Command.Item>
  );
}

function Current() {
  return <span className="rounded-full bg-accent-soft px-2 py-px text-[10.5px] font-medium text-accent">current</span>;
}
