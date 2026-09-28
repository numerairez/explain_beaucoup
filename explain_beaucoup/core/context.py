"""The Analytical Context Node - the immutable contract between UI, semantic
model, analytical engine and any AI layer (design plan section 3).

A click never "changes the chart". A click creates a new Context.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from . import timegrain as tg


# --------------------------------------------------------------------------
# Time
# --------------------------------------------------------------------------

def month_add(month: str, delta: int) -> str:
    """Month-grain convenience used by the demo data generator."""
    return tg.add(month, "month", delta)


def month_diff(a: str, b: str) -> int:
    return tg.diff(a, b, "month")


def month_label(month: str) -> str:
    return tg.label_of(month, "month")


@dataclass(frozen=True, slots=True)
class TimeWindow:
    """An inclusive [start, end] window of periods at a given calendar grain.

    Period keys sort chronologically as text at every grain, so a window is
    filtered with a plain string comparison whether the team works in days,
    ISO weeks, months, quarters or years.
    """

    start: str
    end: str
    grain: tg.Grain = "month"

    @staticmethod
    def single(key: str, grain: tg.Grain = "month") -> "TimeWindow":
        return TimeWindow(key, key, grain)

    @staticmethod
    def of(ts: Any, grain: tg.Grain = "month") -> "TimeWindow":
        k = tg.key_of(ts, grain)
        return TimeWindow(k, k, grain)

    @property
    def periods(self) -> list[str]:
        return tg.rng(self.start, self.end, self.grain)

    @property
    def months(self) -> list[str]:
        """Back-compat alias; `periods` is the grain-neutral name."""
        return self.periods

    @property
    def length(self) -> int:
        return tg.diff(self.end, self.start, self.grain) + 1

    def shift(self, delta: int) -> "TimeWindow":
        """Shift by whole periods of this window's own grain."""
        return TimeWindow(tg.add(self.start, self.grain, delta),
                          tg.add(self.end, self.grain, delta), self.grain)

    def shift_calendar(self, *, periods: int = 0, months: int = 0,
                       years: int = 0) -> "TimeWindow":
        """Shift by a calendar amount - how "same period last year" is built
        at any grain."""
        return TimeWindow(
            tg.shift_calendar(self.start, self.grain, periods=periods,
                              months=months, years=years),
            tg.shift_calendar(self.end, self.grain, periods=periods,
                              months=months, years=years),
            self.grain)

    def trailing(self, n: int) -> "TimeWindow":
        """The n periods ending at this window's end."""
        return TimeWindow(tg.add(self.end, self.grain, -(n - 1)), self.end,
                          self.grain)

    def at_grain(self, grain: tg.Grain) -> "TimeWindow":
        """Re-express the same span at a different calendar grain."""
        if grain == self.grain:
            return self
        lo = tg.key_of(tg.bounds(self.start, self.grain)[0], grain)
        hi = tg.key_of(tg.bounds(self.end, self.grain)[1], grain)
        return TimeWindow(lo, hi, grain)

    @property
    def bounds(self) -> tuple[Any, Any]:
        return (tg.bounds(self.start, self.grain)[0],
                tg.bounds(self.end, self.grain)[1])

    @property
    def label(self) -> str:
        if self.start == self.end:
            return tg.label_of(self.end, self.grain)
        return (f"{tg.label_of(self.start, self.grain)} - "
                f"{tg.label_of(self.end, self.grain)}")

    def to_dict(self) -> dict[str, str]:
        return {"start": self.start, "end": self.end, "grain": self.grain}


# --------------------------------------------------------------------------
# Lineage
# --------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class LineageStep:
    """One hop in how this context was reached. Audit trail, not cache key."""

    verb: str
    summary: str
    params: tuple[tuple[str, Any], ...] = ()
    source: str = ""          # e.g. "view:revenue_by_region/bar=Luzon"

    def to_dict(self) -> dict[str, Any]:
        return {"verb": self.verb, "summary": self.summary,
                "params": dict(self.params), "source": self.source}


# --------------------------------------------------------------------------
# Context
# --------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Context:
    """Immutable analytical state.

    Node = Metric + Scope + Grain + Time + Comparison + Provenance
    """

    dataset: str
    metric: str
    time: TimeWindow
    filters: tuple[tuple[str, str], ...] = ()
    grain: tuple[str, ...] = ()
    comparison: str | None = None          # None | "mom" | "qoq" | "yoy"
    lineage: tuple[LineageStep, ...] = field(default=(), compare=False)

    # -- construction helpers ---------------------------------------------

    @staticmethod
    def new(dataset: str, metric: str, time: TimeWindow, *,
            filters: Mapping[str, str] | None = None,
            grain: tuple[str, ...] = (),
            comparison: str | None = None,
            lineage: tuple[LineageStep, ...] = ()) -> "Context":
        return Context(
            dataset=dataset,
            metric=metric,
            time=time,
            filters=Context._freeze(filters or {}),
            grain=tuple(grain),
            comparison=comparison,
            lineage=lineage,
        )

    @staticmethod
    def _freeze(filters: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
        return tuple(sorted((str(k), str(v)) for k, v in filters.items()))

    @property
    def filter_map(self) -> dict[str, str]:
        return dict(self.filters)

    def evolve(self, step: LineageStep, **changes: Any) -> "Context":
        """Every operation returns a NEW node; nothing is ever mutated."""
        if "filters" in changes and not isinstance(changes["filters"], tuple):
            changes["filters"] = Context._freeze(changes["filters"])
        if "grain" in changes:
            changes["grain"] = tuple(changes["grain"])
        return replace(self, lineage=self.lineage + (step,), **changes)

    def with_filter(self, dimension: str, member: str, step: LineageStep) -> "Context":
        f = self.filter_map
        f[dimension] = member
        return self.evolve(step, filters=f)

    def without_filter(self, dimension: str, step: LineageStep) -> "Context":
        f = self.filter_map
        f.pop(dimension, None)
        return self.evolve(step, filters=f)

    # -- identity ----------------------------------------------------------

    def to_dict(self, *, with_lineage: bool = True) -> dict[str, Any]:
        d: dict[str, Any] = {
            "dataset": self.dataset,
            "metric": self.metric,
            "filters": dict(self.filters),
            "grain": list(self.grain),
            "time": self.time.to_dict(),
            "comparison": self.comparison,
        }
        if with_lineage:
            d["lineage"] = [s.to_dict() for s in self.lineage]
        return d

    @property
    def fingerprint(self) -> str:
        """Stable hash of the analytical state. Lineage is deliberately
        excluded: two contexts reached by different paths that mean the same
        thing must hit the same cached result."""
        payload = json.dumps(self.to_dict(with_lineage=False), sort_keys=True)
        return hashlib.sha1(payload.encode()).hexdigest()[:16]

    @property
    def id(self) -> str:
        return f"ctx_{self.fingerprint}"

    # -- human readable ----------------------------------------------------

    def scope_label(self) -> str:
        if not self.filters:
            return "All"
        return ", ".join(f"{v}" for _, v in self.filters)

    def __str__(self) -> str:
        bits = [self.metric]
        if self.grain:
            bits.append("by " + "/".join(self.grain))
        if self.filters:
            bits.append("for " + self.scope_label())
        bits.append(self.time.label)
        if self.comparison:
            bits.append(f"vs {self.comparison.upper()}")
        return " ".join(bits)
