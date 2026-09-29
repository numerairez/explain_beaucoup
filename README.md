# Explain Beaucoup

An implementation of the *Interactive Analytical Dashboard Framework* design
plan: a dashboard treated not as charts linked by filters, but as an
**explorable graph of analytical contexts**.

* **UI** — PyQt5 5.15 (with QtWebEngine hosting the chart surface)
* **Charts** — Vega-Lite 6, rendered locally (vendored; no network at runtime)
* **Numbers** — a deterministic pandas engine. Nothing else computes a value.

```bash
python3.11 -m venv .venv && .venv/bin/pip install -e .   # Python 3.10 or 3.11
.venv/bin/explain-beaucoup run                         # pick a dataset from models/
.venv/bin/explain-beaucoup run models/sales.yaml       # or straight into the demo
```

## Use it with your own data

The framework is not built around the demo dataset. Everything — drill paths,
menus, valid aggregations, explanations — is generated from a semantic model
you write in YAML.

```bash
explain-beaucoup init  orders.csv --time order_date   # profile and scaffold
$EDITOR orders.yaml                                 # hierarchies, ratios, defaults
explain-beaucoup check orders.yaml --sample           # verify against your data
explain-beaucoup run   orders.yaml
```

**[SETUP.md](SETUP.md) is the guide** — data requirements, the full model
reference, what `check` tells you, and the limits worth knowing up front.
Budget 30–60 minutes for a table you know well; the rest is seconds.

### The three commands

| Command | What it does |
|---|---|
| `init <data>` | Reads csv / parquet / xlsx / json / feather, prints a per-column profile (time / dimension / measure / skipped, with the reason), and writes a starter YAML model beside the data. `--time` names the time column if detection is wrong; `--out`, `--name`, `--sheet`, `--separator`, `--force` adjust the rest. |
| `check <model>` | Loads the model, joins it to the real data, and reports errors and warnings — missing columns, ratios declared as additive, hierarchy levels that do not nest, grains finer than the data. Errors mean it will not run. `--sample` also computes a first breakdown and a real explanation, so you see the whole path work without opening a window. |
| `run [<model>]` | Opens the workspace. With no model it opens the **dataset picker** first — the models in `models/` (`--models DIR` for a folder elsewhere), and the filters that narrow the data before the session starts. `python main.py [model.yaml]` is the same thing. |

### Choosing a dataset, and scoping it before it locks in

`explain-beaucoup run` with no model opens the picker rather than a chart.

* **The datasets** are the YAML models in `models/`, described from the YAML
  alone — so a folder lists instantly, without reading a row of anyone's data.
  A model that will *not* load is listed too, greyed out, carrying its reason
  (`metrics: at least one metric is required`, a data file that has moved).
  Hiding a broken model is worse than explaining it.
* **The scope** is chosen before the workspace exists: a period range, plus a
  set of members per dimension. Member lists cross-filter as filters are added —
  pick Luzon and the next row only offers Luzon's provinces — and a live count
  says what is about to be locked in: `19,596 of 47,280 rows (41%) · 12 months,
  Sep 2025 to Aug 2026 · 7 dimensions to break down by`.

A scope is **not** the `focus` verb. `focus` pins one member inside a session,
and `drill_up` walks back out of it. A scope narrows the governed frame itself,
so every total, share, baseline and explanation afterwards is computed against
the subset and no verb in the grammar can escape it. That is why it is chosen
once, up front — and why the window title and a permanent status-bar note carry
it for the rest of the session, so a scoped total is never read as the whole
table.

Two things fall out of narrowing the *data* rather than the Context:

* Cardinality is **re-profiled** against the subset, so a dimension the scope
  collapsed to a single member stops being offered as a breakdown — the same
  rule that already hides single-member hierarchy levels. If the model's landing
  grain is the one that collapsed, the workspace opens on the next level down
  instead of a one-bar chart.
* A scope that would leave no rows, or no dimension to break the first view down
  by, is **refused in the picker**, by name. The engine would refuse it later;
  saying so before the window opens costs nothing.

