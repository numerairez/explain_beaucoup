// The wire contract with explain_beaucoup/web/server.py. The client renders
// what the session says; it never decides anything analytical itself.

export type Option<T = string> = { value: T; label: string; hint?: string };

export type PanelAction = { action: string; label: string; primary?: boolean; hint?: string };

export type Panel = {
  uid: string;
  context_id: string;
  role: "focus" | "child";
  active: boolean;
  title: string;
  subtitle: string;
  badge: string;
  actions: PanelAction[];
  /** Which component draws `spec` - see charts/registry.tsx. */
  renderer: string;
  spec: Record<string, unknown>;
};

export type Crumb = { uid: string; text: string; pinned: boolean; title: string };

export type TreeItem = {
  uid: string;
  text: string;
  verb: string;
  title: string;
  kind: string;
  pinned: boolean;
  branch: number;
  current: boolean;
  children: TreeItem[];
};

export type Evidence = {
  index: number;
  kind: string;
  label: string;
  role: "pos" | "neg" | "offset" | "link" | "muted";
  score: number;
  headline: string;
  detail: string;
  member: string | null;
  openable: boolean;
};

export type Explanation = {
  context_id: string;
  total: string;
  delta: null | { text: string; pct: number | null; up: boolean; tone: "good" | "critical"; comparison: string };
  where: string;
  best_dimension: string | null;
  evidence: Evidence[];
  queries: number;
};

export type BeatLine = {
  seq: number;
  number: number;
  depth: number;
  head: boolean;
  hidden: boolean;
  uid: string | null;
  move: string;
  selected: string;
  question: string;
  answer: string;
};

export type Layout = "tree" | "timeline";
export type Theme = "light" | "dark";

export type Snapshot = {
  session: string;
  /** The board this session belongs to, and the tile it was dug out of. */
  board: { id: string; tiles: number; tile: string | null; tile_title: string | null };
  title: string;
  scope_label: string;
  theme: Theme;
  current: string;
  selected: string | null;
  toolbar: {
    metrics: Option[];
    grains: Option[];
    periods: Option[];
    comparisons: Option<string | null>[];
    lenses: Option[];
    metric: string;
    grain: string;
    period: string;
    comparison: string | null;
    lens: string | null;
    can_up: boolean;
    can_back: boolean;
    pinned: boolean;
    branch_options: Option[];
  };
  stack: { theme: Theme; scroll_to: string | null; panels: Panel[] };
  crumbs: { path: Crumb[]; siblings: Crumb[] };
  tree: TreeItem[];
  pins: { uid: string; title: string; metric: string; value: string; note: string }[];
  context: {
    rows: { key: string; value: string }[];
    provenance: string;
    notes: string[];
    lineage: { verb: string; summary: string; source: string }[];
  };
  questions: { text: string; verb: string; params: Record<string, unknown>; lands: Lands }[];
  explanation: Explanation | null;
  commentary: { layout: Layout; lines: BeatLine[]; newest: number | null; removed: number };
  report: string;
  status: string;
};

export type MenuItem = {
  label: string;
  action: "run" | "raise" | "close" | "branch" | "pin";
  verb: string;
  params: Record<string, unknown>;
  member: string | null;
  source: string;
  hint: string;
  submenu: string;
  lands: Lands;
};

/** Where a move's result goes: under the chart on top, or in its place. */
export type Lands = "below" | "replace" | "";

export type Menu = { uid: string; heading: string; note: string; groups: MenuItem[][] };

export type Move = {
  type:
    | "run" | "activate" | "select" | "goto" | "raise" | "close" | "back"
    | "pin" | "branch" | "lens" | "metric" | "period" | "time_grain"
    | "comparison" | "explain" | "evidence" | "invoke" | "hide_beat"
    | "restore_beat";
  uid?: string | null;
  verb?: string;
  params?: Record<string, unknown>;
  member?: string | null;
  key?: string | null;
  value?: string | null;
  index?: number | null;
  item?: MenuItem;
  source?: string;
};

export type MoveResult = {
  message: string;
  tone: "info" | "blocked";
  explained: boolean;
  state: Snapshot;
};

export type CatalogEntry = {
  index: number;
  name: string;
  path: string;
  summary: string;
  ok: boolean;
  problem: string;
  metrics: string[];
  dimensions: string[];
  /** Tiles on the model's canned dashboard; 0 when it has none. */
  tiles: number;
};

