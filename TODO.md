# TODO — extracting the framework

Where this comes from: the backend is already close to reusable, but a third
party cannot get at it. The grammar, the semantic validation and the explain
engine are all dataset-neutral and Qt-free; what is missing is a *seam* to hold
them by. Everything below is about exposing what exists rather than building
something new.

This is roadmap **phase 7** in README terms: the framework extraction. It
changes no analytical behaviour. Any item that would change a number or a
ranking is out of scope and called out as such.

## Decisions taken

These three fork the design, so they are recorded rather than re-argued:

| Decision | Choice | Consequence |
|---|---|---|
| Primary consumer | **Python library / notebook** — import it, point it at a DataFrame and a model, drive it in code or Jupyter | Session serialisation, shareable URLs and multi-tenant caching drop down the list. Reprs, fluent verbs and raised exceptions move up. |
| Compute | **`DataBackend` protocol, pandas first** — extract the questions the engine asks, move today's code behind `PandasBackend`, ship with no second implementation | The protocol is the deliverable. A SQL or DuckDB backend becomes somebody else's afternoon, not a rewrite of `engine/engine.py`. |
| Extension | **Registries + entry points** — verbs, signals and renderers register themselves; installed plugins discovered via entry points | Registration must work at *runtime*, not only at install time: a notebook user needs `@eb.signal(...)` in a cell to work. |

## What is deliberately not in scope

* **Multi-dataset or joined sessions.** `SemanticModel.validate` requires
  `ctx.dataset == model.dataset` (`semantic/specs.py`). One governed table per
  model stays a constraint of the design — but say so in the docs rather than
  leaving people to discover it.
* **A dashboard builder.** Still phase 6, still deliberately not started.
* **Changing any threshold, weight or ranking.** Phase 6 below moves the
  literals into a policy object; it does not retune them.

---

## Phase 0 — declare the public API

`explain_beaucoup/__init__.py` is empty, so today *everything* is accidentally
public and nothing is stable. This has to come first because it constrains
every phase after it.

- [ ] Export the intended surface from `__init__.py`: `open`, `Workspace`,
      `Context`, `TimeWindow`, the verb/signal/renderer decorators, the result
      and evidence types, `SemanticError`.
- [ ] A short "what is stable" note in the README: the exported names and the
      model YAML schema are the contract; module paths are not.
- [ ] Add `version:` to the model YAML schema, accepted and defaulted by
      `semantic/loader.py`. Third parties will pin to the schema, and there is
      no migration story without a version key on the file.

## Phase 1 — headless `Session` / `Workspace`

The largest coupling: the interaction semantics live in the Qt window.
`run_op`, `navigate`, `_existing_child`, `raise_node`, `_node_title`, the
`result_kind` override, `target_kind` handling and the `PRESERVE_KIND` /
`IN_PLACE` / `MEMBER_VERBS` / `GROUP_ORDER` tables are all in
`ui/main_window.py`. Nobody outside Qt can reproduce that behaviour.

- [ ] New `core/session.py`: owns model, engine, graph, per-node selection and
      chart-kind resolution. Every verb goes through `Session.run(verb, params)`.
- [ ] Move the kind-resolution rules out of the UI verbatim — `result_kind`,
      the `PRESERVE_KIND` inheritance, and `Evidence.target_kind` — so the
      chart type a card lands on is decided in one testable place.
- [ ] `MainWindow` becomes a subscriber: it renders session state and forwards
      gestures. No analytical decision left in the widget.
- [ ] **Errors raise.** `_blocked()` currently swallows `SemanticError` into the
      status bar; a library must propagate it and let the UI catch. This is the
      correct layering regardless.
- [ ] Fix the process-global node counter (`_ids` in `core/graph.py`) — two
      `Workspace` objects in one kernel share a uid sequence today.
- [ ] Tests: drive a whole investigation through `Session` alone, asserting the
      landing chart kinds that `ui/main_window.py` currently decides.

## Phase 2 — notebook ergonomics

Where it starts to feel like a framework rather than an app.

- [ ] `eb.open("model.yaml")` and `eb.Workspace(frame, model)` as the two ways in.
- [ ] Fluent verbs that return the new node — `node.decompose("region")`,
      `node.trend()`, `node.explain()` — while the graph still records the edge.
- [ ] `_repr_mimebundle_` on results and nodes. `view/vega.py` already emits a
      spec, so Jupyter renders it natively as `application/vnd.vega.v5+json`.
      Highest ratio of payoff to effort in this document: charts in notebooks,
      no PyQt, almost no new code.
- [ ] `.table()` returning a DataFrame, so people can leave the framework for
      their own analysis without fighting it.
- [ ] Reprs worth reading — notebook users live in them. `Node` should print
      its context, kind and top rows.
