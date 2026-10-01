import { useEffect } from "react";
import { Group, Panel, Separator } from "react-resizable-panels";
import { useWorkspace } from "../store";
import { ChartStack } from "./ChartStack";
import { Crumbs, StatusBar } from "./Crumbs";
import { LeftPanel } from "./LeftPanel";
import { MarkMenu } from "./MarkMenu";
import { Palette } from "./Palette";
import { RightPanel } from "./RightPanel";
import { TopBar } from "./TopBar";

export function Workspace() {
  const { busy, menu } = useWorkspace();
  useShortcuts();
  return (
    <div className="flex h-full flex-col">
      <div className={`relative h-0.5 shrink-0 overflow-hidden ${busy ? "busy-bar" : ""}`} />
      <TopBar />
      <Group orientation="horizontal" className="min-h-0 flex-1">
        <Panel id="left" defaultSize={260} minSize={200} maxSize={420} collapsible collapsedSize={0}>
          <LeftPanel />
        </Panel>
        <Separator />
        <Panel id="centre" minSize={480}>
          <main className="flex h-full flex-col bg-paper">
            <Crumbs />
            <div className="min-h-0 flex-1">
              <ChartStack />
            </div>
          </main>
        </Panel>
        <Separator />
        <Panel id="right" defaultSize={400} minSize={320} maxSize={640} collapsible collapsedSize={0}>
          <RightPanel />
        </Panel>
      </Group>
      <StatusBar />
      <MarkMenu key={menu ? `${menu.x}:${menu.y}:${menu.menu.heading}` : "none"} />
      <Palette />
    </div>
  );
}

function useShortcuts() {
  const { act, snap, theme, setTheme, setPalette, palette, menu } = useWorkspace();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = (e.target as HTMLElement)?.closest("input, textarea, [contenteditable]");
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette(!palette);
        return;
      }
      if (typing || palette || menu || e.metaKey || e.ctrlKey || e.altKey) return;
      const t = snap.toolbar;
      switch (e.key.toLowerCase()) {
        case "e":
          void act({ type: "explain", member: snap.selected });
          break;
        case "p":
          void act({ type: "pin" });
          break;
        case "u":
          if (t.can_up) void act({ type: "run", verb: "drill_up" });
          break;
        case "[":
        case "backspace":
          if (t.can_back) void act({ type: "back" });
          break;
        case "b":
          setPalette(true);
          break;
        case "d":
          setTheme(theme === "dark" ? "light" : "dark");
          break;
        default:
          return;
      }
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [act, snap, theme, setTheme, setPalette, palette, menu]);
}