---

## The core idea

A click never "changes the chart". A click creates a **Context**:

```python
Context(dataset="sales", metric="revenue",
        filters={"region": "Luzon"}, grain=("province",),
        time=TimeWindow("2026-08", "2026-08", "month"), comparison="mom",
        lineage=[...])
```

A Context is the whole analytical question, and it is the contract every layer
speaks: **Metric + Scope + Grain + Time + Comparison + Provenance**.

| Field | Meaning |
|---|---|
| `dataset` | Which model this node belongs to; a mismatch is rejected. |
| `metric` | One metric name from the model. Never an expression. |
| `filters` | The scope — the dimension members pinned to a single value. Sorted, so two paths to the same scope are the same node. |
| `grain` | What the value is broken down *by*: a dimension name, the time column, or `()` for the scope total. |
| `time` | A `TimeWindow(start, end, grain)` of inclusive period keys. |
| `comparison` | `None`, or the name of a comparison the model declares (`mom`, `qoq`, `yoy`, …). |
| `lineage` | The audit trail of how this node was reached — deliberately **excluded** from identity. |

Two properties fall out of that shape and carry a lot of weight:

* **Immutability.** `Context` is a frozen dataclass; `evolve()` returns a copy
  with one more lineage step. Nothing is ever mutated, so competing hypotheses
  stay alive side by side.
* **Fingerprint identity.** `ctx.id` is a SHA-1 of the analytical state *without*
  lineage. Two contexts reached by different routes that mean the same thing
  share an id — and therefore share a cache entry.

Operations are typed edges from one Context to another, and every one of them
returns a *new* node:

```python
ctx2 = apply(model, ctx1, Operation.of("drill_down", dimension="province",
                                       member="Luzon"))
ctx3 = apply(model, ctx2, Operation.of("compare", period="mom"))
exp  = explain(engine, model, ctx3)
```

## The chart stack

Because an operation adds a node rather than replacing one, the chart surface
is a stack rather than a slot:

```
+-----------------------------------------------------+
| Revenue by Region                     Explain   Pin |  <- the node in focus
|   Luzon    ####################################     |     full size, on top
|   Visayas  ######                                   |
+-----------------------------------------------------+

CHILDREN CHARTS   2 views drilled out of the chart above

+---------------------------+  +---------------------------+
| Luzon - by Province  ^  x |  | Visayas - by Province ^ x |  <- two up, small
|   Metro Manila  ######### |  |   Cebu           ######## |
|   Cavite        ######    |  |   Negros Occ.    ###      |
+---------------------------+  +---------------------------+
```

* Drilling from the top chart opens the result **below** it, under a
  *Children charts* heading. The mother stays where it is, and the children
  sit two up so drills from the same chart read as one set.
* **Raise to top** promotes a chart below to the focus slot; it then spawns its
  own children underneath, and `↑ Parent` walks back up.
* Acting on a lower chart (right-click, double-click) raises it first, so an
  operation always applies to the chart on top.
* Operations that only re-frame the same scope — a different metric, period or
  comparison — replace the top chart instead of stacking under it.
* The same drill twice scrolls to the chart that already exists rather than
  stacking a duplicate; `x` closes a chart and everything drilled out of it.

## Layers

| Layer | Module | Responsibility |
|---|---|---|
| Interaction | `ui/` | Mark selection, context menus, breadcrumbs, branching, the chart stack |
| Analytical grammar | `core/operations.py` | Typed verbs over Contexts |
| Calendar | `core/timegrain.py` | Day / week / month / quarter / year period maths |
| Semantic model | `semantic/specs.py` | Validates grain, metrics, hierarchies — and *advertises* what is legal |
| Model authoring | `semantic/loader.py` | YAML → model, validation against real data, scaffolding |
| Dataset catalogue | `semantic/catalog.py` | Which models exist, and the scope one is opened under |
| Data | `data/source.py` | Reads a team's file, normalises the time axis |
| Analytical engine | `engine/engine.py` | Deterministic computation, result handles, caching |
| Explain engine | `engine/explain.py` | Ranked, deterministic evidence |
| Investigation graph | `core/graph.py` | Nodes, edges, branches, pins, history |
| Renderer | `view/vega.py` | Result → Vega-Lite spec |
| Narration | `engine/narrate.py` | The AI seam — numbers already decided |