export type EntryDetail = {
  index: number;
  title: string;
  path: string;
  rows: number;
  grain: string;
  grains: string[];
  periods: Option[];
  metrics: string[];
  dimensions: string[];
  hierarchies: { label: string; levels: string[] }[];
  filterable: Option[];
};

export type ScopeIn = {
  filters: Record<string, string[]>;
  start: string | null;
  end: string | null;
  dims?: string[];
};

export type Preview = {
  rows: number;
  total_rows: number;
  periods: number;
  grain: string;
  span: [string, string] | null;
  breakdowns: number;
  problems: string[];
  notes: string[];
  ok: boolean;
  members: Record<string, string[]>;
  label: string;
};

export type Delta = { text: string; pct: number | null; up: boolean; tone: "good" | "critical"; comparison: string };

export type BoardTile = {
  id: string;
  title: string;
  size: "normal" | "wide";
  view: string | null;
  steps: string[];
  /** Set when the model refuses one of the tile's steps under this scope. */
  error?: string;
  kind?: string;
  context?: string;
  subtitle?: string;
  headline?: { value: string; metric: string; period: string; delta: Delta | null } | null;
  why?: { headline: string; label: string; role: Evidence["role"] } | null;
  /** Members unusual against their own history; null when not read. */
  exceptions?: number | null;
  renderer?: string;
  spec?: Record<string, unknown> | null;
  empty?: boolean;
  investigation: null | {
    session: string;
    views: number;
    at: string | null;
    pins: { uid: string; title: string; note: string; value: string }[];
  };
};

export type Board = {
  board: string;
  title: string;
  dataset: string;
  scope_label: string;
  file: string;
  error: string;
  period: string;
  free: string | null;
  tiles: BoardTile[];
};

/** Where opening a dataset lands: its dashboard, or straight in a session. */
export type Opened = { board: string; session: string | null };

export class ApiError extends Error {}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(detail);
  }
  const type = res.headers.get("content-type") || "";
  return (type.includes("json") ? res.json() : res.text()) as Promise<T>;
}

const q = (o: Record<string, string>) => "?" + new URLSearchParams(o).toString();

export const api = {
  boot: () => call<{ session: string | null; board: string | null; models_dir: string }>("GET", "/api/boot"),
  catalog: () => call<{ models_dir: string; entries: CatalogEntry[] }>("GET", "/api/catalog"),
  entry: (i: number) => call<EntryDetail>("GET", `/api/catalog/${i}`),
  preview: (i: number, scope: ScopeIn) => call<Preview>("POST", `/api/catalog/${i}/preview`, scope),
  open: (entry: number, scope: ScopeIn) => call<Opened>("POST", "/api/boards", { entry, ...scope }),
  board: (bid: string, theme: Theme) => call<Board>("GET", `/api/boards/${bid}${q({ theme })}`),
  investigate: (bid: string, tile: string, key: string | null) =>
    call<{ session: string; uid: string; key: string | null }>(
      "POST", `/api/boards/${bid}/tiles/${encodeURIComponent(tile)}/investigate`, { key }),
  explore: (bid: string) => call<{ session: string }>("POST", `/api/boards/${bid}/explore`),
  removeTile: (bid: string, tile: string, theme: Theme) =>
    call<Board>("DELETE", `/api/boards/${bid}/tiles/${encodeURIComponent(tile)}${q({ theme })}`),
  addTile: (sid: string, uid: string | null, title = "") =>
    call<{ tile: string; title: string; board: string; tiles: number; file: string }>(
      "POST", `/api/sessions/${sid}/dashboard`, { uid, title }),
  snapshot: (sid: string, theme: Theme, layout: Layout) =>
    call<Snapshot>("GET", `/api/sessions/${sid}${q({ theme, layout })}`),
  move: (sid: string, move: Move, theme: Theme, layout: Layout) =>
    call<MoveResult>("POST", `/api/sessions/${sid}/move${q({ theme, layout })}`, move),
  menu: (sid: string, uid: string | null, key: string | null) =>
    call<Menu>("POST", `/api/sessions/${sid}/menu`, { type: "select", uid, key }),
  commentary: (sid: string, fmt: "markdown" | "plain" | "json" | "html", layout: Layout) =>
    call<string>("GET", `/api/sessions/${sid}/commentary${q({ fmt, layout })}`),
  exportReport: (sid: string) => call<string>("GET", `/api/sessions/${sid}/export`),
};
