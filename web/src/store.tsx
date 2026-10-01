import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { toast } from "sonner";
import { api, ApiError } from "./api";
import type { Layout, Menu, Move, Snapshot, Theme } from "./api";

export type RightTab = "explain" | "commentary" | "next" | "context" | "report";

type MenuState = { x: number; y: number; menu: Menu } | null;

type Store = {
  snap: Snapshot;
  busy: boolean;
  theme: Theme;
  layout: Layout;
  hint: string;
  rightTab: RightTab;
  menu: MenuState;
  palette: boolean;
  act: (move: Move) => Promise<void>;
  openMenu: (uid: string | null, key: string | null, x: number, y: number) => Promise<void>;
  closeMenu: () => void;
  setTheme: (t: Theme) => void;
  setLayout: (l: Layout) => void;
  setRightTab: (t: RightTab) => void;
  setPalette: (open: boolean) => void;
  leave: () => void;
};

const Ctx = createContext<Store | null>(null);

export function useWorkspace(): Store {
  const s = useContext(Ctx);
  if (!s) throw new Error("useWorkspace outside WorkspaceProvider");
  return s;
}

export function readTheme(): Theme {
  try {
    const t = localStorage.getItem("eb.theme");
    if (t === "light" || t === "dark") return t;
  } catch {
    /* storage blocked */
  }
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function applyTheme(t: Theme) {
  document.documentElement.dataset.theme = t;
  try {
    localStorage.setItem("eb.theme", t);
  } catch {
    /* storage blocked */
  }
}

export function WorkspaceProvider({
  initial,
  onLeave,
  children,
}: {
  initial: Snapshot;
  onLeave: () => void;
  children: ReactNode;
}) {
  const [snap, setSnap] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [theme, setThemeState] = useState<Theme>(initial.theme);
  const [layout, setLayoutState] = useState<Layout>(initial.commentary.layout);
  const [hint, setHint] = useState("");
  const [rightTab, setRightTab] = useState<RightTab>("commentary");
  const [menu, setMenu] = useState<MenuState>(null);
  const [palette, setPalette] = useState(false);
  const sid = initial.session;

  // Moves apply in the order they were made: a double-click is two selects
  // and an activate, and the last answer must be the one rendered.
  const chain = useRef<Promise<unknown>>(Promise.resolve());
  const view = useRef({ theme, layout });
  view.current = { theme, layout };

  const enqueue = useCallback(<T,>(job: () => Promise<T>): Promise<T> => {
    const next = chain.current.then(job, job);
    chain.current = next.catch(() => undefined);
    return next;
  }, []);

  const act = useCallback(
    (move: Move) =>
      enqueue(async () => {
        setBusy(true);
        try {
          const r = await api.move(sid, move, view.current.theme, view.current.layout);
          setSnap(r.state);
          if (move.type === "select") {
            setHint(r.message);
            return;
          }
          setHint("");
          if (r.tone === "blocked") {
            toast.error("Not available here", { description: r.message });
          } else if (r.explained) {
            setRightTab("explain");
          } else if (r.message) {
            toast(r.message.split("   ·   ")[0], {
              description: r.message.split("   ·   ").slice(1).join(" · ") || undefined,
            });
          }
        } catch (e) {
          toast.error("Something went wrong", { description: String((e as Error).message) });
          if (e instanceof ApiError && /session has ended/.test(e.message)) onLeave();
        } finally {
          setBusy(false);
        }
      }),
    [enqueue, sid, onLeave],
  );

  const refresh = useCallback(
    (t: Theme, l: Layout) =>
      enqueue(async () => {
        setSnap(await api.snapshot(sid, t, l));
      }),
    [enqueue, sid],
  );

  const setTheme = useCallback(
    (t: Theme) => {
      applyTheme(t);
      setThemeState(t);
      view.current.theme = t;
      void refresh(t, view.current.layout);
    },
    [refresh],
  );

  const setLayout = useCallback(
    (l: Layout) => {
      setLayoutState(l);
      view.current.layout = l;
      void refresh(view.current.theme, l);
    },
    [refresh],
  );

  const openMenu = useCallback(
    (uid: string | null, key: string | null, x: number, y: number) =>
      enqueue(async () => {
        try {
          const m = await api.menu(sid, uid, key);
          setMenu({ x, y, menu: m });
        } catch (e) {
          toast.error("Could not build the menu", { description: String((e as Error).message) });
        }
      }),
    [enqueue, sid],
  );

  useEffect(() => applyTheme(theme), [theme]);

  const value = useMemo<Store>(
    () => ({
      snap, busy, theme, layout, hint, rightTab, menu, palette,
      act, openMenu, closeMenu: () => setMenu(null),
      setTheme, setLayout, setRightTab, setPalette, leave: onLeave,
    }),
    [snap, busy, theme, layout, hint, rightTab, menu, palette, act, openMenu, setTheme, setLayout, onLeave],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