The dependency arrow points one way: `ui → core/engine → semantic`. The engine
has no idea a GUI exists, which is why the whole framework is testable without
Qt — 106 tests, none of which open a window.

---

## The analytical grammar

Thirteen verbs in [`core/operations.py`](explain_beaucoup/core/operations.py),
plus `branch` and `pin` at the graph level and `explain` handled by the UI.
Every verb takes the current Context and returns a new one, or raises
`SemanticError` — the analysis is blocked *before* a chart or a narrative
exists.

`apply()` is the only entry point, and it ends with `model.validate(new)`, so
no verb can produce a node the model would reject.

### Composition — what is this made of?

| Verb | Params | What it does |
|---|---|---|
| `drill_down` | `dimension`, `member?` | Moves one level **down a declared hierarchy**: Region → Province. The `dimension` must be the structural child of the current grain, or the operation is refused by name (`'City' is not the structural child of 'Region' (expected 'Province')`). With a `member`, it also narrows the scope to that member first, which is what clicking a bar means. |
| `decompose` | `dimension`, `member?` | Breaks the same population down by a **different, unrelated** dimension: Luzon → by Channel. Free of hierarchy constraints, so this is the lateral move — but the menu only offers dimensions the model says are meaningful here. |
| `drill_up` | — | Steps back up the hierarchy, dropping the filter that pinned the parent level. At the top level it goes to the scope total (`grain=()`). Refused with "Already at the top of this path" when there is no grain at all. |
| `focus` | `dimension`, `member` | Narrows the scope but **keeps the grain** — "show me only this member", as opposed to "break this member down". |

Both drills and `decompose` share one subtlety worth knowing. If the current
grain is the *time* column, the member you clicked is a period, not a
dimension member — so it re-keys `ctx.time` instead of adding a filter. Time
lives in `ctx.time`, never in `ctx.filters`. Dropping that distinction would
break the whole plotted window down by the new dimension, which is not what
clicking one point on a line means.

### Change — what moved, and against what?

| Verb | Params | What it does |
|---|---|---|
| `compare` | `period` | Attaches a reference window (`mom`, `qoq`, `yoy`, or whatever the model declares) so every row gains `prior`, `delta` and `delta_pct`. Refused if the shifted window would overlap the window being analysed — see *Semantic safety*. |
| `clear_comparison` | — | Drops the reference window again. |
| `change_contribution` | `member?` | Re-reads the node as **share of the parent's movement**: each child's delta as a percentage of the total delta. Shares can exceed 100% or go negative, which is the point — that is how you find the mover hiding behind a flat headline. Requires a dimensional grain, and a comparison — if none is set it attaches `mom`, so the model must declare a comparison under that name. |

### Trend and time

| Verb | Params | What it does |
|---|---|---|
| `trend` | `periods?` (default 12), `member?`, `grain?` | Holds the scope fixed and switches the grain to the time column, over the trailing *n* periods ending at the current window. With a `member`, it filters to that member first — "show me this one bar's history". |
| `set_time` | `start`, `end`, `grain?` | Sets the window explicitly. |
| `set_grain` | `grain`, `end?` | Re-buckets the time axis (month → quarter → year). Rejected if the grain is not in `model.grains`. Because re-keying a window can run past the end of the data — a year window becomes twelve months, most of them empty — the caller passes the last period that actually has data and the window is clamped to it. |

### Reading the same node differently

