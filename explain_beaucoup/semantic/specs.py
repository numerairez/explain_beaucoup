"""Semantic model: dimensions, hierarchies, metrics, grains, comparisons.

High interactivity is only trustworthy if the framework knows what operations
make sense (design plan section 8). This module both *validates* operations and
*advertises* the menu of legal next steps.

Nothing here is specific to any dataset. A team describes their own data with
these objects - usually by writing a YAML model that `semantic/loader.py`
turns into a `SemanticModel`.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Iterable, Literal

from ..core import timegrain as tg
from ..core.context import Context

MetricKind = Literal["additive", "semi_additive", "ratio", "count"]
Fmt = Literal["currency", "percent", "number"]


class SemanticError(Exception):
    """Raised when an operation is not meaningful under the model."""


# --------------------------------------------------------------------------

@dataclass(frozen=True)
class DimensionSpec:
    name: str
    label: str
    column: str
    hierarchy: str | None = None      # hierarchy this level belongs to
    level: int = 0                    # position within that hierarchy
    describes: str = ""
    is_cell: bool = False             # part of the finest addressable cell


@dataclass(frozen=True)
class Hierarchy:
    name: str
    label: str
    levels: tuple[str, ...]           # dimension names, coarse -> fine

    def child_of(self, dim: str) -> str | None:
        i = self.levels.index(dim)
        return self.levels[i + 1] if i + 1 < len(self.levels) else None

    def parent_of(self, dim: str) -> str | None:
        i = self.levels.index(dim)
        return self.levels[i - 1] if i > 0 else None


@dataclass(frozen=True)
class MetricSpec:
    name: str
    label: str
    kind: MetricKind
    fmt: Fmt
    column: str | None = None                 # additive / count metrics
    numerator: str | None = None              # ratio metrics
    denominator: str | None = None
    higher_is_better: bool = True
    unit: str = ""
    symbol: str = ""                          # currency symbol, e.g. "$"
    decimals: int | None = None
    invalid_grains: frozenset[str] = frozenset()
    describes: str = ""

    @property
    def is_ratio(self) -> bool:
        return self.kind == "ratio"

    @property
    def aggregation(self) -> str:
        if self.is_ratio:
            return f"recompute_from_components({self.numerator} / {self.denominator})"
        return "sum"

    def components(self) -> tuple[str, ...]:
        if self.is_ratio:
            return (self.numerator, self.denominator)  # type: ignore[return-value]
        return (self.column,)                          # type: ignore[return-value]


@dataclass(frozen=True)
class ComparisonSpec:
    """A reference period, expressed as a calendar offset so it works at any
    grain. `periods` is in units of the context's own grain."""

    name: str
    label: str = ""
    periods: int = 0
    months: int = 0
    years: int = 0

    def label_for(self, grain: tg.Grain) -> str:
        if self.label:
            return self.label.replace("{grain}", grain)
        if self.periods:
            n = "previous" if self.periods == 1 else f"{self.periods} {grain}s ago"
            return f"{n} {grain}" if self.periods == 1 else n
        if self.years:
            return ("same period last year" if self.years == 1
                    else f"same period {self.years} years ago")
        if self.months:
            return f"{self.months} months earlier"
        return self.name

    def shift(self, window):
        return window.shift_calendar(periods=self.periods, months=self.months,
                                     years=self.years)


@dataclass(frozen=True)
class RelationshipSpec:
    """A sanctioned relationship between metrics.

    `volume_rate` says: metric = volume x rate, so a movement in the metric can
    be split into a volume effect and a rate effect. Declaring it is what lets
    the explain engine offer that decomposition - it is never assumed.
    """

    kind: str
    metric: str
    volume: str = ""
    rate: str = ""
    volume_noun: str = "volume"
    rate_noun: str = "rate"


# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Capability:
    """An operation the semantic model says is legal from a given node.

    This is what populates the right-click menu - the UI never invents verbs.
    """

    verb: str
    label: str
    params: tuple[tuple[str, str], ...] = ()
    group: str = "Analyze"
    hint: str = ""

    @property
    def param_map(self) -> dict[str, str]:
        return dict(self.params)


DEFAULT_COMPARISONS = (
    ComparisonSpec("prev", "previous {grain}", periods=1),
    ComparisonSpec("yoy", "same period last year", years=1),
)


# --------------------------------------------------------------------------

