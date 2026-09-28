"""The analytical engine: deterministic computation over governed data.

The engine owns every number. The UI and any AI layer pass Contexts and
receive ResultHandles - they never compute values themselves.

Nothing here knows the name of a single column: the semantic model supplies
them, and the DataSource supplies the time axis.
"""

from __future__ import annotations

import dataclasses
import time as _time
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from ..core import timegrain as tg
from ..core.context import Context, TimeWindow
from ..core.operations import (BREAKDOWN, CHANGE, DISTRIBUTION, EXCEPTIONS,
                               TIMESERIES)
from ..data.source import DataSource
from ..semantic.specs import MetricSpec, SemanticModel

PERIOD = "__period__"


def comparison_label(model: SemanticModel, ctx: Context) -> str:
    """How to describe this node's reference period, at its own grain."""
    if not ctx.comparison:
        return ""
    return model.comparison(ctx.comparison).label_for(ctx.time.grain)


@dataclass(frozen=True)
class Row:
    key: str                      # member value, or period key
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
            "dimension": self.dimension, "total": self.total,
            "prior_total": self.prior_total,
            "rows": [r.to_dict() for r in self.rows],
            "provenance": self.provenance,
        }


class AnalyticalEngine:
    def __init__(self, data: DataSource | pd.DataFrame,
                 model: SemanticModel) -> None:
        if isinstance(data, pd.DataFrame):
            data = DataSource.build(data, model.time_column,
                                    native_grain=model.time_grain,
                                    name=model.dataset)
        self.source = data
        self.model = model
        self._cache: dict[str, ResultHandle] = {}

    @property
    def df(self) -> pd.DataFrame:
        return self.source.frame

    # -- scope -------------------------------------------------------------

    def _scope(self, ctx: Context, window: TimeWindow) -> pd.DataFrame:
        keys = self.source.keys(window.grain)
        mask = (keys >= window.start) & (keys <= window.end)
        df = self.source.frame
        for dim, member in ctx.filters:
            mask &= df[self.model.dim(dim).column] == member
        return df[mask]

    def _period_keys(self, df: pd.DataFrame, grain: tg.Grain) -> pd.Series:
        return self.source.keys(grain).loc[df.index]

    # -- aggregation -------------------------------------------------------

    def _aggregate(self, df: pd.DataFrame, metric: MetricSpec,
                   by: str | pd.Series | None) -> pd.DataFrame:
        """Sum the *components*, then (for ratios) divide. A ratio is never
        summed and never averaged - the plan's margin_pct rule, enforced."""
        cols = [c for c in metric.components() if c]
        if by is None:
            sums = {c: float(df[c].sum()) for c in cols}
            out = pd.DataFrame([sums])
            out.insert(0, "__key__", "__total__")
        elif len(df) == 0:
            out = pd.DataFrame({"__key__": pd.Series(dtype="object"),
                                **{c: pd.Series(dtype="float") for c in cols}})
        else:
            grouper = by if isinstance(by, pd.Series) else df[by]
            g = df.groupby(grouper.rename("__key__"),
                           observed=True)[cols].sum().reset_index()
            out = g
        if metric.is_ratio:
            num, den = metric.numerator, metric.denominator
            out["__value__"] = [(n / d) if d else None
                                for n, d in zip(out[num], out[den])]
            out["__num__"] = out[num]
            out["__den__"] = out[den]
        else:
            out["__value__"] = out[metric.column]
            out["__num__"] = None
            out["__den__"] = None
        return out

    def _prior_window(self, ctx: Context) -> TimeWindow | None:
        if not ctx.comparison:
            return None
        return self.model.comparison(ctx.comparison).shift(ctx.time)

    # -- public ------------------------------------------------------------

    def execute(self, ctx: Context, kind: str = BREAKDOWN) -> ResultHandle:
        self.model.validate(ctx)
        cache_key = f"{ctx.fingerprint}:{kind}"
        hit = self._cache.get(cache_key)
        if hit is not None:
            return hit
        t0 = _time.perf_counter()
        if kind == TIMESERIES:
            handle = self._timeseries(ctx)
        elif kind == DISTRIBUTION:
            handle = self._distribution(ctx)
        elif kind == EXCEPTIONS:
            handle = self._exceptions(ctx)
        else:
            handle = self._breakdown(ctx, kind)
        handle = dataclasses.replace(
            handle, computed_ms=round((_time.perf_counter() - t0) * 1000, 1))
        self._cache[cache_key] = handle
        return handle

    # -- kinds -------------------------------------------------------------

    def _breakdown(self, ctx: Context, kind: str) -> ResultHandle:
        metric = self.model.metric(ctx.metric)
        dim = ctx.grain[0] if ctx.grain else None
        on_time = dim == self.model.time_column
        scope = self._scope(ctx, ctx.time)
        by: str | pd.Series | None = None
        if dim:
            by = (self._period_keys(scope, ctx.time.grain) if on_time
                  else self.model.dim(dim).column)
        cur = self._aggregate(scope, metric, by)
        tot = self._aggregate(scope, metric, None)
        total = _f(tot["__value__"].iloc[0]) if len(tot) else None

        prior_map: dict[str, float] = {}
        prior_total = None
        pwin = self._prior_window(ctx)
        if pwin is not None:
            pscope = self._scope(ctx, pwin)
            pby: str | pd.Series | None = None
            if dim:
                pby = (self._period_keys(pscope, pwin.grain) if on_time
                       else self.model.dim(dim).column)
            pri = self._aggregate(pscope, metric, pby)
            prior_map = {str(k): _f(v) for k, v in
                         zip(pri["__key__"], pri["__value__"])}
            ptot = self._aggregate(pscope, metric, None)
            prior_total = _f(ptot["__value__"].iloc[0]) if len(ptot) else None

        rows: list[Row] = []
        for _, r in cur.iterrows():
            key = str(r["__key__"])
            value = _f(r["__value__"])
            prior = prior_map.get(key)
            delta = (value - prior) if (value is not None and prior is not None) else None
            dpct = (delta / prior) if (delta is not None and prior) else None
            share = None
            if not metric.is_ratio and total:
                share = (value or 0.0) / total
            rows.append(Row(key, key, value, prior, delta, dpct, share,
                            _f(r["__num__"]), _f(r["__den__"])))

        # Members that existed in the prior period but are gone now.
        for key, prior in prior_map.items():
            if not any(r.key == key for r in rows):
                rows.append(Row(key, key, None, prior, None, None, None,
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
            fact_rows=int(len(scope)),
            provenance=self._provenance(ctx, dim, metric), notes=notes)

    def _timeseries(self, ctx: Context) -> ResultHandle:
        metric = self.model.metric(ctx.metric)
        grain = ctx.time.grain
        scope = self._scope(ctx, ctx.time)
        cur = self._aggregate(scope, metric, self._period_keys(scope, grain))
        cur = cur.sort_values("__key__")
        series = {str(k): _f(v) for k, v in zip(cur["__key__"], cur["__value__"])}

        prior_series: dict[str, float] = {}
        pwin = self._prior_window(ctx)
        if pwin is not None:
            pscope = self._scope(ctx, pwin)
            pri = self._aggregate(pscope, metric,
                                  self._period_keys(pscope, pwin.grain))
            prior_series = {str(k): _f(v) for k, v in
                            zip(pri["__key__"], pri["__value__"])}
        spec = self.model.comparison(ctx.comparison) if ctx.comparison else None

        # A trailing window routinely ends mid-period. Saying so is the
        # difference between "sales collapsed" and "the week isn't over".
        data_max = self.source.timestamps.max()
        data_min = self.source.timestamps.min()

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
            partial = value is not None and (hi > data_max or lo < data_min)
            if partial:
                partials.append(tg.label_of(key, grain))
            rows.append(Row(key, tg.label_of(key, grain), value, prior, delta,
                            dpct, extra=(("partial", bool(partial)),)))

        last = rows[-1].value if rows else None
        prev = rows[-2].value if len(rows) > 1 else None
        return ResultHandle(
            id=f"res_{ctx.fingerprint}_{TIMESERIES}", context=ctx,
            kind=TIMESERIES, metric=metric, dimension=self.model.time_column,
            rows=tuple(rows), total=last, prior_total=prev,
            delta_total=(last - prev) if (last is not None and prev is not None) else None,
            fact_rows=int(len(scope)),
            provenance=self._provenance(ctx, self.model.time_column, metric),
            notes=((f"{len([r for r in rows if r.value is not None])} "
                    f"{grain}(s).",)
                   + ((f"Partial {grain}(s) at the edge of the data: "
                       f"{', '.join(partials)} - the data ends "
                       f"{data_max:%d %b %Y}.",) if partials else ())))

    def _distribution(self, ctx: Context) -> ResultHandle:
        """The population underneath the aggregate: one point per fact cell.

        What a "cell" is comes from the model, not from this module.
        """
        metric = self.model.metric(ctx.metric)
        scope = self._scope(ctx, ctx.time)
        cell_dims = [d for d in self.model.cell_dimensions()
                     if d not in ctx.filter_map]
        cols = [c for c in metric.components() if c]
        if not cell_dims or len(scope) == 0:
            rows: tuple[Row, ...] = ()
            vals: list[float] = []
        else:
            cell_cols = [self.model.dim(d).column for d in cell_dims]
            g = scope.groupby(cell_cols, observed=True)[cols].sum().reset_index()
            if metric.is_ratio:
                g["__value__"] = [(n / d) if d else None for n, d in
                                  zip(g[metric.numerator], g[metric.denominator])]
            else:
                g["__value__"] = g[metric.column]
            g = g[g["__value__"].notna()]
            rows = tuple(
                Row(key=" / ".join(str(r[c]) for c in cell_cols),
                    label=" / ".join(str(r[c]) for c in cell_cols),
                    value=_f(r["__value__"]))
                for _, r in g.iterrows())
            vals = sorted(r.value for r in rows if r.value is not None)

        notes: tuple[str, ...] = ()
        if vals:
            def q(p: float) -> float:
                return vals[min(int(p * (len(vals) - 1)), len(vals) - 1)]
            notes = (f"cell = {' x '.join(self.model.label_of(d) for d in cell_dims)}",
                     f"n={len(vals)}  p10={q(.1):,.0f}  median={q(.5):,.0f}  "
                     f"p90={q(.9):,.0f}")
        return ResultHandle(
            id=f"res_{ctx.fingerprint}_{DISTRIBUTION}", context=ctx,
            kind=DISTRIBUTION, metric=metric,
            dimension=ctx.grain[0] if ctx.grain else None, rows=rows,
            total=float(sum(vals)) if vals and not metric.is_ratio else None,
            prior_total=None, delta_total=None, fact_rows=int(len(scope)),
            provenance=self._provenance(ctx, "cell", metric), notes=notes)

    def _exceptions(self, ctx: Context, baseline_periods: int = 12
                    ) -> ResultHandle:
        """Children that are unusual against their *own* history."""
        metric = self.model.metric(ctx.metric)
        dim = ctx.grain[0] if ctx.grain else None
        if dim is None or dim == self.model.time_column:
            return self._breakdown(ctx, EXCEPTIONS)
        col = self.model.dim(dim).column
        grain = ctx.time.grain

        cur_scope = self._scope(ctx, ctx.time)
        cur = self._aggregate(cur_scope, metric, col)
        cur_map = {str(k): _f(v) for k, v in zip(cur["__key__"], cur["__value__"])}

        hist_win = TimeWindow(tg.add(ctx.time.start, grain, -baseline_periods),
                              tg.add(ctx.time.start, grain, -1), grain)
        hist_scope = self._scope(ctx, hist_win)
        cols = [c for c in metric.components() if c]
        rows: list[Row] = []
        if len(hist_scope):
            keys = self._period_keys(hist_scope, grain)
            hg = hist_scope.groupby([hist_scope[col], keys.rename("__p__")],
                                    observed=True)[cols].sum().reset_index()
            if metric.is_ratio:
                hg["__value__"] = [(n / d) if d else None for n, d in
                                   zip(hg[metric.numerator], hg[metric.denominator])]
            else:
                hg["__value__"] = hg[metric.column]
            for key, grp in hg.groupby(col, observed=True):
                vals = grp["__value__"].dropna()
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
        tot = self._aggregate(cur_scope, metric, None)
        return ResultHandle(
            id=f"res_{ctx.fingerprint}_{EXCEPTIONS}", context=ctx,
            kind=EXCEPTIONS, metric=metric, dimension=dim, rows=tuple(rows),
            total=_f(tot["__value__"].iloc[0]) if len(tot) else None,
            prior_total=None, delta_total=None, fact_rows=int(len(cur_scope)),
            provenance=self._provenance(ctx, dim, metric),
            notes=(f"z-score against each member's own trailing "
                   f"{baseline_periods} {grain}(s).",))

    # -- provenance --------------------------------------------------------

    def _provenance(self, ctx: Context, dim: str | None,
                    metric: MetricSpec) -> str:
        col = None
        if dim == self.model.time_column:
            col = f"{self.model.time_column}:{ctx.time.grain}"
        elif dim and dim in self.model.dimensions:
            col = self.model.dim(dim).column
        elif dim:
            col = dim
        where = " AND ".join(f"{self.model.dim(d).column}='{m}'"
                             for d, m in ctx.filters)
        where = (where + " AND " if where else "") + \
                (f"{self.model.time_column} BETWEEN '{ctx.time.start}' AND "
                 f"'{ctx.time.end}' (by {ctx.time.grain})")
        select = (f"SUM({metric.numerator})/SUM({metric.denominator}) AS {metric.name}"
                  if metric.is_ratio else f"SUM({metric.column}) AS {metric.name}")
        group = f" GROUP BY {col}" if col else ""
        sel = f"{col}, " if col else ""
        return f"SELECT {sel}{select} FROM {self.model.dataset} WHERE {where}{group}"

    def cache_size(self) -> int:
        return len(self._cache)


def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return float(v)
