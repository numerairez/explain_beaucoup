import { useCallback, useEffect, useRef, useState } from "react";
import type { Theme } from "../api";
import { lab } from "./labApi";
import type { Ctx, Node, Registry, Trace } from "./labApi";

/** The lab's state. The page holds Contexts as values: the path is a list of
 *  them, and "going back" is just pointing at an older one - nothing is
 *  undone, because nothing was ever changed. */
export type LabState = {
  ready: boolean;
  error: string | null;
  registry: Registry | null;
  root: Node | null;
  path: Node[];
  index: number;
  node: Node | null;
  trace: Trace | null;
  member: string | null;
  busy: boolean;
  run: (verb: string, params: Record<string, unknown>, opts?: { member?: string | null }) => Promise<Trace | null>;
  runFrom: (from: Ctx, kind: string, verb: string, params: Record<string, unknown>, member?: string | null) => Promise<Trace>;
  goTo: (i: number) => void;
  load: (ctx: Ctx, kind: string) => Promise<void>;
  reset: () => void;
  setMember: (m: string | null) => void;
};

export function useLab(sid: string | null, theme: Theme): LabState {
  const [registry, setRegistry] = useState<Registry | null>(null);
  const [root, setRoot] = useState<Node | null>(null);
  const [path, setPath] = useState<Node[]>([]);
  const [index, setIndex] = useState(0);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [member, setMember] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const themeRef = useRef(theme);
  themeRef.current = theme;

  useEffect(() => {
    if (!sid) return;
    lab.start(sid, theme).then(
      (r) => {
        setRegistry(r.registry);
        setRoot(r.start);
        setPath([r.start]);
        setIndex(0);
      },
      (e: unknown) => setError(String(e instanceof Error ? e.message : e)),
    );
    // Only on a new session; a theme change re-reads the node in place below.
  }, [sid]);

  const node = path[index] ?? null;

  // Specs are drawn for a theme, so a theme change re-reads the node on show.
  const shownTheme = useRef(theme);
  useEffect(() => {
    if (!sid || !node || shownTheme.current === theme) return;
    shownTheme.current = theme;
    lab.read(sid, theme, node.context, node.reading.kind, member).then((n) =>
      setPath((p) => p.map((x, i) => (i === index ? n : x))),
    );
  }, [theme, sid, node, index, member]);

  const runFrom = useCallback(
    (from: Ctx, kind: string, verb: string, params: Record<string, unknown>, m: string | null = null) =>
      lab.step(sid!, themeRef.current, { context: from, verb, params, member: m, kind }),
    [sid],
  );

  const run = useCallback(
    async (verb: string, params: Record<string, unknown>, opts?: { member?: string | null }) => {
      if (!sid || !node) return null;
      setBusy(true);
      try {
        const m = opts && "member" in opts ? (opts.member ?? null) : member;
        const t = await runFrom(node.context, node.reading.kind, verb, params, m);
        setTrace(t);
        if (t.ok && t.after && t.reading && t.capabilities) {
          const next: Node = { context: t.after, reading: t.reading, capabilities: t.capabilities };
          setPath((p) => [...p.slice(0, index + 1), next]);
          setIndex(index + 1);
          setMember(null);
        }
        return t;
      } catch (e) {
        setError(String(e instanceof Error ? e.message : e));
        return null;
      } finally {
        setBusy(false);
      }
    },
    [sid, node, member, index, runFrom],
  );

  const goTo = useCallback((i: number) => {
    setIndex(i);
    setTrace(null);
    setMember(null);
  }, []);

  const load = useCallback(
    async (ctx: Ctx, kind: string) => {
      if (!sid) return;
      setBusy(true);
      try {
        const n = await lab.read(sid, themeRef.current, ctx, kind);
        setPath((p) => [...p.slice(0, index + 1), n]);
        setIndex(index + 1);
        setTrace(null);
        setMember(null);
      } finally {
        setBusy(false);
      }
    },
    [sid, index],
  );

  const reset = useCallback(() => {
    if (!root) return;
    setPath([root]);
    setIndex(0);
    setTrace(null);
    setMember(null);
  }, [root]);

  // Selecting a mark redraws the node with that mark ringed, the way the
  // workspace does - the selection is part of the view, not of the Context.
  const select = useCallback(
    (m: string | null) => {
      setMember(m);
      if (!sid || !node) return;
      lab.read(sid, themeRef.current, node.context, node.reading.kind, m).then(
        (n) => setPath((p) => p.map((x, i) => (i === index ? n : x))),
        () => undefined,
      );
    },
    [sid, node, index],
  );

  return {
    ready: !!node && !!registry, error, registry, root, path, index, node, trace, member, busy,
    run, runFrom, goTo, load, reset, setMember: select,
  };
}
