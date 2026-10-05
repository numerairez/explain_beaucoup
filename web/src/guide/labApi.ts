import { ApiError } from "../api";
import type { Lands, Theme } from "../api";

/** A Context as it travels: plain data, plus the id the fingerprint gives it. */
export type Ctx = {
  dataset: string;
  metric: string;
  filters: Record<string, string>;
  grain: string[];
  time: { start: string; end: string; grain: string };
  comparison: string | null;
  lineage: { verb: string; summary: string; params: Record<string, unknown>; source: string }[];
  id: string;
  label: string;
  identity: string;
};

export type Capability = {
  verb: string;
  label: string;
  params: Record<string, string>;
  group: string;
  hint: string;
  member_scoped: boolean;
  lands: Lands;
};

export type Reading = {
  kind: string;
  analysis: { kind: string; title: string; registered: boolean };
  cache: { hit: boolean; key: string; size: number; ms: number };
  query: {
    backend: string;
    components: string[];
    filters: [string, string][];
    window: { start: string; end: string; grain: string };
    group_by: string[];
    ratio: [string, string, string] | null;
    sql: string;
  };
  result: {
    total: number | null;
    prior_total: number | null;
    delta_total: number | null;
    fact_rows: number;
    row_count: number;
    notes: string[];
    axes: string[];
    rows: { key: string; label: string; value: number | null; prior: number | null; delta: number | null; share: number | null }[];
    value_txt: string;
  };
  view: { renderer: string; builder: string; spec: Record<string, unknown> };
  marks: string[];
};

/** A node in the lab: a Context, how it reads, and what it offers. */
export type Node = { context: Ctx; reading: Reading; capabilities: Capability[] };

export type StageId = "operation" | "verb" | "apply" | "validate" | "kind" | "engine" | "backend" | "view" | "place";

export type Stage = { stage: StageId; ok: boolean; error?: string; detail?: Record<string, unknown> };

export type Trace = {
  before: Ctx;
  stages: Stage[];
  ok: boolean;
  after?: Ctx;
  reading?: Reading;
  capabilities?: Capability[];
};

export type VerbInfo = {
  name: string;
  class: string;
  module: string;
  doc: string;
  member_scoped: boolean;
  placement: "replace" | "below";
  preserves_kind: boolean;
  carries_change: boolean;
  hierarchy_scoped: boolean;
  advertises: boolean;
};

export type Registry = {
  verbs: VerbInfo[];
  analyses: { kind: string; compute: string; doc: string }[];
  views: { kind: string; renderer: string; builder: string }[];
  signals: { name: string; per: "dimension" | "scope"; emits: { kind: string; weight: number | null }[] }[];
  weights: Record<string, number>;
  backend: { class: string; doc: string; native_grain: string };
  model: {
    dataset: string;
    title: string;
    time_column: string;
    grains: string[];
    dimensions: { name: string; label: string; column: string; hierarchy: string | null; members: number | null }[];
    hierarchies: { name: string; label: string; levels: string[]; labels: string[] }[];
    metrics: { name: string; label: string; kind: string; rule: string; invalid_grains: string[] }[];
    comparisons: { name: string; label: string }[];
  };
};

export type Explanation =
  | { ok: false; error: string }
  | {
      ok: true;
      comparison: string | null;
      total: string;
      delta: string | null;
      delta_pct: number | null;
      dimensions_tested: string[];
      best_dimension: string | null;
      queries: number;
      evidence: {
        kind: string;
        headline: string;
        detail: string;
        score: number;
        weight: number | null;
        dimension: string | null;
        target: Ctx | null;
        target_kind: string;
      }[];
    };

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
  return res.json() as Promise<T>;
}

const base = (sid: string) => `/api/sessions/${sid}/lab`;

export const lab = {
  start: (sid: string, theme: Theme) =>
    call<{ start: Node; registry: Registry }>("GET", `${base(sid)}?theme=${theme}`),
  step: (sid: string, theme: Theme, body: { context: Ctx; verb: string; params: Record<string, unknown>; member: string | null; kind: string }) =>
    call<Trace>("POST", `${base(sid)}/step?theme=${theme}`, body),
  read: (sid: string, theme: Theme, context: Ctx, kind: string, selected: string | null = null) =>
    call<Node>("POST", `${base(sid)}/read?theme=${theme}`, { context, kind, selected }),
  explain: (sid: string, context: Ctx) => call<Explanation>("POST", `${base(sid)}/explain`, { context }),
};
