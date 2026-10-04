import type { ComponentType } from "react";
import { VegaChart } from "../components/VegaChart";
import type { MarkHandlers } from "../components/VegaChart";

/** What every chart renderer receives. A renderer draws `spec` however it
 *  likes, but reports marks by their `key` through `handlers` - that is what
 *  lets a mark in any renderer open the same governed menu. */
export type RendererProps = {
  spec: Record<string, unknown>;
  handlers: MarkHandlers;
  compact?: boolean;
};

/** Panel `renderer` name (set server-side in `view/vega.py`'s VIEWS) ->
 *  the component that draws it. Register a new library here. */
export const RENDERERS: Record<string, ComponentType<RendererProps>> = {
  "vega-lite": VegaChart,
};

export function Chart({ renderer, ...props }: RendererProps & { renderer?: string }) {
  const Component = RENDERERS[renderer ?? "vega-lite"];
  if (!Component) {
    return (
      <div className="grid h-40 place-items-center text-[12.5px] text-muted">
        No renderer for “{renderer}” in this client
      </div>
    );
  }
  return <Component {...props} />;
}
