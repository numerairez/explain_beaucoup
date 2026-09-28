# Explain Beaucoup

An implementation of the *Interactive Analytical Dashboard Framework* design
plan: a dashboard treated not as charts linked by filters, but as an
**explorable graph of analytical contexts**.

* **UI** — PyQt6 (with QtWebEngine hosting the chart surface)
* **Charts** — Vega-Lite 6, rendered locally (vendored; no network at runtime)
* **Numbers** — a deterministic pandas engine. Nothing else computes a value.

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/explain-beaucoup run models/sales.yaml       # the bundled demo
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

---

## The core idea

A click never "changes the chart". A click creates a **Context**:

```python
Context(dataset="sales", metric="revenue",
        filters={"region": "Luzon"}, grain=("province",),
        time=TimeWindow("2026-08", "2026-08", "month"), comparison="mom",
        lineage=[...])
```

Operations are typed edges from one Context to another, and every one of them
returns a *new* node — nothing is mutated, so competing hypotheses stay alive
side by side.

```python
ctx2 = apply(model, ctx1, Operation.of("drill_down", dimension="province",
                                       member="Luzon"))
ctx3 = apply(model, ctx2, Operation.of("compare", period="mom"))
exp  = explain(engine, model, ctx3)
```

## Layers

| Layer | Module | Responsibility |
|---|---|---|
| Interaction | `ui/` | Mark selection, context menus, breadcrumbs, branching |
| Analytical grammar | `core/operations.py` | Typed verbs over Contexts |
| Calendar | `core/timegrain.py` | Day / week / month / quarter / year period maths |
| Semantic model | `semantic/specs.py` | Validates grain, metrics, hierarchies — and *advertises* what is legal |
| Model authoring | `semantic/loader.py` | YAML → model, validation against real data, scaffolding |
| Data | `data/source.py` | Reads a team's file, normalises the time axis |
| Analytical engine | `engine/engine.py` | Deterministic computation, result handles, caching |
| Explain engine | `engine/explain.py` | Ranked, deterministic evidence |
| Investigation graph | `core/graph.py` | Nodes, edges, branches, pins, history |
| Renderer | `view/vega.py` | Result → Vega-Lite spec |
| Narration | `engine/narrate.py` | The AI seam — numbers already decided |

The dependency arrow points one way: `ui → core/engine → semantic`. The engine
has no idea a GUI exists, which is why the whole framework is testable without
Qt — 86 tests, none of which open a window.

## The analytical grammar

`drill_down` · `drill_up` · `decompose` · `focus` · `compare` · `trend` ·
`contribution` · `change_contribution` · `exceptions` · `distribution` ·
`switch_metric` · `set_time` · `set_grain` — plus `branch` and `pin` at the
graph level.

Three distinct kinds of drill, as the plan distinguishes them:

* **Structural** — Country → Region → City (follows your hierarchy)
* **Dimensional** — Luzon → by Product / Channel / Segment
* **Metric** — Revenue → Volume → Margin % over the same population

## Semantic safety

The model blocks nonsensical analysis *before* a chart or narrative exists, and
the same metadata generates the right-click menu — so the UI can never offer an
operation the model would reject:

* A ratio metric is declared from components and recomputed at every grain,
  never summed or averaged. Share-of-total is withheld as meaningless.
* Structural drill must follow the hierarchy.
* `change_contribution` / `exceptions` require a dimensional grain.
* A comparison whose reference window would overlap the window being analysed
  is refused — on aggregates. On a time series it is per-point, so it is fine.
* Single-member levels are never offered as a breakdown, without hiding the
  rest of their hierarchy.
* Time grains finer than the data are rejected; re-keying a window clamps to
  the last period that actually has data; partial periods at the edge of the
  data are marked on the chart.

## "Explain this"

Deterministic signals, scored and ranked — contribution, change contribution,
offsetting contributors, concentration, deviation from each member's own
trailing baseline, new/missing members, and a declared volume-vs-rate split.

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
filtering to a single bar — an explanation is a place you can go, not a claim
you have to trust.

## Vega integration

Vega renders and emits; it never decides what an interaction means. A selected
mark emits exactly the payload the plan specifies:

```json
{"view_id": "n7", "context_id": "ctx_ec942d86b3acfa92",
 "gesture": "contextmenu", "mark": {"key": "Luzon", "value": 46551011}}
```

Python resolves that into a Context, asks the semantic model what is legal, and
builds the menu. A bar, a table row and a KPI all expose the same grammar.

Charts follow one validated design system (`view/theme.py`): a single hue for
single-series magnitude, a blue↔red diverging pair for signed change, thin
marks with 4px data-ends and a 2px gap, recessive grid, a legend whenever there
are two series, direct labels with the axis domain padded so they never
collide, and a dark mode whose steps were selected for the dark surface rather
than flipped.

## The role of AI

There is **no LLM call in this codebase**, by design. The seam is built and the
contract enforced: `engine/narrate.py` receives `ResultHandle`s and `Evidence`
— never raw data — and its output cites node and context ids. An AI sidecar
would replace the narration functions only; it could choose among operations
the semantic model already validated, but it could never aggregate a ratio at
an invalid grain or do arithmetic of its own.

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
.venv/bin/python -m pytest tests/ -q     # 86 tests, no GUI required
```

`tests/test_framework.py` covers the guarantees: ratio recomputation, semantic
blocking, context immutability and fingerprint sharing, engine results against
the raw frame, the planted story in the explain engine, branch/pin
preservation, and the charting invariants.

`tests/test_portability.py` sets up a second, unrelated dataset from scratch —
clinic appointments, daily `dd/mm/yyyy` dates, £ costs, its own hierarchy — and
drives it through the same path a new team follows, including a planted anomaly
the explain engine finds without being told it exists.
