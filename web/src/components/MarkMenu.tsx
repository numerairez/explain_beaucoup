import * as DM from "@radix-ui/react-dropdown-menu";
import { ArrowUpRight, ChevronRight, GitBranch, Pin, Sparkles, X } from "lucide-react";
import type { ReactNode } from "react";
import type { MenuItem } from "../api";
import { useWorkspace } from "../store";

const content =
  "pop z-50 min-w-[248px] max-w-[340px] overflow-hidden rounded-xl border border-line bg-raised p-1 shadow-lg outline-none";
const row =
  "relative flex cursor-default items-center gap-2.5 rounded-lg px-2.5 py-[7px] text-[12.5px] text-ink outline-none select-none data-[highlighted]:bg-sunken data-[disabled]:opacity-40";

function iconFor(item: MenuItem): ReactNode {
  const cls = "size-3.5 shrink-0 text-muted";
  if (item.verb === "explain") return <Sparkles className={`${cls} text-accent`} />;
  if (item.action === "raise") return <ArrowUpRight className={cls} />;
  if (item.action === "close") return <X className={cls} />;
  if (item.action === "branch") return <GitBranch className={cls} />;
  if (item.action === "pin") return <Pin className={cls} />;
  return <span className="size-3.5 shrink-0" />;
}

/** The analytical grammar for one mark, anchored where it was right-clicked. */
export function MarkMenu() {
  const { menu, closeMenu, act, snap } = useWorkspace();
  if (!menu) return null;
  const { x, y, menu: m } = menu;

  const run = (item: MenuItem) => {
    closeMenu();
    void act({ type: "invoke", uid: m.uid, item });
  };

  const renderItem = (item: MenuItem, i: number) =>
    item.action === "branch" ? (
      <DM.Sub key={`b${i}`}>
        <DM.SubTrigger className={row}>
          {iconFor(item)}
          <span className="flex-1">Branch from here</span>
          <ChevronRight className="size-3.5 text-muted" />
        </DM.SubTrigger>
        <DM.Portal>
          <DM.SubContent className={content} sideOffset={4}>
            <DM.Label className="px-2.5 pt-1.5 pb-1 text-[11px] text-muted">A competing hypothesis, beside this path</DM.Label>
            {snap.toolbar.branch_options.length === 0 && (
              <DM.Item disabled className={row}>No alternative dimension</DM.Item>
            )}
            {snap.toolbar.branch_options.map((o) => (
              <DM.Item
                key={o.value}
                className={row}
                onSelect={() => {
                  closeMenu();
                  void act({ type: "branch", value: o.value });
                }}
              >
                Break down by {o.label}
              </DM.Item>
            ))}
          </DM.SubContent>
        </DM.Portal>
      </DM.Sub>
    ) : (
      <DM.Item key={i} className={row} onSelect={() => run(item)} title={item.hint || undefined}>
        {iconFor(item)}
        <span className="flex-1 leading-snug">{item.label}</span>
      </DM.Item>
    );

  return (
    <DM.Root open onOpenChange={(o) => !o && closeMenu()} modal={false}>
      <DM.Trigger asChild>
        <span style={{ position: "fixed", left: x, top: y, width: 0, height: 0 }} />
      </DM.Trigger>
      <DM.Portal>
        <DM.Content
          data-floating
          className={content}
          align="start"
          sideOffset={2}
          collisionPadding={12}
          onCloseAutoFocus={(e) => e.preventDefault()}
        >
          <div className="px-2.5 pt-2 pb-1.5">
            <div className="truncate text-[12.5px] font-semibold text-ink tabular">{m.heading}</div>
            {m.note && <div className="mt-0.5 text-[11px] leading-snug text-muted">{m.note}</div>}
          </div>
          {m.groups.map((group, gi) => {
            const plain = group.filter((it) => !it.submenu);
            const nested = group.filter((it) => it.submenu);
            return (
              <div key={gi}>
                <DM.Separator className="mx-1 my-1 h-px bg-line" />
                {plain.map(renderItem)}
                {nested.length > 0 && (
                  <DM.Sub>
                    <DM.SubTrigger className={row}>
                      <span className="size-3.5 shrink-0" />
                      <span className="flex-1">{nested[0].submenu}</span>
                      <ChevronRight className="size-3.5 text-muted" />
                    </DM.SubTrigger>
                    <DM.Portal>
                      <DM.SubContent className={content} sideOffset={4}>
                        {nested.map((it, i) => (
                          <DM.Item key={i} className={row} onSelect={() => run(it)}>
                            {it.label}
                          </DM.Item>
                        ))}
                      </DM.SubContent>
                    </DM.Portal>
                  </DM.Sub>
                )}
              </div>
            );
          })}
        </DM.Content>
      </DM.Portal>
    </DM.Root>
  );
}
