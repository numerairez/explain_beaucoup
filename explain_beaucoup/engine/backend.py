"""Where the numbers come from.

Every result family the engine knows - breakdown, timeseries, exceptions,
distribution, matrix - is built from one primitive:

    backend.aggregate(Query) -> one row per group, the components summed

A ratio is recomputed from its summed components by the engine, never by a
backend, so a backend only ever has to *sum*. That is what keeps a new backend
small: implement `aggregate` and a handful of calendar questions, and every
verb, explanation and chart works on top of it.

Two ship with the framework:

* `PandasBackend` - an in-memory frame. What `DataSource` and the YAML models
  produce. Its provenance is the *equivalent* SQL.
* `DuckDBBackend` - the same queries run as real SQL, against a DuckDB table,
  a parquet file or a frame. Its provenance is the query that actually ran.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Protocol, runtime_checkable

import pandas as pd

from ..core import timegrain as tg
from ..core.context import TimeWindow
from ..data.source import DataSource

# The group-by token for "the period key at the window's grain".
PERIOD = "__period__"
# Every aggregate also counts the fact rows that went into each group.
N = "__n__"


@dataclass(frozen=True)
class Query:
    """One governed aggregation, in physical column names.

    `group_by` holds data columns, or `PERIOD` for the period key at
    `window.grain`. `ratio` is (alias, numerator, denominator) and only shapes
    the provenance - the engine divides.
    """

    components: tuple[str, ...]
    window: TimeWindow
    filters: tuple[tuple[str, str], ...] = ()
    group_by: tuple[str, ...] = ()
    ratio: tuple[str, str, str] | None = None
    alias: str = ""                      # display name of an additive metric


@runtime_checkable
class Backend(Protocol):
    """What the engine needs from a data store."""

    name: str
    time_column: str
    native_grain: tg.Grain

    def aggregate(self, q: Query) -> pd.DataFrame:
        """One row per distinct `group_by` combination present in scope, with
        a column per group key, a summed column per component, and `N`.
        With no `group_by`, exactly one row (zeros when nothing is in scope)."""

    def periods(self, grain: tg.Grain) -> list[str]: ...

    def latest(self, grain: tg.Grain) -> str: ...

    def time_bounds(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        """First and last timestamp in the data."""

    def distinct_counts(self, columns: Iterable[str]) -> dict[str, int]: ...

    def sql(self, q: Query) -> str:
        """The query as SQL - executed, or equivalent."""


# --------------------------------------------------------------------------
# SQL text, shared by both backends
# --------------------------------------------------------------------------

def _quote(v: Any) -> str:
    return "'" + str(v).replace("'", "''") + "'"


def render_sql(q: Query, table: str, period: Callable[[tg.Grain], str], *,
               ident: Callable[[str], str] = lambda c: c) -> str:
    """Render a Query. `period(grain)` gives the SQL expression for a period
    key; `ident` quotes column names (identity for display)."""
    pexpr = period(q.window.grain)
    keys = [pexpr + f" AS {PERIOD}" if g == PERIOD else ident(g)
            for g in q.group_by]
    measures = [f"SUM({ident(c)}) AS {ident(c)}" for c in q.components]
    if q.ratio:
        alias, num, den = q.ratio
        measures.append(f"SUM({ident(num)}) / NULLIF(SUM({ident(den)}), 0) "
                        f"AS {ident(alias)}")
    elif q.alias and q.components and q.alias != q.components[0]:
        measures.append(f"SUM({ident(q.components[0])}) AS {ident(q.alias)}")
    measures.append(f"COUNT(*) AS {N}")
    where = [f"{ident(c)}={_quote(v)}" for c, v in q.filters]
    where.append(f"{pexpr} BETWEEN {_quote(q.window.start)} AND "
                 f"{_quote(q.window.end)}")
    sql = (f"SELECT {', '.join(keys + measures)} FROM {table} "
           f"WHERE {' AND '.join(where)}")
    if q.group_by:
        groups = [PERIOD if g == PERIOD else ident(g) for g in q.group_by]
        sql += f" GROUP BY {', '.join(groups)}"
    return sql


# --------------------------------------------------------------------------
# pandas
# --------------------------------------------------------------------------

class PandasBackend:
    """An in-memory frame with a parsed time axis."""

    def __init__(self, source: DataSource) -> None:
        self.source = source
        self.name = source.name
        self.time_column = source.time_column
        self.native_grain = source.native_grain

    @property
    def frame(self) -> pd.DataFrame:
        return self.source.frame

    def _mask(self, q: Query) -> pd.Series:
        keys = self.source.keys(q.window.grain)
        mask = (keys >= q.window.start) & (keys <= q.window.end)
        df = self.source.frame
        for col, member in q.filters:
            mask &= df[col] == member
        return mask

    def aggregate(self, q: Query) -> pd.DataFrame:
        df = self.source.frame[self._mask(q)]
        cols = list(q.components)
        if not q.group_by:
            row = {c: float(df[c].sum()) for c in cols}
            row[N] = int(len(df))
            return pd.DataFrame([row])
        if len(df) == 0:
            return pd.DataFrame({**{g: pd.Series(dtype="object")
                                    for g in q.group_by},
                                 **{c: pd.Series(dtype="float") for c in cols},
                                 N: pd.Series(dtype="int")})
        groupers = [self.source.keys(q.window.grain).loc[df.index]
                    .rename(PERIOD) if g == PERIOD else df[g].rename(g)
                    for g in q.group_by]
        g = df.groupby(groupers, observed=True)
        out = g[cols].sum()
        out[N] = g.size()
        return out.reset_index()

    def periods(self, grain: tg.Grain) -> list[str]:
        return self.source.periods(grain)

    def latest(self, grain: tg.Grain) -> str:
        return self.source.latest(grain)

    def time_bounds(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        ts = self.source.timestamps
        return ts.min(), ts.max()

    def distinct_counts(self, columns: Iterable[str]) -> dict[str, int]:
        f = self.source.frame
        return {c: int(f[c].nunique()) for c in columns if c in f.columns}

    def sql(self, q: Query) -> str:
        return render_sql(q, self.name,
                          lambda g: f"PERIOD({self.time_column}, '{g}')")


# --------------------------------------------------------------------------
# DuckDB
# --------------------------------------------------------------------------

_DUCK_PERIOD = {
    "day": "strftime({ts}, '%Y-%m-%d')",
    "week": "strftime({ts}, '%G-W%V')",
    "month": "strftime({ts}, '%Y-%m')",
    "quarter": "strftime({ts}, '%Y') || '-Q' || CAST(quarter({ts}) AS VARCHAR)",
    "year": "strftime({ts}, '%Y')",
}


class DuckDBBackend:
    """The same governed queries, executed as SQL.

    Point it at any DuckDB relation - a table, a view, `read_parquet(...)` -
    whose time column is a DATE or TIMESTAMP. `from_source` registers an
    in-memory frame, which is how its parity with pandas is tested.
    """

    def __init__(self, conn: Any, table: str, time_column: str, *,
                 native_grain: tg.Grain = "month", name: str = "",
                 display_time: str = "") -> None:
        self.conn = conn
        self.table = table
        self.time_column = time_column
        # The column name provenance shows, when the queried one is derived.
        self.display_time = display_time or time_column
        self.native_grain = tg.check_grain(native_grain)
        self.name = name or table
        self._periods: dict[str, list[str]] = {}

    @staticmethod
    def from_source(source: DataSource, conn: Any = None) -> "DuckDBBackend":
        import duckdb
        conn = conn or duckdb.connect()
        frame = source.frame.copy()
        ts = "__ts__"
        frame[ts] = source.timestamps
        view = f"eb_{source.name}".replace("-", "_")
        conn.register(view, frame)
        return DuckDBBackend(conn, view, ts, native_grain=source.native_grain,
                             name=source.name, display_time=source.time_column)

    @staticmethod
    def _ident(c: str) -> str:
        return '"' + c.replace('"', '""') + '"'

    def _period(self, grain: tg.Grain, *, display: bool = False) -> str:
        col = self.display_time if display else self.time_column
        return _DUCK_PERIOD[grain].format(ts=self._ident(col))

    def sql(self, q: Query) -> str:
        return render_sql(q, self.name,
                          lambda g: self._period(g, display=True),
                          ident=self._ident)

    def aggregate(self, q: Query) -> pd.DataFrame:
        sql = render_sql(q, self.table, self._period, ident=self._ident)
        out = self.conn.execute(sql).fetchdf()
        if q.ratio:
            out = out.drop(columns=[q.ratio[0]], errors="ignore")
        if q.alias and q.alias not in q.components:
            out = out.drop(columns=[q.alias], errors="ignore")
        for c in q.components:
            out[c] = out[c].astype(float).fillna(0.0)
        out[N] = out[N].astype(int)
        return out

    def _columns(self) -> set[str]:
        rel = self.conn.execute(f"SELECT * FROM {self.table} LIMIT 0")
        return {d[0] for d in rel.description}

    def periods(self, grain: tg.Grain) -> list[str]:
        if grain not in self._periods:
            rows = self.conn.execute(
                f"SELECT DISTINCT {self._period(grain)} AS p FROM {self.table} "
                f"WHERE {self._ident(self.time_column)} IS NOT NULL ORDER BY p"
            ).fetchall()
            self._periods[grain] = [r[0] for r in rows]
        return self._periods[grain]

    def latest(self, grain: tg.Grain) -> str:
        return self.periods(grain)[-1]

    def time_bounds(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        lo, hi = self.conn.execute(
            f"SELECT MIN({self._ident(self.time_column)}), "
            f"MAX({self._ident(self.time_column)}) FROM {self.table}"
        ).fetchone()
        return pd.Timestamp(lo), pd.Timestamp(hi)

    def distinct_counts(self, columns: Iterable[str]) -> dict[str, int]:
        have = self._columns()
        cols = [c for c in columns if c in have]
        if not cols:
            return {}
        sel = ", ".join(f"COUNT(DISTINCT {self._ident(c)})" for c in cols)
        vals = self.conn.execute(f"SELECT {sel} FROM {self.table}").fetchone()
        return dict(zip(cols, map(int, vals)))


def as_backend(data: Any, model: Any) -> Backend:
    """Accept a Backend, a DataSource or a bare frame."""
    if isinstance(data, pd.DataFrame):
        data = DataSource.build(data, model.time_column,
                                native_grain=model.time_grain,
                                name=model.dataset)
    if isinstance(data, DataSource):
        return PandasBackend(data)
    if isinstance(data, Backend):
        return data
    raise TypeError(f"Not a data backend: {type(data).__name__}")
