# Setting up your own data

Everything the framework does — the drill paths, the right-click menu, what
counts as a valid aggregation, the ranked explanations — is generated from one
file you write: a **semantic model**. This is what setting up looks like.

```bash
pip install -e .                                  # or: pip install explain-beaucoup
#   needs Python 3.10 or 3.11 - PyQt5 5.15.7 has no wheels for 3.12+

explain-beaucoup init  orders.csv --time order_date # 1. propose a model
$EDITOR orders.yaml                               # 2. fill in what only you know
explain-beaucoup check orders.yaml --sample         # 3. verify against your data
explain-beaucoup run   orders.yaml                  # 4. open the workspace
```

Steps 1, 3 and 4 take seconds. Step 2 is the real work: **30–60 minutes** for a
table you know well, and it is mostly deciding drill paths.

---

## 1. What your data has to look like

One table, one row per fact, **long format**.

| order_date | country | channel | category | units | gross_sales | discount |
|---|---|---|---|---|---|---|
| 2026-03-01 | UK | Email | Footwear | 8 | 940.00 | 61.20 |
| 2026-03-01 | UK | Organic | Outerwear | 3 | 612.50 | 22.10 |

**Required**

- **One time column.** Real dates, ISO strings, `'2026-08'` period strings,
  `14/08/2026` layouts, or bare years — all parse. Mixed layouts in one column
  do not; set `time.format` if detection fails.
- **At least one numeric measure column** (`gross_sales`, `units`).
- **At least one dimension column** with repeated values (`country`, `channel`).

**Formats read:** `.csv`, `.csv.gz`, `.tsv`, `.parquet`, `.json`, `.ndjson`,
`.xlsx`, `.feather`. Parquet/feather need `pyarrow`; Excel needs `openpyxl`.

### Three shapes that need work first

| Your data | Problem | Fix before you start |
|---|---|---|
| One column per month (`jan_sales`, `feb_sales`) | Wide format | `df.melt()` into one date column + one value column |
| Already aggregated to the level you want to drill into | You cannot drill below your rows | Export at the finest grain you want to reach |
| A stored ratio column (`margin_pct`, `conversion_rate`) | **Summing it is wrong at every grain** | Keep the numerator and denominator columns and declare a `ratio` metric — see below |

That third one is the single most common mistake, and the framework is built to
prevent it — but only if you declare the metric properly.

---

## 2. `init` — what it guesses, and what it refuses to

```
$ explain-beaucoup init webshop_orders.csv
Read webshop_orders.csv: 14,377 rows x 10 columns

  time       order_date                   182 distinct   name and values look temporal
  dimension  country                        2 distinct   2 distinct values
  dimension  marketing_channel              5 distinct   5 distinct values
  measure    gross_sales                13634 distinct   numeric
  skipped    order_id                   14377 distinct   too high to group by
```

It guesses roles from dtype and cardinality and writes a starter model. It
**never guesses hierarchies** — drill paths are the one thing only you know,
and a wrong guess would produce confidently wrong analysis.

---

## 3. The model file

```yaml
dataset: webshop
title: Webshop Orders

data:
  path: webshop_orders.csv     # relative to this file

time:
  column: order_date
  grain: day                   # the FINEST grain in your data
  format: "%d/%m/%Y"           # only if detection fails
  grains: [day, week, month, quarter]   # what the UI offers

dimensions:
  country:           {label: Country, column: country}
  uk_region:         {label: Region,  column: uk_region}
  category:          {label: Category, column: category}
  sku:               {label: SKU, column: sku, cell: true}
  marketing_channel: {label: Channel, column: marketing_channel, cell: true}

hierarchies:                   # drill paths, COARSE to FINE
  geography: {label: Geography, levels: [country, uk_region]}
  product:   {label: Product,   levels: [category, sku]}

metrics:
  gross_sales:
    label: Gross Sales
    kind: additive             # additive | count | ratio | semi_additive
    format: currency           # currency | percent | number
    symbol: "£"
    column: gross_sales
  orders: {label: Orders, kind: count, format: number, column: orders}

  discount_rate:               # a ratio is declared from its components
    label: Discount Rate
    kind: ratio
    format: percent
    numerator: discount        # summed
    denominator: gross_sales   # summed, then divided
    higher_is_better: false

comparisons:
  - {name: prev, label: "the previous {grain}", periods: 1}
  - {name: yoy,  label: the same period last year, years: 1}

relationships:                 # optional but high value
  - {kind: volume_rate, metric: gross_sales, volume: orders, rate: aov,
     volume_noun: orders, rate_noun: average order value}

defaults:
  metric: gross_sales
  grain: [marketing_channel]   # the landing view
```