@dataclass
class SemanticModel:
    dataset: str
    dimensions: dict[str, DimensionSpec]
    hierarchies: dict[str, Hierarchy]
    metrics: dict[str, MetricSpec]
    time_column: str = "date"
    time_grain: tg.Grain = "month"
    grains: tuple[tg.Grain, ...] = ("month",)
    default_metric: str = ""
    default_grain: tuple[str, ...] = ()
    comparisons: tuple[ComparisonSpec, ...] = DEFAULT_COMPARISONS
    relationships: tuple[RelationshipSpec, ...] = ()
    lenses: dict[str, tuple[str, ...]] = field(default_factory=dict)
    member_counts: dict[str, int] = field(default_factory=dict)
    title: str = ""

    # -- profiling ---------------------------------------------------------

    def profile(self, frame) -> None:
        """Learn member cardinality from the governed data.

        A level with a single member is a real hierarchy level but a useless
        breakdown, so the model should stop advertising it.
        """
        self.member_counts = {
            name: int(frame[d.column].nunique())
            for name, d in self.dimensions.items() if d.column in frame.columns
        }

    # -- lookups -----------------------------------------------------------

    def dim(self, name: str) -> DimensionSpec:
        try:
            return self.dimensions[name]
        except KeyError:
            raise SemanticError(f"Unknown dimension '{name}'") from None

    def metric(self, name: str) -> MetricSpec:
        try:
            return self.metrics[name]
        except KeyError:
            raise SemanticError(f"Unknown metric '{name}'") from None

    def comparison(self, name: str) -> ComparisonSpec:
        for c in self.comparisons:
            if c.name == name:
                return c
        raise SemanticError(f"Unsupported comparison '{name}'")

    def label_of(self, dim: str | None) -> str:
        if not dim:
            return "Total"
        if dim == self.time_column:
            return "Time"
        return self.dimensions[dim].label if dim in self.dimensions else dim.title()

    def child_dimension(self, dim: str) -> str | None:
        d = self.dimensions.get(dim)
        if d is None or d.hierarchy is None:
            return None
        h = self.hierarchies[d.hierarchy]
        nxt = h.child_of(dim)
        while nxt and self.member_counts.get(nxt, 2) <= 1:
            nxt = h.child_of(nxt)
        return nxt

    def parent_dimension(self, dim: str) -> str | None:
        d = self.dimensions.get(dim)
        if d is None or d.hierarchy is None:
            return None
        h = self.hierarchies[d.hierarchy]
        prev = h.parent_of(dim)
        while prev and self.member_counts.get(prev, 2) <= 1:
            prev = h.parent_of(prev)
        return prev

    def hierarchy_of(self, dim: str) -> Hierarchy | None:
        d = self.dimensions.get(dim)
        return self.hierarchies[d.hierarchy] if d and d.hierarchy else None

    def cell_dimensions(self) -> list[str]:
        """The finest addressable cell - what a distribution is a population
        of. Declared via `is_cell`, else inferred as the leaf of each
        hierarchy plus every standalone dimension."""
        declared = [n for n, d in self.dimensions.items() if d.is_cell]
        if declared:
            return declared
        out: list[str] = []
        for name, d in self.dimensions.items():
            if d.hierarchy is None:
                out.append(name)
            elif self.hierarchies[d.hierarchy].levels[-1] == name:
                out.append(name)
        return out

    def relationship_for(self, metric: str, kind: str = "volume_rate"
                         ) -> RelationshipSpec | None:
        for r in self.relationships:
            if r.metric == metric and r.kind == kind:
                return r
        return None

    def first_grain(self) -> tuple[str, ...]:
        if self.default_grain:
            return tuple(self.default_grain)
        for h in self.hierarchies.values():
            for lvl in h.levels:
                if self.member_counts.get(lvl, 2) > 1:
                    return (lvl,)
        for name in self.dimensions:
            if self.member_counts.get(name, 2) > 1:
                return (name,)
        return ()

    # -- validation --------------------------------------------------------

    def validate(self, ctx: Context) -> None:
        """Block nonsensical analyses *before* a chart or narrative exists."""
        if ctx.dataset != self.dataset:
            raise SemanticError(f"Context targets dataset '{ctx.dataset}', "
                                f"model describes '{self.dataset}'")
        m = self.metric(ctx.metric)
        for g in ctx.grain:
            if g != self.time_column and g not in self.dimensions:
                raise SemanticError(f"Unknown grain '{g}'")
            if g in m.invalid_grains:
                raise SemanticError(
                    f"{m.label} is not meaningful at grain '{self.label_of(g)}'")
        for dim, _ in ctx.filters:
            if dim not in self.dimensions:
                raise SemanticError(f"Cannot filter on unknown dimension '{dim}'")
        if ctx.time.grain not in self.grains:
            raise SemanticError(
                f"Time grain '{ctx.time.grain}' is not available for this "
                f"dataset (data is {self.time_grain}-grain or coarser)")
        if ctx.comparison:
            spec = self.comparison(ctx.comparison)
            shifted = spec.shift(ctx.time)
            on_time = bool(ctx.grain) and ctx.grain[0] == self.time_column
            # On a time series the comparison is applied per point, so a
            # shifted window that overlaps the plotted range is fine.
            if not on_time and shifted.end >= ctx.time.start:
                raise SemanticError(
                    f"'{spec.label_for(ctx.time.grain)}' overlaps the window "
                    f"being analysed ({ctx.time.label}). Narrow the window or "
                    f"pick a further-back comparison.")

    def is_valid(self, ctx: Context) -> tuple[bool, str]:
        try:
            self.validate(ctx)
            return True, ""
        except SemanticError as exc:
            return False, str(exc)

    # -- capability advertisement -----------------------------------------

    def alternative_dimensions(self, ctx: Context,
                               exclude: Iterable[str] = ()) -> list[str]:
        """Dimensions that can *explain* the current population: not already
        pinned by a filter, not the current grain, and - within a hierarchy -
        only the next usable level down."""
        used = set(ctx.filter_map) | set(ctx.grain)
        hidden = used | set(exclude)
        out: list[str] = []
        for name, d in self.dimensions.items():
            if name in hidden:
                continue
            if self.member_counts.get(name, 2) <= 1:
                continue        # degenerate level - nothing to break down
            if d.hierarchy:
                h = self.hierarchies[d.hierarchy]
                depth = max((h.levels.index(u) for u in used
                             if u in h.levels), default=-1)
                # Step past degenerate levels rather than stalling on them,
                # or a single-member top level would hide its whole hierarchy.
                nxt = depth + 1
                while (nxt < len(h.levels)
                       and self.member_counts.get(h.levels[nxt], 2) <= 1):
                    nxt += 1
                if h.levels.index(name) != nxt:
                    continue
            out.append(name)
        return out

    def capabilities(self, ctx: Context) -> list[Capability]:
        """The analytical grammar available from this node, filtered to what
        the metadata says is meaningful here."""
        caps: list[Capability] = []
        current = ctx.grain[0] if ctx.grain else None
        on_time = current == self.time_column

        # -- Composition ---------------------------------------------------
        child = None
        if not on_time:
            child = self.child_dimension(current) if current else None
            if child:
                caps.append(Capability(
                    "drill_down", f"Drill down to {self.label_of(child)}",
                    (("dimension", child),), "Composition",
                    f"Structural drill: {self.label_of(current)} -> "
                    f"{self.label_of(child)}"))
        for alt in self.alternative_dimensions(
                ctx, exclude=(child,) if child else ())[:6]:
            caps.append(Capability(
                "decompose", f"Break down by {self.label_of(alt)}",
                (("dimension", alt),), "Composition",
                self.dimensions[alt].describes))
        # -- Change --------------------------------------------------------
        for cmp_ in self.comparisons:
            if cmp_.name == ctx.comparison:
                continue
            probe = dataclasses.replace(ctx, comparison=cmp_.name)
            if not self.is_valid(probe)[0]:
                continue
            caps.append(Capability(
                "compare", f"Compare with {cmp_.label_for(ctx.time.grain)}",
                (("period", cmp_.name),), "Change",
                f"Reference: {cmp_.label_for(ctx.time.grain)}"))
        if current and not on_time:
            caps.append(Capability("change_contribution",
                                   "What explains the change?", (), "Change",
                                   "Rank children by their share of the "
                                   "parent's movement"))

        # -- Trend / Exception / Distribution ------------------------------
        n = 12 if ctx.time.grain in ("month", "week", "day") else 8
        caps.append(Capability("trend", f"Trend last {n} {ctx.time.grain}s",
                               (("periods", str(n)),), "Trend",
                               "Hold the scope fixed, show history"))
        if current and not on_time:
            caps.append(Capability("exceptions", "Find exceptions", (),
                                   "Exception",
                                   "Children unusual against their own history"))
        if not on_time:
            caps.append(Capability("distribution", "Show distribution", (),
                                   "Distribution",
                                   "The population underneath this aggregate"))

        # -- Time grain ----------------------------------------------------
        for g in self.grains:
            if g != ctx.time.grain:
                caps.append(Capability("set_grain", f"View by {g}",
                                       (("grain", g),), "Trend",
                                       f"Re-bucket the time axis to {g}"))

        # -- Metric drill --------------------------------------------------
        for name, m in self.metrics.items():
            if name == ctx.metric:
                continue
            if current and current in m.invalid_grains:
                continue
            caps.append(Capability("switch_metric", f"Switch metric to {m.label}",
                                   (("metric", name),), "Metric", m.describes))

        # -- Explain -------------------------------------------------------
        caps.insert(0, Capability("explain", "Explain this", (), "Explain",
                                  "Rank deterministic evidence for this value"))
        return caps
