import { useEffect, useRef, useState } from "react";
import embed from "vega-embed";
import type { Result } from "vega-embed";

type Item = { datum?: Record<string, unknown> } | null | undefined;

export type MarkHandlers = {
  onSelect: (key: string) => void;
  onActivate: (key: string) => void;
  onMenu: (key: string | null, x: number, y: number) => void;
};

const keyOf = (item: Item): string | null => {
  const k = item?.datum?.key;
  return k === undefined || k === null ? null : String(k);
};

function rowCount(spec: Record<string, unknown>): number {
  const d = spec.data as { values?: unknown[] } | undefined;
  if (d && Array.isArray(d.values)) return d.values.length;
  for (const l of (spec.layer as Record<string, unknown>[] | undefined) ?? []) {
    const ld = l.data as { values?: unknown[] } | undefined;
    if (ld && Array.isArray(ld.values)) return ld.values.length;
  }
  return -1;
}

/** The server's spec, dressed for the page: the card is the background,
 *  and the type matches the UI. */
function dress(spec: Record<string, unknown>): Record<string, unknown> {
  const config = { ...(spec.config as Record<string, unknown>) };
  const font = "Inter Variable, ui-sans-serif, system-ui, sans-serif";
  config.background = "transparent";
  config.font = font;
  for (const part of ["axis", "legend", "title", "text"]) {
    const c = config[part] as Record<string, unknown> | undefined;
    if (!c) continue;
    config[part] = {
      ...c,
      ...("labelFont" in c ? { labelFont: font } : {}),
      ...("titleFont" in c ? { titleFont: font } : {}),
      ...("font" in c ? { font } : {}),
      ...("subtitleFont" in c ? { subtitleFont: font } : {}),
    };
  }
  return { ...spec, config };
}

export function VegaChart({ spec, handlers, compact }: {
  spec: Record<string, unknown>;
  handlers: MarkHandlers;
  compact?: boolean;
}) {
  const host = useRef<HTMLDivElement>(null);
  const h = useRef(handlers);
  h.current = handlers;
  const [error, setError] = useState<string | null>(null);
  const json = JSON.stringify(spec);
  const rows = rowCount(spec);

  useEffect(() => {
    if (!host.current || rows === 0) return;
    let result: Result | null = null;
    let dead = false;
    const el = host.current;
    // Hold the old height while the new view lays out, so a re-render (a new
    // selection, a theme) does not make the page jump.
    el.style.minHeight = `${el.offsetHeight}px`;
    setError(null);
    embed(el, dress(JSON.parse(json)) as never, {
      actions: false,
      renderer: "svg",
      tooltip: { theme: "custom" },
    })
      .then((r) => {
        if (dead) {
          r.finalize();
          return;
        }
        result = r;
        requestAnimationFrame(() => { el.style.minHeight = ""; });
        r.view.addEventListener("click", (_ev, item) => {
          const key = keyOf(item as Item);
          if (key !== null) h.current.onSelect(key);
        });
        r.view.addEventListener("dblclick", (_ev, item) => {
          const key = keyOf(item as Item);
          if (key !== null) h.current.onActivate(key);
        });
        r.view.addEventListener("contextmenu", (ev, item) => {
          const e = ev as MouseEvent;
          e.preventDefault();
          e.stopPropagation();
          h.current.onMenu(keyOf(item as Item), e.clientX, e.clientY);
        });
      })
      .catch((e: unknown) => setError(String(e)));
    return () => {
      dead = true;
      result?.finalize();
    };
  }, [json, rows]);

  if (rows === 0) {
    return (
      <div className={`grid place-items-center text-[12px] text-muted ${compact ? "h-32" : "h-72"}`}>
        No data in this analytical scope.
      </div>
    );
  }
  return (
    <>
      <div ref={host} className="w-full" />
      {error && <div className="py-6 text-center text-[12px] text-critical">Chart error: {error}</div>}
    </>
  );
}
