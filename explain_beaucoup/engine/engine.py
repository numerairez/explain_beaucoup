"""The analytical engine: deterministic computation over governed data.

The engine owns every number. The UI and any AI layer pass Contexts and
receive ResultHandles - they never compute values themselves.

Nothing here knows the name of a single column, or where the data lives: the
semantic model supplies the columns, and a `Backend` answers one primitive -
sum these components, in this scope, grouped by these keys. Every result
family is an `Analysis` in the `ANALYSES` registry, built on that primitive.
"""

from __future__ import annotations

import dataclasses
import time as _time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

import pandas as pd

from ..core import timegrain as tg
from ..core.context import Context, TimeWindow, cell_key
from ..core.operations import (BREAKDOWN, CHANGE, DISTRIBUTION, EXCEPTIONS,
                               MATRIX, TIMESERIES)
from ..data.source import DataSource
from ..semantic.specs import MetricSpec, SemanticModel
from .backend import N, PERIOD, Backend, Query, as_backend

VALUE, NUM, DEN = "__value__", "__num__", "__den__"


def comparison_label(model: SemanticModel, ctx: Context) -> str:
    """How to describe this node's reference period, at its own grain."""
    if not ctx.comparison:
        return ""
    return model.comparison(ctx.comparison).label_for(ctx.time.grain)