| Verb | Params | What it does |
|---|---|---|
| `exceptions` | `member?` | Ranks the children that are unusual **against their own history**, not against each other: a z-score per member versus its own trailing 12 periods. A member needs at least 4 historical points to qualify. Requires a dimensional grain. |
| `distribution` | `member?` | Drops from the aggregate to the **population underneath it** — one point per fact cell, plus n / p10 / median / p90. What a "cell" is comes from the model (`cell: true`, else the leaf of each hierarchy plus every standalone dimension), never from this module. |
| `switch_metric` | `metric` | The same population, a different measure: Revenue → Volume → Margin %. Metrics that declare the current grain invalid are not offered. |

### The five result families

`result_kind()` decides which family a node is read as, and the renderer maps
each one to a view. This is why the same Context can be a bar chart or a line
chart without either the Context or the engine knowing about charts.

| Kind | Produced by | Shape |
|---|---|---|
| `breakdown` | the default | One row per dimension member, sorted by value; `share` of the scope total on additive metrics. |
| `timeseries` | any node whose grain is the time column | One row per period in the window, in calendar order, with partial edge periods flagged. |
| `change_contribution` | `change_contribution` | A breakdown read as signed deltas — diverging colour, sorted by movement. |
| `exceptions` | `exceptions` | Members ranked by `abs(z)` against their own baseline, carrying `z`, `baseline`, `baseline_periods`. |
| `distribution` | `distribution` | One row per fact cell, with quantile notes. |

### Graph-level operations

These act on the investigation graph rather than on a Context, so they are not
verbs in `apply()`:

* **`branch`** — opens a competing hypothesis *beside* the current path instead
  of continuing it. Branch numbers are what let the report print "Competing
  hypotheses" rather than one linear story.
* **`pin`** — marks a node as a finding, with an optional note. Pins survive
  navigation and land in the exported report.
* **`explain`** — runs the explain engine over the node in focus. Handled
  directly by the UI (it produces evidence, not a new node).

### Three distinct kinds of drill

As the plan distinguishes them — and the distinction is enforced, not just
documented:

* **Structural** — Country → Region → Province → City. Follows the hierarchy
  you declared; `drill_down` refuses anything else.
* **Dimensional** — Luzon → by Product / Channel / Segment. `decompose`, with
  candidates supplied by `alternative_dimensions()`.
* **Metric** — Revenue → Volume → Margin % over the same population.
  `switch_metric`, filtered by each metric's `invalid_grains`.

---

## Semantic safety

The model blocks nonsensical analysis *before* a chart or narrative exists, and
the same metadata generates the right-click menu — so the UI can never offer an
operation the model would reject. `capabilities(ctx)` and `validate(ctx)` read
the same declarations, which is what keeps the menu and the validator from
drifting apart.

* A **ratio metric** is declared from components and recomputed at every grain:
  the engine sums the numerator and the denominator, *then* divides. It is
  never summed and never averaged. Share-of-total is withheld on ratios as
  meaningless, and the result carries a note saying so.
* **Structural drill must follow the hierarchy** — and hierarchy walks step
  *past* single-member levels rather than stalling on them, so a one-country
  dataset does not hide its whole geography hierarchy.
* **`change_contribution` and `exceptions` require a dimensional grain.** At
  the total or time grain there are no children to attribute the movement to,
  and the error says which case you are in and what to do about it.
* **A comparison whose reference window would overlap the window being
  analysed is refused** — on aggregates. On a time series the comparison is
  applied per point, so an overlapping shifted window is fine and is allowed.
* **Single-member levels are never offered as a breakdown.** `profile()` learns
  member cardinality from the governed data, and a level with one member is a
  real hierarchy level but a useless breakdown.
* **Time grains finer than the data are rejected**; re-keying a window clamps
  to the last period that actually has data; and partial periods at the edge of
  the data are marked on the chart. Saying so is the difference between "sales
  collapsed" and "the week isn't over".
* Unknown metrics, unknown grains, filters on unknown dimensions and
  cross-dataset contexts are all refused by name rather than by exception type.

### The calendar

Not month-only. A period is identified by a **key whose text sorts
chronologically**, so a window is filtered with a plain string comparison at
every grain:

| Grain | Key | Notes |
|---|---|---|
| `day` | `2026-08-14` | |
| `week` | `2026-W33` | ISO week |
| `month` | `2026-08` | |
| `quarter` | `2026-Q3` | |
| `year` | `2026` | |

Comparisons are expressed as **calendar offsets** (`periods`, `months`,
`years`) rather than as a number of rows, which is what lets "same period last
year" mean the right thing whether the node is at day, week, month or quarter
grain.

### What the engine returns

Every computation returns a `ResultHandle` — a reference to a deterministic
output that carries its own provenance. The UI and any AI layer pass Contexts
and receive handles; they never touch a DataFrame.

| Field | Meaning |
|---|---|
| `rows` | `Row(key, label, value, prior, delta, delta_pct, share, numerator, denominator, extra)` |
| `total` / `prior_total` / `delta_total` | The scope aggregate, recomputed — not a sum of the rows |
| `fact_rows` | How many source rows were in scope |
| `provenance` | The equivalent SQL, with the ratio rule visible in the SELECT |
| `notes` | Ratio recomputation, partial periods, cell definition, baseline length |
| `computed_ms` | Wall time, for the inspector |

Results are cached on `(fingerprint, kind)`. Because the fingerprint excludes
lineage, arriving at the same analytical state by a different route is a cache
hit rather than a recomputation.

---

## "Explain this"

Deterministic signals, scored and ranked. If the node has no comparison, the
engine attaches the model's first *valid* declared comparison rather than a
hardcoded period — explaining a value means explaining its movement. It then
explains the **scope aggregate**, not the split currently on screen, and tests
the dimension already in front of you first, followed by the alternatives the
model allows (up to four).

| Signal | Weight | What it looks for |
|---|---|---|
| `change_contribution` | 1.00 | Children moving the same way as the parent, each carrying ≥12% of the movement. |
| `offset` | 0.92 | Children moving the **opposite** way by ≥15% of the parent's delta — the masking that makes a flat headline misleading. Reports what the total would have been without them. |
| `metric_relationship` | 0.75 | A volume-vs-rate split, `dV = dVolume × rate_prev + volume_now × dRate`. Offered **only** where the model declares `kind: volume_rate`; the engine never guesses that two columns are related. |
| `anomaly` | 0.80 | A member ≥2 sd from its **own** trailing 12-period baseline. |
| `new_member` / `missing_member` | 0.70 | Structural change in the population: a member with no prior-period base, or one that contributed last period and nothing now. |
| `concentration` | 0.55 | Few members carrying 80% of the value, with a Herfindahl index — the reason member-level moves dominate the aggregate. |
| `contribution` | 0.50 | The plain largest member, as a share of the total. Withheld on ratio metrics, where it means nothing. |

Weights are tuned so **"what moved" outranks "what is big"**, which is what
people actually ask when something surprises them. Scores also scale with
magnitude, so a signal has to be both the right *kind* and large to lead.

Alongside the ranked evidence, the engine reports a **best dimension**: the one
where the movement is least evenly spread (`max |delta| / sum |delta|`). The
dimension that best *explains* a change is the lopsided one.

On the demo data, Aug 2026 revenue is down only **1.3%**, and the engine
explains why that headline is misleading:

```
1.14 [change_contribution] Payments accounts for 319% of the decrease
1.12 [offset             ] Lending moved the other way (+1.78M), masking the fall
1.04 [change_contribution] Digital accounts for 174% of the decrease
0.80 [anomaly            ] Lending is 6.1 sd above its own 12-month baseline
0.75 [metric_relationship] The move is mostly a volume effect
```

Every card is clickable, and clicking it *drills into* the member rather than
filtering to a single bar — landing on "revenue by category, filtered to
Payments" would be one bar, which is useless. So the target scopes to the
member and steps the grain down to whatever sits below it. An explanation is a
place you can go, not a claim you have to trust.

---

## The workspace

