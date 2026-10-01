import type { ReactNode } from "react";

// The small markdown subset the engine writes in: `[label](node:uid)` chart
// links, **bold**, _italic_ and `code`. Rendered to elements, never to HTML
// strings, so nothing the data says can become markup.

const INLINE = /\[([^\]]+)\]\((node:[^)]+)\)|\*\*(.+?)\*\*|(?<![\w])_(.+?)_(?![\w])|`([^`]+)`/g;

export function Inline({ text, onNode }: { text: string; onNode?: (uid: string) => void }) {
  const out: ReactNode[] = [];
  let last = 0;
  let i = 0;
  for (const m of text.matchAll(INLINE)) {
    if (m.index! > last) out.push(text.slice(last, m.index));
    const k = i++;
    if (m[1] !== undefined) {
      const uid = m[2].slice("node:".length);
      out.push(
        <button
          key={k}
          onClick={() => onNode?.(uid)}
          className="font-medium text-accent underline decoration-accent/30 underline-offset-2 transition-colors hover:decoration-accent"
        >
          <Inline text={m[1]} />
        </button>,
      );
    } else if (m[3] !== undefined) {
      out.push(<strong key={k} className="font-semibold text-ink"><Inline text={m[3]} onNode={onNode} /></strong>);
    } else if (m[4] !== undefined) {
      out.push(<em key={k} className="text-ink-2 italic"><Inline text={m[4]} onNode={onNode} /></em>);
    } else if (m[5] !== undefined) {
      out.push(
        <code key={k} className="rounded bg-sunken px-1 py-px font-mono text-[10.5px] text-muted">{m[5]}</code>,
      );
    }
    last = m.index! + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return <>{out}</>;
}

/** Block markdown for the report: headings, list items, paragraphs. */
export function Markdown({ text, onNode }: { text: string; onNode?: (uid: string) => void }) {
  const blocks = text.split(/\n{2,}/);
  return (
    <div className="space-y-3 text-[12.5px] leading-relaxed text-ink-2">
      {blocks.map((block, bi) => {
        const lines = block.split("\n").filter((l) => l.trim());
        if (!lines.length) return null;
        const h = lines[0].match(/^(#{1,3})\s+(.*)$/);
        if (h && lines.length === 1) {
          const size = h[1].length === 1 ? "text-[17px]" : h[1].length === 2 ? "text-[14px]" : "text-[13px]";
          return (
            <div key={bi} className={`${size} pt-1 font-semibold tracking-tight text-ink`}>
              <Inline text={h[2]} onNode={onNode} />
            </div>
          );
        }
        if (lines.every((l) => /^\s*([-*]|\d+\.)\s/.test(l))) {
          return (
            <ul key={bi} className="space-y-1.5">
              {lines.map((l, li) => {
                const depth = Math.floor((l.match(/^\s*/)?.[0].length ?? 0) / 2);
                return (
                  <li key={li} className="flex gap-2" style={{ paddingLeft: depth * 14 }}>
                    <span className="mt-[7px] size-1 shrink-0 rounded-full bg-muted" />
                    <span><Inline text={l.replace(/^\s*([-*]|\d+\.)\s/, "")} onNode={onNode} /></span>
                  </li>
                );
              })}
            </ul>
          );
        }
        return (
          <div key={bi}>
            {lines.map((l, li) => {
              const indent = /^\s{2,}/.test(l);
              return (
                <div key={li} className={indent ? "pl-4 text-[12px] text-muted" : "text-ink"}>
                  <Inline text={l.trim()} onNode={onNode} />
                </div>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}