@dataclass(frozen=True)
class Row:
    key: str                      # member value, period key, or cell key
    label: str
    value: float | None
    prior: float | None = None
    delta: float | None = None
    delta_pct: float | None = None
    share: float | None = None    # of the scope total (additive metrics only)
    numerator: float | None = None
    denominator: float | None = None
    extra: tuple[tuple[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        d = {"key": self.key, "label": self.label, "value": self.value,
             "prior": self.prior, "delta": self.delta,
             "delta_pct": self.delta_pct, "share": self.share}
        d.update(dict(self.extra))
        return d


@dataclass(frozen=True)
class ResultHandle:
    """A reference to a deterministic output. Carries its own provenance."""

    id: str
    context: Context
    kind: str
    metric: MetricSpec
    dimension: str | None
    rows: tuple[Row, ...]
    total: float | None
    prior_total: float | None
    delta_total: float | None
    fact_rows: int
    provenance: str
    computed_ms: float = 0.0
    notes: tuple[str, ...] = field(default=())
    # Every axis the rows are keyed by; `dimension` is the first of them.
    axes: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.rows

    def row(self, key: str) -> Row | None:
        for r in self.rows:
            if r.key == key:
                return r
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id": self.id, "context_id": self.context.id,
            "kind": self.kind, "metric": self.metric.name,
            "dimension": self.dimension, "axes": list(self.axes),
            "total": self.total, "prior_total": self.prior_total,
            "rows": [r.to_dict() for r in self.rows],
            "provenance": self.provenance,
        }


# --------------------------------------------------------------------------
# The analysis registry
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Analysis:
    """One family of result: how it is computed and how a node of it is
    titled. How it is *drawn* is the view layer's business (`view.vega`)."""

    kind: str
    compute: Callable[["AnalyticalEngine", Context], ResultHandle]
    title: Callable[[SemanticModel, Context], str]


ANALYSES: dict[str, Analysis] = {}


def register_analysis(kind: str, *, title: Callable[[SemanticModel, Context], str]
                      ) -> Callable:
    """Decorator: register `fn(engine, ctx) -> ResultHandle` as a result kind."""
    def deco(fn: Callable[["AnalyticalEngine", Context], ResultHandle]):
        ANALYSES[kind] = Analysis(kind, fn, title)
        return fn
    return deco


def title_for(model: SemanticModel, ctx: Context, kind: str) -> str:
    a = ANALYSES.get(kind) or ANALYSES[BREAKDOWN]
    return a.title(model, ctx)


def normal_kind(model: SemanticModel, ctx: Context, kind: str) -> str:
    """A two-axis node can only be read as a matrix."""
    return MATRIX if len(ctx.grain) == 2 else kind


# --------------------------------------------------------------------------

class AnalyticalEngine:
    def __init__(self, data: Backend | DataSource | pd.DataFrame,
                 model: SemanticModel) -> None:
        self.backend: Backend = as_backend(data, model)
        self.model = model
        self._cache: dict[str, ResultHandle] = {}

    # -- the data, for callers that need its calendar -----------------------

    @property
    def source(self) -> DataSource | None:
        """The in-memory source, when the backend has one."""
        return getattr(self.backend, "source", None)

    @property
    def df(self) -> pd.DataFrame:
        return self.backend.frame                 # type: ignore[attr-defined]

    def latest(self, grain: tg.Grain) -> str:
        return self.backend.latest(grain)

    def periods(self, grain: tg.Grain) -> list[str]:
        return self.backend.periods(grain)

    def time_bounds(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        return self.backend.time_bounds()

    @property
    def native_grain(self) -> tg.Grain:
        return self.backend.native_grain

    # -- the primitive, in model terms --------------------------------------

    def query(self, ctx: Context, window: TimeWindow, metric: MetricSpec,
              by: Sequence[str] = ()) -> Query:
        """A backend query for `metric` in `ctx`'s scope over `window`,
        grouped by axes named in model terms (dimensions or the time
        column)."""
        cols = tuple(c for c in metric.components() if c)
        group = tuple(PERIOD if a == self.model.time_column
                      else self.model.dim(a).column for a in by)
        filters = tuple((self.model.dim(d).column, m) for d, m in ctx.filters)
        ratio = ((metric.name, metric.numerator, metric.denominator)
                 if metric.is_ratio else None)
        return Query(cols, window, filters, group, ratio, metric.name)

    def aggregate(self, ctx: Context, window: TimeWindow, metric: MetricSpec,
                  by: Sequence[str] = ()) -> pd.DataFrame:
        """Sum the *components*, then (for ratios) divide. A ratio is never
        summed and never averaged - the plan's margin_pct rule, enforced.

        Group keys come back as `__k0__`, `__k1__`, ... in the order of `by`.
        """
        q = self.query(ctx, window, metric, by)
        out = self.backend.aggregate(q)
        out = out.rename(columns={g: f"__k{i}__"
                                  for i, g in enumerate(q.group_by)})
        if metric.is_ratio:
            num, den = metric.numerator, metric.denominator
            out[VALUE] = [(n / d) if d else None
                          for n, d in zip(out[num], out[den])]
            out[NUM] = out[num]
            out[DEN] = out[den]
        else:
            out[VALUE] = out[metric.column]
            out[NUM] = None
            out[DEN] = None
        return out

    def total(self, ctx: Context, window: TimeWindow, metric: MetricSpec
              ) -> tuple[float | None, int]:
        """The scope aggregate - recomputed, never a sum of rows - and how
        many fact rows went into it."""
        t = self.aggregate(ctx, window, metric)
        if not len(t):
            return None, 0
        return _f(t[VALUE].iloc[0]), int(t[N].iloc[0])

    def prior_window(self, ctx: Context) -> TimeWindow | None:
        if not ctx.comparison:
            return None
        return self.model.comparison(ctx.comparison).shift(ctx.time)

    def provenance(self, ctx: Context, metric: MetricSpec,
                   by: Sequence[str] = (), window: TimeWindow | None = None
                   ) -> str:
        return self.backend.sql(self.query(ctx, window or ctx.time, metric, by))

    # -- public ------------------------------------------------------------

    def execute(self, ctx: Context, kind: str = BREAKDOWN) -> ResultHandle:
        self.model.validate(ctx)
        kind = normal_kind(self.model, ctx, kind)
        cache_key = f"{ctx.fingerprint}:{kind}"
        hit = self._cache.get(cache_key)
        if hit is not None:
            return hit
        t0 = _time.perf_counter()
        analysis = ANALYSES.get(kind)
        if analysis is None:
            handle = dataclasses.replace(
                ANALYSES[BREAKDOWN].compute(self, ctx), kind=kind,
                id=f"res_{ctx.fingerprint}_{kind}")
        else:
            handle = analysis.compute(self, ctx)
        handle = dataclasses.replace(
            handle, computed_ms=round((_time.perf_counter() - t0) * 1000, 1))
        self._cache[cache_key] = handle
        return handle

    def cache_size(self) -> int:
        return len(self._cache)


# --------------------------------------------------------------------------
# Titles
# --------------------------------------------------------------------------

def _metric_label(model: SemanticModel, ctx: Context) -> str:
    return model.metric(ctx.metric).label


def _breakdown_title(model: SemanticModel, ctx: Context) -> str:
    if ctx.grain:
        return f"{_metric_label(model, ctx)} by {model.label_of(ctx.grain[0])}"
    return f"{_metric_label(model, ctx)} - {ctx.scope_label()}"


# --------------------------------------------------------------------------
# The built-in analyses
# --------------------------------------------------------------------------

def _keyed(frame: pd.DataFrame, col: str = "__k0__") -> dict[str, float | None]:
    return {str(k): _f(v) for k, v in zip(frame[col], frame[VALUE])}


@register_analysis(BREAKDOWN, title=_breakdown_title)
def breakdown(engine: AnalyticalEngine, ctx: Context,
              kind: str = BREAKDOWN) -> ResultHandle:
    model = engine.model
    metric = model.metric(ctx.metric)
    dim = ctx.grain[0] if ctx.grain else None
    by = (dim,) if dim else ()
    cur = engine.aggregate(ctx, ctx.time, metric, by)
    total, fact_rows = engine.total(ctx, ctx.time, metric)

    prior_map: dict[str, float | None] = {}
    prior_total = None
    pwin = engine.prior_window(ctx)
    if pwin is not None:
        if by:
            prior_map = _keyed(engine.aggregate(ctx, pwin, metric, by))
        prior_total, _ = engine.total(ctx, pwin, metric)

    # A member that is new this period (or gone since) moved from (or to)
    # nothing. For an additive metric that is a real movement and must be
    # counted, or the children stop summing to the parent's change; a
    # ratio has no value to move from, so it stays unknown.
    additive = pwin is not None and not metric.is_ratio
    rows: list[Row] = []
    if by:
        for _, r in cur.iterrows():
            key = str(r["__k0__"])
            rows.append(_row(key, key, _f(r[VALUE]), prior_map.get(key),
                             total, metric, additive,
                             _f(r[NUM]), _f(r[DEN])))
    elif len(cur):
        rows.append(_row("__total__", "__total__", total, prior_total, total,
                         metric, additive, _f(cur[NUM].iloc[0]),
                         _f(cur[DEN].iloc[0])))

    # Members that existed in the prior period but are gone now.
    seen = {r.key for r in rows}
    for key, prior in prior_map.items():
        if key not in seen:
            rows.append(Row(key, key, None, prior,
                            _delta(None, prior, additive), None, None,
                            extra=(("missing", True),)))

    rows.sort(key=lambda r: (r.value is None, -(r.value or 0.0)))
    delta_total = (total - prior_total) if (total is not None and
                                            prior_total is not None) else None
    notes: tuple[str, ...] = ()
    if metric.is_ratio:
        notes = (f"{metric.label} is recomputed as "
                 f"{metric.numerator}/{metric.denominator} at this grain, "
                 f"never summed.",)
    return ResultHandle(
        id=f"res_{ctx.fingerprint}_{kind}", context=ctx, kind=kind,
        metric=metric, dimension=dim, rows=tuple(rows), total=total,
        prior_total=prior_total, delta_total=delta_total,
        fact_rows=fact_rows, provenance=engine.provenance(ctx, metric, by),
        notes=notes, axes=by)


@register_analysis(CHANGE, title=lambda model, ctx:
                   f"What moved {_metric_label(model, ctx)} - "
                   f"{ctx.scope_label()}")
def change(engine: AnalyticalEngine, ctx: Context) -> ResultHandle:
    return breakdown(engine, ctx, CHANGE)


@register_analysis(TIMESERIES, title=lambda model, ctx:
                   f"{_metric_label(model, ctx)} by {ctx.time.grain} - "
                   f"{ctx.scope_label()}")
def timeseries(engine: AnalyticalEngine, ctx: Context) -> ResultHandle:
    model = engine.model
    metric = model.metric(ctx.metric)
    grain = ctx.time.grain
    time = model.time_column
    cur = engine.aggregate(ctx, ctx.time, metric, (time,))
    series = _keyed(cur)
    fact_rows = int(cur[N].sum()) if len(cur) else 0

    prior_series: dict[str, float | None] = {}
    pwin = engine.prior_window(ctx)
    if pwin is not None:
        prior_series = _keyed(engine.aggregate(ctx, pwin, metric, (time,)))
    spec = model.comparison(ctx.comparison) if ctx.comparison else None

    # A trailing window routinely ends mid-period. Saying so is the
    # difference between "sales collapsed" and "the week isn't over".
    data_min, data_max = engine.time_bounds()
    # Compare at the data's own grain: monthly rows are stamped on the
    # 1st, so August's last day is after the last timestamp even when
    # August is complete.
    native = engine.native_grain
    last_key = tg.key_of(data_max, native)
    first_key = tg.key_of(data_min, native)

    rows: list[Row] = []
    partials: list[str] = []
    periods = ctx.time.periods
    for i, key in enumerate(periods):
        value = series.get(key)
        if spec is not None:
            prior = prior_series.get(
                tg.shift_calendar(key, grain, periods=spec.periods,
                                  months=spec.months, years=spec.years))
        else:
            prior = series.get(periods[i - 1]) if i > 0 else None
        delta = (value - prior) if (value is not None and prior is not None) else None
        dpct = (delta / prior) if (delta is not None and prior) else None
        lo, hi = tg.bounds(key, grain)
        partial = value is not None and (
            tg.key_of(hi, native) > last_key
            or tg.key_of(lo, native) < first_key)
        if partial:
            partials.append(tg.label_of(key, grain))
        rows.append(Row(key, tg.label_of(key, grain), value, prior, delta,
                        dpct, extra=(("partial", bool(partial)),)))

    last = rows[-1].value if rows else None
    prev = rows[-2].value if len(rows) > 1 else None
    return ResultHandle(
        id=f"res_{ctx.fingerprint}_{TIMESERIES}", context=ctx,
        kind=TIMESERIES, metric=metric, dimension=time,
        rows=tuple(rows), total=last, prior_total=prev,
        delta_total=(last - prev) if (last is not None and prev is not None) else None,
        fact_rows=fact_rows,
        provenance=engine.provenance(ctx, metric, (time,)),
        notes=((f"{len([r for r in rows if r.value is not None])} "
                f"{grain}(s).",)
               + ((f"Partial {grain}(s) at the edge of the data: "
                   f"{', '.join(partials)} - the data ends "
                   f"{data_max:%d %b %Y}.",) if partials else ())),
        axes=(time,))


@register_analysis(DISTRIBUTION, title=lambda model, ctx:
                   f"Distribution - {ctx.scope_label()}")
def distribution(engine: AnalyticalEngine, ctx: Context) -> ResultHandle:
    """The population underneath the aggregate: one point per fact cell.

    What a "cell" is comes from the model, not from this module.
    """
    model = engine.model
    metric = model.metric(ctx.metric)
    cell_dims = [d for d in model.cell_dimensions() if d not in ctx.filter_map]
    g = (engine.aggregate(ctx, ctx.time, metric, cell_dims)
         if cell_dims else pd.DataFrame())
    _, fact_rows = engine.total(ctx, ctx.time, metric)
    rows: tuple[Row, ...] = ()
    vals: list[float] = []
    if len(g):
        g = g[g[VALUE].notna()]
        keys = [f"__k{i}__" for i in range(len(cell_dims))]
        rows = tuple(
            Row(key=" / ".join(str(r[k]) for k in keys),
                label=" / ".join(str(r[k]) for k in keys),
                value=_f(r[VALUE]))
            for _, r in g.iterrows())
        vals = sorted(r.value for r in rows if r.value is not None)

    notes: tuple[str, ...] = ()
    if vals:
        def q(p: float) -> float:
            return vals[min(int(p * (len(vals) - 1)), len(vals) - 1)]
        notes = (f"cell = {' x '.join(model.label_of(d) for d in cell_dims)}",
                 f"n={len(vals)}  p10={q(.1):,.0f}  median={q(.5):,.0f}  "
                 f"p90={q(.9):,.0f}")
    return ResultHandle(
        id=f"res_{ctx.fingerprint}_{DISTRIBUTION}", context=ctx,
        kind=DISTRIBUTION, metric=metric,
        dimension=ctx.grain[0] if ctx.grain else None, rows=rows,
        total=float(sum(vals)) if vals and not metric.is_ratio else None,
        prior_total=None, delta_total=None, fact_rows=fact_rows,
        provenance=engine.provenance(ctx, metric, cell_dims), notes=notes,
        axes=tuple(cell_dims))


@register_analysis(EXCEPTIONS, title=lambda model, ctx:
                   f"Exceptions - {ctx.scope_label()}")
def exceptions(engine: AnalyticalEngine, ctx: Context,
               baseline_periods: int = 12) -> ResultHandle:
    """Children that are unusual against their *own* history."""
    model = engine.model
    metric = model.metric(ctx.metric)
    dim = ctx.grain[0] if ctx.grain else None
    if dim is None or dim == model.time_column:
        return breakdown(engine, ctx, EXCEPTIONS)
    grain = ctx.time.grain

    cur_map = _keyed(engine.aggregate(ctx, ctx.time, metric, (dim,)))
    hist_win = TimeWindow(tg.add(ctx.time.start, grain, -baseline_periods),
                          tg.add(ctx.time.start, grain, -1), grain)
    hg = engine.aggregate(ctx, hist_win, metric, (dim, model.time_column))
    rows: list[Row] = []
    for key, grp in hg.groupby("__k0__", observed=True):
        vals = grp[VALUE].dropna()
        if len(vals) < 4:
            continue
        mean = float(vals.mean())
        std = float(vals.std(ddof=1)) or 0.0
        value = cur_map.get(str(key))
        if value is None:
            continue
        z = (value - mean) / std if std else 0.0
        rows.append(Row(str(key), str(key), value, mean, value - mean,
                        (value - mean) / mean if mean else None, None,
                        extra=(("z", round(z, 2)), ("baseline", mean),
                               ("baseline_periods", len(vals)))))
    rows.sort(key=lambda r: -abs(dict(r.extra).get("z", 0.0)))
    total, fact_rows = engine.total(ctx, ctx.time, metric)
    return ResultHandle(
        id=f"res_{ctx.fingerprint}_{EXCEPTIONS}", context=ctx,
        kind=EXCEPTIONS, metric=metric, dimension=dim, rows=tuple(rows),
        total=total, prior_total=None, delta_total=None, fact_rows=fact_rows,
        provenance=engine.provenance(ctx, metric, (dim,)),
        notes=(f"z-score against each member's own trailing "
               f"{baseline_periods} {grain}(s).",),
        axes=(dim,))


@register_analysis(MATRIX, title=lambda model, ctx:
                   f"{_metric_label(model, ctx)} by "
                   f"{model.label_of(ctx.grain[0])} × "
                   f"{model.label_of(ctx.grain[1])}")
def matrix(engine: AnalyticalEngine, ctx: Context) -> ResultHandle:
    """The value at every (row, column) cell of a two-axis node.

    Rows and columns each carry an order: dimension members by their total,
    periods by the calendar. With a comparison, each cell is compared with
    the same cell in the reference window - a time column shifts with it.
    """
    model = engine.model
    metric = model.metric(ctx.metric)
    axes = ctx.grain
    time = model.time_column
    cur = engine.aggregate(ctx, ctx.time, metric, axes)
    total, fact_rows = engine.total(ctx, ctx.time, metric)

    prior_map: dict[tuple[str, str], float | None] = {}
    prior_total = None
    pwin = engine.prior_window(ctx)
    spec = model.comparison(ctx.comparison) if ctx.comparison else None
    if pwin is not None and spec is not None:
        pri = engine.aggregate(ctx, pwin, metric, axes)
        prior_map = {(str(a), str(b)): _f(v) for a, b, v in
                     zip(pri["__k0__"], pri["__k1__"], pri[VALUE])}
        prior_total, _ = engine.total(ctx, pwin, metric)

    def shifted(i: int, key: str) -> str:
        if axes[i] != time or spec is None:
            return key
        return tg.shift_calendar(key, ctx.time.grain, periods=spec.periods,
                                 months=spec.months, years=spec.years)

    def order(i: int) -> list[str]:
        keys = [str(k) for k in cur[f"__k{i}__"].unique()] if len(cur) else []
        if axes[i] == time:
            return sorted(keys)
        sums = cur.groupby(f"__k{i}__", observed=True)[VALUE].sum()
        return sorted(keys, key=lambda k: -(_f(sums.get(k, 0)) or 0.0))

    row_order, col_order = order(0), order(1)
    rpos = {k: n for n, k in enumerate(row_order)}
    cpos = {k: n for n, k in enumerate(col_order)}

    def label(i: int, key: str) -> str:
        return tg.label_of(key, ctx.time.grain) if axes[i] == time else key

    additive = pwin is not None and not metric.is_ratio
    rows: list[Row] = []
    for _, r in cur.iterrows():
        a, b = str(r["__k0__"]), str(r["__k1__"])
        prior = prior_map.get((shifted(0, a), shifted(1, b)))
        row = _row(cell_key(a, b), cell_key(label(0, a), label(1, b)),
                   _f(r[VALUE]), prior, total, metric, additive,
                   _f(r[NUM]), _f(r[DEN]))
        rows.append(dataclasses.replace(row, extra=(
            ("row", a), ("col", b), ("row_label", label(0, a)),
            ("col_label", label(1, b)), ("row_order", rpos[a]),
            ("col_order", cpos[b]))))
    rows.sort(key=lambda r: (dict(r.extra)["row_order"],
                             dict(r.extra)["col_order"]))
    delta_total = (total - prior_total) if (total is not None and
                                            prior_total is not None) else None
    notes = (f"{len(row_order)} {model.label_of(axes[0])} × "
             f"{len(col_order)} {model.label_of(axes[1])}",)
    if metric.is_ratio:
        notes += (f"{metric.label} is recomputed per cell as "
                  f"{metric.numerator}/{metric.denominator}, never summed.",)
    return ResultHandle(
        id=f"res_{ctx.fingerprint}_{MATRIX}", context=ctx, kind=MATRIX,
        metric=metric, dimension=axes[0], rows=tuple(rows), total=total,
        prior_total=prior_total, delta_total=delta_total, fact_rows=fact_rows,
        provenance=engine.provenance(ctx, metric, axes), notes=notes,
        axes=axes)


# --------------------------------------------------------------------------

def _row(key: str, label: str, value: float | None, prior: float | None,
         total: float | None, metric: MetricSpec, additive: bool,
         num: float | None = None, den: float | None = None) -> Row:
    delta = _delta(value, prior, additive)
    dpct = (delta / prior) if (delta is not None and prior) else None
    share = None
    if not metric.is_ratio and total:
        share = (value or 0.0) / total
    return Row(key, label, value, prior, delta, dpct, share, num, den)


def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return float(v)


def _delta(value: float | None, prior: float | None,
           additive: bool) -> float | None:
    if value is not None and prior is not None:
        return value - prior
    if additive and (value is not None or prior is not None):
        return (value or 0.0) - (prior or 0.0)
    return None