| Region | Contents |
|---|---|
| Toolbar | Metric, By (time grain), Period, Compare and **Lens** selectors, then Explain / Drill up / Back / Branch / Pin / Export / Dark mode |
| Left dock | **Map** — the investigation graph as a tree; **Pins** — saved findings |
| Right dock | **Explain** — ranked evidence cards; **Next** — suggested questions; **Context** — the node inspector with its provenance SQL; **Report** — the narrated investigation |
| Centre | Breadcrumbs, then the chart stack |

**Lenses** switch how the node in focus is read without changing what it is
about: Composition, Change, Trend, Exception, Distribution.

**Gestures on a mark** — click selects; double-click takes the most natural
drill from here (the structural child if the hierarchy has one, otherwise the
first alternative dimension); right-click opens the grammar menu, grouped
Explain → Composition → Change → Trend → Exception → Distribution → Metric. A
bar, a table row and a KPI all expose the same grammar, and the menu is built
against whichever panel of the stack it came from.

| Shortcut | Action |
|---|---|
| `Ctrl+E` | Explain this |
| `Ctrl+↑` | Drill up |
| `Ctrl+[` | Back |
| `Ctrl+B` | Branch here |
| `Ctrl+P` | Pin |
| `Ctrl+S` | Export report |
| `Ctrl+D` | Dark mode |

The **Next** panel is generated from the semantic model, and each question
carries the verb and params it describes — so the text and the action can never
drift apart.

## Vega integration

Vega renders and emits; it never decides what an interaction means. A selected
mark emits exactly the payload the plan specifies:

```json
{"view_id": "n7", "context_id": "ctx_ec942d86b3acfa92",
 "gesture": "contextmenu", "mark": {"key": "Luzon", "value": 46551011}}
```

Python resolves that into a Context, asks the semantic model what is legal, and
builds the menu.

Charts follow one validated design system (`view/theme.py`): a single hue for
single-series magnitude, a blue↔red diverging pair for signed change, thin
marks with 4px data-ends and a 2px gap, recessive grid, a legend whenever there
are two series, direct labels with the axis domain padded so they never
collide, and a dark mode whose steps were selected for the dark surface rather
than flipped.

## The role of AI

There is **no LLM call in this codebase**, by design. The seam is built and the
contract enforced. The plan's explanation contract is:

```
AI request -> typed analytical operation -> deterministic result object -> narrative
```

`engine/narrate.py` implements the last arrow without a model: because the
numbers, the node ids and the operations are all already decided, the narrative
is a rendering problem. It receives `ResultHandle`s and `Evidence` — never raw
data — and its output cites node and context ids, which is what makes it
checkable.

An AI sidecar would replace the narration functions only. It could choose among
operations the semantic model already validated, but it could never aggregate a
ratio at an invalid grain or do arithmetic of its own.

## Roadmap status

| Phase | Plan | State |
|---|---|---|
| 0 | Semantic prototype | Done — declarative, any dataset |
| 1 | Context + drill + breadcrumbs | Done |
| 2 | Compare + trend | Done, at any calendar grain |
| 3 | Explain engine | Done |
| 4 | Branching graph | Done (branch, pin, revisit, report) |
| 5 | AI sidecar | Seam + contract built; no model wired |
| 6 | Dashboard builder | Not started — deliberately, per the plan |

## Tests

```bash
.venv/bin/python -m pytest tests/ -q     # 106 tests, no GUI required
```

`tests/test_framework.py` covers the guarantees: ratio recomputation, semantic
blocking, context immutability and fingerprint sharing, engine results against
the raw frame, the planted story in the explain engine, branch/pin
preservation, and the charting invariants.

`tests/test_catalog.py` covers the layer in front of all that: a folder of
models described without reading their data, a broken model reported rather than
dropped, and a scope that re-profiles cardinality, keeps the model it came from
untouched, and is refused when it would leave nothing to analyse.

`tests/test_portability.py` sets up a second, unrelated dataset from scratch —
clinic appointments, daily `dd/mm/yyyy` dates, £ costs, its own hierarchy — and
drives it through the same path a new team follows, including a planted anomaly
the explain engine finds without being told it exists.