- [ ] `Evidence.open()` as the API form of the evidence-card path: honour
      `target` and `target_kind` the same way the cards do.
- [ ] A notebook under `docs/` or `examples/` driving the bundled dataset end to
      end, run in CI so it cannot rot.

## Phase 3 — packaging split

- [ ] PyQt6 and PyQt6-WebEngine move to a `[gui]` extra; `pip install
      explain-beaucoup` stops pulling ~150MB of GUI for a library user.
- [ ] Confirm `cli.py` imports the UI lazily in every path (it looks like it
      does) and that `init` / `check` work with the GUI uninstalled.
- [ ] Keep `[parquet]`, `[excel]`, `[dev]`; add `[sql]` as a placeholder for
      phase 5.
- [ ] CI job that installs the base package only and runs the non-GUI tests.

## Phase 4 — registries

One verb's identity is spread across five places: the `apply()` if/elif chain
and `result_kind()` in `core/operations.py`, the `capabilities()` branches in
`semantic/specs.py`, and the four verb sets in `ui/main_window.py`. A registry
collapses that to one declaration per verb — which is a simplification first
and an extension point second.

- [ ] Verb registry: a decorator carrying label, group, result kind, whether it
      preserves kind, whether it re-frames in place, and whether it takes a
      member.
- [ ] Each verb owns its own **availability** hook. `capabilities()` is not a
      static list — it tests whether a child dimension exists, whether a
      comparison would overlap the window, whether the metric is legal at the
      grain. So a verb registers `available(model, ctx) -> Iterable[Capability]`
      and `semantic/specs.py` becomes the loop that asks each one.
- [ ] Signal registry for `engine/explain.py`. Needs a uniform `SignalInput`
      first: today's signal functions take different arguments (some a parent
      delta, some a dimension context).
- [ ] Renderer registry — `view/vega.py` already has the builder dict; this is
      mostly renaming.
- [ ] Runtime registration *and* entry-point discovery under an
      `explain_beaucoup.plugins` group, over the same registry.
- [ ] Test that a plugin verb registered in-process appears in the right-click
      menu with no UI change, and that a plugin signal ranks in `explain()`.

## Phase 5 — `DataBackend` protocol

Every result kind reduces to the same primitive: sum the metric's component
columns over a scope of filters plus a time window, grouped by zero or more
keys. `_breakdown` groups by a dimension or the period, `_timeseries` by the
period, `_distribution` by the cell dimensions, `_exceptions` by dimension and
period together, totals by nothing.

- [ ] Define the protocol — roughly `aggregate(filters, window, measures, by)`,
      `row_count`, `periods`, `extent`, `distinct_counts`.
- [ ] Make `GroupKey` either a dimension column or `Period(grain)`. That is the
      decision that makes the seam real: pandas uses its cached key series
      (`data/source.py`), SQL emits `DATE_TRUNC`, and the engine stops caring.
- [ ] Keep in the engine, where the governance belongs: ratio recomputation,
      deltas, shares, the missing-member pass, z-scores, quantiles and
      partial-period detection.
- [ ] Replace the remaining pandas calls in the statistics (`vals.std(ddof=1)`,
      `mean()`, the quantile helper) with `statistics`, so `engine/engine.py`
      itself becomes pandas-free.
- [ ] Move today's masking and `groupby` into `PandasBackend` with no behaviour
      change — `tests/test_framework.py` asserts engine results against the raw
      frame, so it is the regression net.
- [ ] Make the result cache injectable. `self._cache` is an unbounded dict keyed
      by fingerprint: right for a desktop session, a leak anywhere else.
- [ ] Decide what a catalogue **scope** becomes. It slices the frame today
      (`semantic/catalog.py`); behind a backend it should be a persistent filter
      set and window clamp. The guarantee to preserve is that no verb can escape
      it.

## Phase 6 — policy and phrasing

- [ ] `ExplainPolicy` for the literals: the `WEIGHT` table, the 0.12 and 0.15
      share thresholds, the z-score divisor, the baseline period counts. Passed
      to `explain()` or hung off the model. Same defaults — no retuning here.
- [ ] Route the evidence headlines and details through a phrasebook instead of
      inline f-strings, so a team can relabel or translate without forking
      `engine/explain.py`.

---

## Open questions

* Does `Workspace` expose the graph directly or behind a façade? The fluent
  notebook API and the branch/pin model pull in different directions.
* Do plugin verbs get validated against the semantic model the same way as
  built-ins, or is a plugin trusted? The framework's whole claim is that
  nonsense is blocked before a chart exists, so probably the former.
* `semantic/sales_model.py` and `data/generate.py` are demo fixtures sitting in
  the package. Split them into an `examples/` extra, or leave them as the thing
  that makes `pip install` immediately runnable?
