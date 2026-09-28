"""Bringing a team's own data in.

A DataSource is a governed table plus the one thing the framework insists on:
a time column it can bucket into calendar periods. Everything else about the
table is described by the semantic model, not discovered here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from ..core import timegrain as tg

READERS = {
    ".csv": "csv", ".gz": "csv", ".tsv": "csv", ".txt": "csv",
    ".parquet": "parquet", ".pq": "parquet",
    ".json": "json", ".ndjson": "json",
    ".xlsx": "excel", ".xls": "excel", ".xlsm": "excel",
    ".feather": "feather",
}


class DataSourceError(Exception):
    pass


def read_table(path: str | Path, *, sheet: str | int | None = None,
               separator: str | None = None, **kwargs: Any) -> pd.DataFrame:
    """Read whatever file a team has, by extension."""
    p = Path(path)
    if not p.exists():
        raise DataSourceError(f"No such data file: {p}")
    suffixes = [s.lower() for s in p.suffixes]
    kind = None
    for suf in reversed(suffixes):
        if suf in READERS and READERS[suf] != "csv":
            kind = READERS[suf]
            break
    if kind is None:
        kind = READERS.get(suffixes[-1] if suffixes else "", None)
    if kind is None:
        raise DataSourceError(
            f"Don't know how to read '{p.name}'. Supported: "
            f"{', '.join(sorted(set(READERS)))}")
    try:
        if kind == "csv":
            sep = separator or ("\t" if ".tsv" in suffixes else ",")
            return pd.read_csv(p, sep=sep, **kwargs)
        if kind == "parquet":
            return pd.read_parquet(p, **kwargs)
        if kind == "json":
            lines = ".ndjson" in suffixes
            return pd.read_json(p, lines=lines, **kwargs)
        if kind == "excel":
            return pd.read_excel(p, sheet_name=sheet if sheet is not None else 0,
                                 **kwargs)
        return pd.read_feather(p, **kwargs)
    except ImportError as exc:
        raise DataSourceError(
            f"Reading {kind} files needs an extra package: {exc}. "
            f"Try `pip install pyarrow` (parquet/feather) or "
            f"`pip install openpyxl` (excel).") from None
    except Exception as exc:
        raise DataSourceError(f"Could not read {p.name}: {exc}") from None


def parse_time(series: pd.Series, *, fmt: str | None = None,
               dayfirst: bool = False) -> pd.Series:
    """Normalise any reasonable time column to timestamps.

    Handles real dates, ISO strings, '2026-08' period strings, and non-ISO
    layouts like '14/08/2026'.
    """
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    if fmt:
        return pd.to_datetime(series, format=fmt)
    if pd.api.types.is_numeric_dtype(series):
        # Bare years (2024, 2025) are common in annual extracts.
        vals = series.dropna()
        if not vals.empty and vals.between(1900, 2999).all():
            return pd.to_datetime(series.astype("Int64").astype(str),
                                  format="%Y")
        raise DataSourceError(
            "Numeric time column that does not look like a year. Give an "
            "explicit `time.format` in the model, or convert it upstream.")
    text = series.astype("string").str.strip()
    for attempt in ({"format": "ISO8601"}, {"format": "mixed",
                                            "dayfirst": dayfirst}):
        try:
            return pd.to_datetime(text, **attempt)
        except (ValueError, TypeError):
            continue
    raise DataSourceError(
        "Could not parse the time column. Set `time.format` in the model "
        "(e.g. '%d/%m/%Y').")


@dataclass
class DataSource:
    """A frame plus its time axis. Period keys are derived lazily per grain
    and cached, so switching grain costs one vectorised pass, not a reload."""

    frame: pd.DataFrame
    time_column: str
    timestamps: pd.Series = field(repr=False)
    native_grain: tg.Grain = "month"
    name: str = "dataset"
    _keys: dict[str, pd.Series] = field(default_factory=dict, repr=False)

    @staticmethod
    def build(frame: pd.DataFrame, time_column: str, *,
              fmt: str | None = None, dayfirst: bool = False,
              native_grain: tg.Grain | None = None,
              name: str = "dataset") -> "DataSource":
        if time_column not in frame.columns:
            raise DataSourceError(
                f"Time column '{time_column}' is not in the data. "
                f"Columns: {', '.join(map(str, frame.columns))}")
        ts = parse_time(frame[time_column], fmt=fmt, dayfirst=dayfirst)
        if ts.isna().all():
            raise DataSourceError(
                f"Every value in time column '{time_column}' failed to parse.")
        grain = native_grain or tg.detect_grain(ts)
        return DataSource(frame=frame, time_column=time_column, timestamps=ts,
                          native_grain=tg.check_grain(grain), name=name)

    @staticmethod
    def from_file(path: str | Path, time_column: str, **kwargs: Any) -> "DataSource":
        read_kwargs = {k: kwargs.pop(k) for k in ("sheet", "separator")
                       if k in kwargs}
        frame = read_table(path, **read_kwargs)
        return DataSource.build(frame, time_column,
                                name=kwargs.pop("name", Path(path).stem),
                                **kwargs)

    # -- period keys -------------------------------------------------------

    def keys(self, grain: tg.Grain) -> pd.Series:
        grain = tg.check_grain(grain)
        cached = self._keys.get(grain)
        if cached is None:
            cached = tg.keys_of(self.timestamps, grain).rename("__period__")
            self._keys[grain] = cached
        return cached

    def periods(self, grain: tg.Grain) -> list[str]:
        return sorted(self.keys(grain).dropna().unique().tolist())

    def span(self, grain: tg.Grain) -> tuple[str, str]:
        ps = self.periods(grain)
        if not ps:
            raise DataSourceError("The dataset has no usable time values.")
        return ps[0], ps[-1]

    def latest(self, grain: tg.Grain) -> str:
        return self.span(grain)[1]

    @property
    def row_count(self) -> int:
        return int(len(self.frame))

    def grains(self) -> list[tg.Grain]:
        """Grains that make sense for this data: the native one and coarser."""
        i = tg.ORDER[self.native_grain]
        return [g for g in tg.GRAINS if tg.ORDER[g] >= i]


# --------------------------------------------------------------------------
# profiling - used by `explain-beaucoup init` to propose a starter model
# --------------------------------------------------------------------------

@dataclass
class ColumnProfile:
    name: str
    dtype: str
    role: str                 # "time" | "dimension" | "measure" | "ignore"
    distinct: int
    nulls: int
    sample: list[str]
    reason: str = ""


def profile_frame(frame: pd.DataFrame, *, max_dimension_cardinality: int = 500
                  ) -> list[ColumnProfile]:
    """Guess each column's analytical role. A guess, not a decision - the
    generated model is meant to be edited."""
    out: list[ColumnProfile] = []
    rows = max(len(frame), 1)
    for col in frame.columns:
        s = frame[col]
        distinct = int(s.nunique(dropna=True))
        nulls = int(s.isna().sum())
        sample = [str(v) for v in s.dropna().unique()[:4]]
        dtype = str(s.dtype)

        if pd.api.types.is_datetime64_any_dtype(s):
            role, reason = "time", "datetime column"
        elif pd.api.types.is_numeric_dtype(s):
            if distinct <= 12 and set(map(str, s.dropna().unique())) <= {
                    str(i) for i in range(-50, 51)}:
                role, reason = "dimension", "few distinct integers - looks like a code"
            else:
                role, reason = "measure", "numeric"
        else:
            looks_temporal = any(k in str(col).lower()
                                 for k in ("date", "month", "period", "day",
                                           "week", "quarter", "year", "time"))
            if looks_temporal and distinct <= rows:
                role, reason = "time", "name and values look temporal"
            elif distinct <= max_dimension_cardinality:
                role, reason = "dimension", f"{distinct} distinct values"
            else:
                role, reason = "ignore", (f"{distinct} distinct values - too "
                                          f"high to group by")
        out.append(ColumnProfile(str(col), dtype, role, distinct, nulls,
                                 sample, reason))
    return out