### Key reference

| Key | Meaning |
|---|---|
| `time.grain` | The finest grain in your data. Asking for a finer one than you have produces empty periods. |
| `time.grains` | Which grains the UI offers. Only the native grain and coarser are valid. |
| `dimensions.*.cell` | Marks the finest addressable cell — what a distribution is a population *of*. Defaults to each hierarchy's leaf plus every standalone dimension. |
| `hierarchies.*.levels` | Coarse to fine. This is what enables structural drill (Country → Region). Without it, dimensional breakdown still works. |
| `metrics.*.kind: ratio` | Recomputed from components at every grain, never summed. Share-of-total is withheld because it is meaningless for a ratio. |
| `metrics.*.higher_is_better` | Whether a rise is good. Drives the up/down colouring. |
| `comparisons.*` | `periods` (of the context's own grain), `months`, or `years`. A comparison that would overlap the window being analysed is refused. |
| `relationships` | Declares `metric = volume x rate`, which unlocks volume-vs-rate decomposition in the explain engine. It is never assumed. |
| `defaults` | The landing metric and breakdown. |

> **YAML gotcha:** `"the previous {grain}"` must be quoted in flow style, or
> YAML reads `{grain}` as a mapping.

---

## 4. `check` — what it tells you

```
$ explain-beaucoup check webshop.yaml --sample
Model webshop.yaml  (webshop: 5 dimensions, 5 metrics, 2 hierarchies)
Data  14,377 rows, day-grain, grains available: day, week, month, quarter

  [INFO   ] metrics.aov: denominator 'orders' is zero in 975 rows; those cells
            return no value rather than infinity
  [INFO   ] time: 14,377 rows spanning 182 day(s): 2025-10-01 to 2026-03-31

OK — model is usable.
```

| Message | What to do |
|---|---|
| `column 'x' is not in the data` | Typo, or the column is named differently in the file |
| `column 'x' is int64, not numeric`… | A measure column is stored as text — clean it upstream |
| `only 1 distinct value(s)` | Fine. It stays usable as a filter, just never offered as a breakdown |
| `N distinct values - charts will be truncated` | Add a parent level and put both in a hierarchy |
| `'child' has fewer members than its parent` | Your hierarchy levels are probably reversed |
| `not enough history for a year-on-year comparison` | Drop `yoy`, or accept that it will be unavailable |
| `no dimension has more than one member` | The data is a single cell — nothing to analyse |

`--sample` computes a real first view and a real explanation, so you can tell
whether the model is *right*, not just loadable.

---

## 5. What you get once it loads

Nothing below is configured — it all follows from the model:

- Right-click menu built from what your metadata says is legal
- Structural drill along your hierarchies, dimensional breakdown across them,
  and metric drill over the same population
- MoM/QoQ/YoY-style comparisons at any grain, with overlap refused
- "Explain this": contribution, change contribution, offsetting movers,
  concentration, deviation from each member's own baseline, new and missing
  members, and volume-vs-rate if you declared it
- An investigation graph with branches, pins and an exportable report
- Partial periods at the edge of your data marked on the chart and in the notes

---

## 6. Limits worth knowing before you commit

- **In-memory pandas.** Comfortable to a few million rows. Beyond that, the
  seam to push down to SQL is `AnalyticalEngine._scope` and `._aggregate` —
  they are the only two methods that touch data.
- **One table.** No joins across fact tables. Pre-join upstream.
- **No row-level security.** The semantic model has no permissions layer yet;
  the plan reserves one. Today, filter the file before handing it over.
- **No saved layouts.** Investigations export as Markdown; they are not
  persisted as objects.
- **One dataset per model file.** Multiple models = multiple files.

---

## 7. A worked example

`models/sales.yaml` is the bundled demo, and `tests/test_portability.py` sets
up a second, unrelated dataset (clinic appointments, daily `dd/mm/yyyy` dates,
£ costs, its own hierarchy) from scratch in about 40 lines of YAML — including
a planted anomaly the explain engine finds without being told about it. It is
the shortest honest description of what your own set-up will look like.
