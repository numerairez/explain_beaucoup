import * as Tooltip from "@radix-ui/react-tooltip";
import { useCallback, useEffect, useState } from "react";
import { Toaster } from "sonner";
import { api } from "./api";
import type { Snapshot } from "./api";
import { Picker } from "./components/Picker";
import { Workspace } from "./components/Workspace";
import { applyTheme, readTheme, WorkspaceProvider } from "./store";
import { Logo } from "./ui";

// The session id lives in the URL hash, so a reload picks the investigation
// back up for as long as the server keeps it.
const fromHash = () => window.location.hash.match(/^#\/s\/(\w+)/)?.[1] ?? null;

export function App() {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [phase, setPhase] = useState<"boot" | "pick" | "work">("boot");
  const theme = readTheme();

  useEffect(() => applyTheme(theme), [theme]);

  const enter = useCallback(
    async (sid: string) => {
      try {
        const s = await api.snapshot(sid, readTheme(), "tree");
        window.location.hash = `/s/${sid}`;
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
    const sid = fromHash();
    if (sid) {
      void enter(sid);
      return;
    }
    api.boot().then((b) => (b.session ? enter(b.session) : setPhase("pick"))).catch(() => setPhase("pick"));
  }, [enter]);

  const leave = useCallback(() => {
    window.location.hash = "";
    setSnap(null);
    setPhase("pick");
  }, []);

  return (
    <Tooltip.Provider>
      {phase === "boot" && (
        <div className="grid h-full place-items-center">
          <div className="animate-pulse"><Logo size={36} /></div>
        </div>
      )}
      {phase === "pick" && <Picker onOpen={enter} />}
      {phase === "work" && snap && (
        <WorkspaceProvider key={snap.session} initial={snap} onLeave={leave}>
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
