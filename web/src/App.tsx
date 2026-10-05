import * as Tooltip from "@radix-ui/react-tooltip";
import { useCallback, useEffect, useState } from "react";
import { Toaster } from "sonner";
import { api } from "./api";
import type { Opened, Snapshot } from "./api";
import { Dashboard } from "./components/Dashboard";
import type { Dug } from "./components/Dashboard";
import { Picker } from "./components/Picker";
import { GuidePage } from "./guide/GuidePage";
import { Workspace } from "./components/Workspace";
import { applyTheme, readTheme, WorkspaceProvider } from "./store";
import { Logo } from "./ui";

// The session id lives in the URL hash, so a reload picks the investigation
// back up for as long as the server keeps it.
const fromHash = () => window.location.hash.match(/^#\/s\/(\w+)/)?.[1] ?? null;
// A dataset's dashboard lives at #/d/<board>.
const boardHash = () => window.location.hash.match(/^#\/d\/(\w+)/)?.[1] ?? null;
// "How it works" lives at #/guide, optionally borrowing a session: #/guide/<sid>.
const guideHash = () => {
  const m = window.location.hash.match(/^#\/guide(?:\/(\w+))?/);
  return m ? { sid: m[1] ?? null } : null;
};

export function App() {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [phase, setPhase] = useState<"boot" | "pick" | "board" | "work">("boot");
  const [board, setBoard] = useState<string | null>(null);
  const [pendingMenu, setPendingMenu] = useState<{ uid: string; key: string } | null>(null);
  const [guide, setGuide] = useState(guideHash);
  const theme = readTheme();

  useEffect(() => {
    const onHash = () => setGuide(guideHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => applyTheme(theme), [theme]);

  const enterBoard = useCallback((bid: string) => {
    window.location.hash = `/d/${bid}`;
    setSnap(null);
    setPendingMenu(null);
    setBoard(bid);
    setPhase("board");
  }, []);

  const enter = useCallback(
    async (sid: string, menu: { uid: string; key: string } | null = null) => {
      try {
        const s = await api.snapshot(sid, readTheme(), "tree");
        window.location.hash = `/s/${sid}`;
        setPendingMenu(menu);
        setSnap(s);
        setPhase("work");
      } catch {
        window.location.hash = "";
        setPhase("pick");
      }
    },
    [],
  );

  useEffect(() => {
    if (guideHash()) return;
    const sid = fromHash();
    if (sid) {
      void enter(sid);
      return;
    }
    const bid = boardHash();
    if (bid) {
      enterBoard(bid);
      return;
    }
    api
      .boot()
      .then((b) => (b.session ? enter(b.session) : b.board ? enterBoard(b.board) : setPhase("pick")))
      .catch(() => setPhase("pick"));
  }, [enter, enterBoard]);

  const opened = useCallback(
    (o: Opened) => (o.session ? void enter(o.session) : enterBoard(o.board)),
    [enter, enterBoard],
  );
  const dug = useCallback(
    (d: Dug) => void enter(d.session, d.key !== null ? { uid: d.uid, key: d.key } : null),
    [enter],
  );

  const leave = useCallback(() => {
    window.location.hash = "";
    setSnap(null);
    setPhase("pick");
  }, []);

  if (guide) {
    return (
      <Tooltip.Provider>
        <GuidePage
          sid={guide.sid}
          onBack={(sid) => {
            setGuide(null);
            if (sid && snap?.session === sid) {
              window.location.hash = `/s/${sid}`;
            } else if (sid) {
              void enter(sid);
            } else {
              leave();
            }
          }}
        />
      </Tooltip.Provider>
    );
  }

  return (
    <Tooltip.Provider>
      {phase === "boot" && (
        <div className="grid h-full place-items-center">
          <div className="animate-pulse"><Logo size={36} /></div>
        </div>
      )}
      {phase === "pick" && <Picker onOpen={opened} />}
      {phase === "board" && board && <Dashboard key={board} bid={board} onDig={dug} onLeave={leave} />}
      {phase === "work" && snap && (
        <WorkspaceProvider
          key={snap.session}
          initial={snap}
          onLeave={leave}
          onDashboard={enterBoard}
          initialMenu={pendingMenu}
        >
          <Workspace />
        </WorkspaceProvider>
      )}
      <Toaster
        position="bottom-center"
        offset={44}
        toastOptions={{
          classNames: {
            toast: "!rounded-xl !border !border-line !bg-raised !text-ink !shadow-lg !font-sans",
            description: "!text-muted",
          },
        }}
      />
    </Tooltip.Provider>
  );
}
