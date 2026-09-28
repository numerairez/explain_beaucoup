"""Calendar grains.

The framework is not month-only. A period is identified by a *key* whose text
sorts chronologically at every grain, so the engine can filter a window with a
plain string comparison no matter what grain the team works at:

    day      2026-08-14
    week     2026-W33      (ISO week)
    month    2026-08
    quarter  2026-Q3
    year     2026
"""

from __future__ import annotations

from typing import Iterable, Literal

import pandas as pd

Grain = Literal["day", "week", "month", "quarter", "year"]
GRAINS: tuple[Grain, ...] = ("day", "week", "month", "quarter", "year")

# How many of the finer grain make up the coarser one - used to express a
# comparison offset in periods, and to rank grains.
ORDER = {g: i for i, g in enumerate(GRAINS)}

_MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


class TimeGrainError(ValueError):
    pass


def check_grain(grain: str) -> Grain:
    if grain not in ORDER:
        raise TimeGrainError(
            f"Unknown time grain '{grain}'. Use one of: {', '.join(GRAINS)}")
    return grain            # type: ignore[return-value]


# --------------------------------------------------------------------------
# timestamp -> key
# --------------------------------------------------------------------------

def key_of(ts: pd.Timestamp, grain: Grain) -> str:
    ts = pd.Timestamp(ts)
    if grain == "day":
        return ts.strftime("%Y-%m-%d")
    if grain == "week":
        iso = ts.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    if grain == "month":
        return ts.strftime("%Y-%m")
    if grain == "quarter":
        return f"{ts.year}-Q{ts.quarter}"
    return f"{ts.year}"


def keys_of(series: pd.Series, grain: Grain) -> pd.Series:
    """Vectorised key derivation for a whole datetime column."""
    if grain == "day":
        return series.dt.strftime("%Y-%m-%d")
    if grain == "week":
        iso = series.dt.isocalendar()
        return (iso.year.astype(str) + "-W"
                + iso.week.astype(int).astype(str).str.zfill(2))
    if grain == "month":
        return series.dt.strftime("%Y-%m")
    if grain == "quarter":
        return series.dt.year.astype(str) + "-Q" + series.dt.quarter.astype(str)
    return series.dt.year.astype(str)


# --------------------------------------------------------------------------
# key -> timestamp
# --------------------------------------------------------------------------

def anchor(key: str, grain: Grain) -> pd.Timestamp:
    """The first instant of the period a key names."""
    try:
        if grain == "day":
            return pd.Timestamp(key)
        if grain == "week":
            year, week = key.split("-W")
            return pd.Timestamp.fromisocalendar(int(year), int(week), 1)
        if grain == "month":
            return pd.Timestamp(key + "-01")
        if grain == "quarter":
            year, q = key.split("-Q")
            return pd.Timestamp(year=int(year), month=(int(q) - 1) * 3 + 1, day=1)
        return pd.Timestamp(year=int(key), month=1, day=1)
    except (ValueError, TypeError) as exc:
        raise TimeGrainError(
            f"'{key}' is not a valid {grain} key ({exc})") from None


def bounds(key: str, grain: Grain) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Inclusive [start, end] timestamps of the period."""
    start = anchor(key, grain)
    end = anchor(add(key, grain, 1), grain) - pd.Timedelta(nanoseconds=1)
    return start, end


# --------------------------------------------------------------------------
# key arithmetic
# --------------------------------------------------------------------------

_OFFSET = {"day": dict(days=1), "week": dict(weeks=1), "month": dict(months=1),
           "quarter": dict(months=3), "year": dict(years=1)}


def add(key: str, grain: Grain, n: int) -> str:
    if n == 0:
        return key
    step = {k: v * n for k, v in _OFFSET[grain].items()}
    return key_of(anchor(key, grain) + pd.DateOffset(**step), grain)


def shift_calendar(key: str, grain: Grain, *, periods: int = 0,
                   months: int = 0, years: int = 0) -> str:
    """Shift by a calendar amount, then re-key at this grain.

    This is what makes a comparison like "same period last year" work at any
    grain: the offset is expressed in calendar units, not in buckets.
    """
    ts = anchor(key, grain)
    if months or years:
        ts = ts - pd.DateOffset(months=months, years=years)
    key2 = key_of(ts, grain)
    return add(key2, grain, -periods) if periods else key2


def diff(a: str, b: str, grain: Grain) -> int:
    """How many periods from b to a (a - b)."""
    if a == b:
        return 0
    forward, cur, n = a > b, b, 0
    lo, hi = (b, a) if forward else (a, b)
    cur = lo
    while cur < hi and n < 100_000:
        cur = add(cur, grain, 1)
        n += 1
    return n if forward else -n


def rng(start: str, end: str, grain: Grain) -> list[str]:
    out, cur = [], start
    while cur <= end and len(out) < 100_000:
        out.append(cur)
        cur = add(cur, grain, 1)
    return out


# --------------------------------------------------------------------------
# display
# --------------------------------------------------------------------------

def label_of(key: str, grain: Grain) -> str:
    if grain == "day":
        ts = anchor(key, grain)
        return f"{ts.day} {_MONTH_NAMES[ts.month - 1]} {ts.year}"
    if grain == "week":
        year, week = key.split("-W")
        return f"W{int(week)} {year}"
    if grain == "month":
        y, m = key.split("-")
        return f"{_MONTH_NAMES[int(m) - 1]} {y}"
    if grain == "quarter":
        y, q = key.split("-Q")
        return f"Q{q} {y}"
    return key


def grain_noun(grain: Grain) -> str:
    return grain


def detect_grain(series: pd.Series) -> Grain:
    """Infer the finest grain the data actually distinguishes.

    Used by `explain-beaucoup init` to propose a sensible default, and by
    `check` to warn when a model asks for a finer grain than the data has.
    """
    ts = series.dropna()
    if ts.empty:
        return "month"
    if (ts.dt.dayofyear == 1).all():
        return "year"
    if ((ts.dt.month - 1) % 3 == 0).all() and (ts.dt.day == 1).all():
        return "quarter"
    if (ts.dt.day == 1).all():
        return "month"
    if (ts.dt.dayofweek == ts.dt.dayofweek.iloc[0]).all() and ts.nunique() > 1:
        return "week"
    return "day"
