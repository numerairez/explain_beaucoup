"""The built-in verbs of the analytical grammar.

Each class is self-contained: what it does to a Context, what it advertises
from a node, and how the session should treat its result. A team extends the
grammar the same way - subclass `Verb`, decorate it with `register_verb`.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from ..semantic.specs import Capability, SemanticError, SemanticModel
from .context import Context, TimeWindow
from .operations import (CHANGE, DISTRIBUTION, EXCEPTIONS, Operation, Verb,
                         narrow, need, on_time, register_verb, single_axis)


def _current(ctx: Context) -> str | None:
    return ctx.grain[0] if ctx.grain else None


def _rest(ctx: Context) -> tuple[str, ...]:
    """A matrix's other axes - kept when only its rows are re-split."""
    return ctx.grain[1:]


# --------------------------------------------------------------------------
# Composition
# --------------------------------------------------------------------------

@register_verb
class DrillDown(Verb):
    """One level down a declared hierarchy."""
    name = "drill_down"
    member_scoped = True
    carries_change = True

    def apply(self, model, ctx, p, op):
        dim = need(p, "dimension", self.name)
        member = p.get("member")
        current = _current(ctx)
        expected = model.child_dimension(current) if current else None
        if current and expected and dim != expected:
            raise SemanticError(
                f"'{model.label_of(dim)}' is not the structural child of "
                f"'{model.label_of(current)}' (expected "
                f"'{model.label_of(expected)}')")
        filters, window = narrow(model, ctx, member)
        label = f"Drill to {model.label_of(dim)}"
        if member:
            label = f"Drill into {member} by {model.label_of(dim)}"
        # A cell pins every axis; without one, a matrix re-splits its rows.
        grain = (dim,) if member else (dim,) + _rest(ctx)
        return ctx.evolve(self.step(label, p, op), filters=filters,
                          grain=grain, time=window)

    def advertise(self, model, ctx):
        current = _current(ctx)
        if not current or on_time(model, ctx):
            return []
        child = model.child_dimension(current)
        if not child:
            return []
        return [Capability(
            self.name, f"Drill down to {model.label_of(child)}",
            (("dimension", child),), "Composition",
            f"Structural drill: {model.label_of(current)} -> "
            f"{model.label_of(child)}")]


@register_verb
class Decompose(Verb):
    """The same population, split by a different, unrelated dimension."""
    name = "decompose"
    member_scoped = True
    carries_change = True

    def apply(self, model, ctx, p, op):
        dim = need(p, "dimension", self.name)
        member = p.get("member")
        filters, window = narrow(model, ctx, member)
        scope = f"{member} " if member else ""
        grain = (dim,) if member else (dim,) + _rest(ctx)
        if member is None and on_time(model, ctx) and ctx.comparison:
            # Leaving a time axis for an aggregate: the whole window cannot
            # be compared with a reference that overlaps it, so read the
            # period the series ends on - as a matrix does when it drops time.
            probe = dataclasses.replace(ctx, grain=grain)
            if not model.is_valid(probe)[0]:
                window = TimeWindow.single(ctx.time.end, ctx.time.grain)
        return ctx.evolve(
            self.step(f"Explain {scope}by {model.label_of(dim)}", p, op),
            filters=filters, grain=grain, time=window)

    def advertise(self, model, ctx):
        current = _current(ctx)
        child = (model.child_dimension(current)
                 if current and not on_time(model, ctx) else None)
        return [Capability(self.name, f"Break down by {model.label_of(alt)}",
                           (("dimension", alt),), "Composition",
                           model.dimensions[alt].describes)
                for alt in model.alternative_dimensions(
                    ctx, exclude=(child,) if child else ())[:6]]


@register_verb
class Cross(Verb):
    """Add a second axis: the value by two dimensions at once, or by a
    dimension over time. The result is a matrix.

    Scoped to one member, the member is pinned and its next level down
    becomes the rows: Laguna crossed with Channel is Laguna's cities by
    channel. A member with no level below would cross into a single row, so
    that is refused; a point on a time axis crosses the whole chart."""
    name = "cross"
    member_scoped = True

    def apply(self, model, ctx, p, op):
        dim = need(p, "dimension", self.name)
        if len(ctx.grain) != 1:
            raise SemanticError(
                "Cross needs a view broken down by exactly one axis - "
                "break the value down first.")
        current = ctx.grain[0]
        member = p.get("member")
        if member is not None and current != model.time_column:
            child = model.child_dimension(current)
            if child is None:
                raise SemanticError(
                    f"{member} has no level below "
                    f"{model.label_of(current)} to cross. Cross the whole "
                    f"chart instead, or use 'Break down by'.")
            filters, window = narrow(model, ctx, member)
            ctx = dataclasses.replace(ctx, filters=Context._freeze(filters),
                                      time=window, grain=(child,))
            current = child
        if dim == current:
            raise SemanticError(
                f"'{model.label_of(dim)}' is already the axis of this view")
        window = ctx.time
        if model.time_column in (dim, current):
            n = int(p.get("periods", model.trend_periods(ctx.time.grain)))
            if current != model.time_column:
                window = ctx.time.trailing(n)
        # Time always runs along the columns, so a heatmap reads left-to-right.
        grain = ((current, dim) if dim == model.time_column
                 else (dim, current) if current == model.time_column
                 else (current, dim))
        return ctx.evolve(
            self.step(f"Cross {model.label_of(grain[0])} with "
                      f"{model.label_of(grain[1])}", p, op),
            grain=grain, time=window)

    def advertise(self, model, ctx):
        if len(ctx.grain) != 1:
            return []
        current = ctx.grain[0]
        # The level below nests inside this one: crossing with it is a
        # mostly empty grid, and drilling down already shows it.
        child = (model.child_dimension(current)
                 if current != model.time_column else None)
        caps: list[Capability] = []
        for alt in model.alternative_dimensions(
                ctx, exclude=(child,) if child else ())[:3]:
            caps.append(Capability(
                self.name, f"Cross with {model.label_of(alt)}",
                (("dimension", alt),), "Composition",
                f"A heatmap of {model.label_of(current)} × "
                f"{model.label_of(alt)}"))
        if current != model.time_column:
            n = model.trend_periods(ctx.time.grain)
            caps.append(Capability(
                self.name, f"Cross with time (last {n} {ctx.time.grain}s)",
                (("dimension", model.time_column), ("periods", str(n))),
                "Composition",
                f"A heatmap of {model.label_of(current)} over time"))
        return caps


@register_verb
class DrillUp(Verb):
    """One level up the hierarchy - or out of a matrix, back to its rows."""
    name = "drill_up"
    placement = "replace"
    carries_change = True

    def apply(self, model, ctx, p, op):
        if len(ctx.grain) == 2:
            # Out of a matrix: drop the column axis, keep the rows.
            grain, window = single_axis(model, ctx)
            return ctx.evolve(
                self.step(f"Back to {model.label_of(grain[0])} alone"
                          if grain else "Back to the total", {}, op),
                grain=grain, time=window)
        current = _current(ctx)
        if not current:
            raise SemanticError("Already at the top of this path")
        parent = model.parent_dimension(current)
        filters = ctx.filter_map
        if parent:
            filters.pop(parent, None)
            grain: tuple[str, ...] = (parent,)
            summary = f"Drill up to {model.label_of(parent)}"
        else:
            filters.pop(current, None)
            grain = ()
            summary = "Drill up to total"
        return ctx.evolve(self.step(summary, {}, op), filters=filters,
                          grain=grain)


@register_verb
class Focus(Verb):
    """Narrow the scope but keep the grain - "show me only this member"."""
    name = "focus"
    member_scoped = True

    def apply(self, model, ctx, p, op):
        dim = need(p, "dimension", self.name)
        member = need(p, "member", self.name)
        return ctx.with_filter(dim, member,
                               self.step(f"Focus on {member}", p, op))


# --------------------------------------------------------------------------
# Change
# --------------------------------------------------------------------------

@register_verb
class Compare(Verb):
    """Attach a reference period, so every row gains prior and delta."""
    name = "compare"
    placement = "replace"
    preserves_kind = True

    def apply(self, model, ctx, p, op):
        period = need(p, "period", self.name)
        return ctx.evolve(self.step(f"Compare {period.upper()}", p, op),
                          comparison=period)

    def advertise(self, model, ctx):
        caps = []
        for cmp_ in model.comparisons:
            if cmp_.name == ctx.comparison:
                continue
            probe = dataclasses.replace(ctx, comparison=cmp_.name)
            if not model.is_valid(probe)[0]:
                continue
            caps.append(Capability(
                self.name, f"Compare with {cmp_.label_for(ctx.time.grain)}",
                (("period", cmp_.name),), "Change",
                f"Reference: {cmp_.label_for(ctx.time.grain)}"))
        return caps


@register_verb
class ClearComparison(Verb):
    """Drop the reference period."""
    name = "clear_comparison"
    placement = "replace"
    preserves_kind = True

    def apply(self, model, ctx, p, op):
        return ctx.evolve(self.step("Clear comparison", {}, op),
                          comparison=None)


class _Reading(Verb):
    """Verbs that keep the analytical state and change how the node is
    *read*. A member narrows scope first; a matrix is read one axis at a
    time."""
    member_scoped = True
    needs_dimension = True
    result: str = ""

    def apply(self, model, ctx, p, op):
        grain, window = single_axis(model, ctx)
        base = (ctx if (grain, window) == (ctx.grain, ctx.time)
                else dataclasses.replace(ctx, grain=grain, time=window))
        current_grain = _current(base)
        if self.needs_dimension and (
                current_grain is None or current_grain == model.time_column):
            raise SemanticError(
                f"'{self.name.replace('_', ' ')}' needs a dimensional grain "
                f"to break the value down by - this node is at "
                f"{'the total' if current_grain is None else 'time'} grain. "
                f"Break the value down by a dimension first.")
        member = p.get("member")
        filters, window = narrow(model, ctx, member)
        if len(ctx.grain) == 2 and member is not None:
            window = window if window.start == window.end else base.time
        elif len(ctx.grain) == 2:
            window = base.time
        changes: dict[str, Any] = {"filters": filters, "time": window,
                                   "grain": base.grain}
        if member is not None and self.hierarchy_scoped:
            # Pinned to one member, the current grain has a single mark left:
            # the member itself, owning 100% of its own movement. Read it
            # along its hierarchy instead - the next level down, or not at all.
            child = model.child_dimension(current_grain)
            if child is None:
                raise SemanticError(
                    f"{member} has no level below "
                    f"{model.label_of(current_grain)} to break "
                    f"'{self.name.replace('_', ' ')}' down by. Use 'Break "
                    f"down by' to pick a dimension.")
            changes["grain"] = (child,)
        self.extra(model, ctx, changes)
        return ctx.evolve(
            self.step(self.name.replace("_", " ").title(), p, op), **changes)

    def extra(self, model: SemanticModel, ctx: Context,
              changes: dict[str, Any]) -> None:
        pass

    def kind(self, model, ctx):
        return self.result


@register_verb
class ChangeContribution(_Reading):
    """Each child's share of the parent's movement."""
    name = "change_contribution"
    hierarchy_scoped = True
    result = CHANGE

    def extra(self, model, ctx, changes):
        if not ctx.comparison:
            # Attribution needs a reference period; take the model's first
            # valid one rather than assuming it declares a particular name.
            probe = dataclasses.replace(ctx, grain=changes["grain"],
                                        time=changes["time"])
            default = model.default_comparison(probe)
            if default is None:
                raise SemanticError(
                    "'change contribution' needs a comparison, and none of "
                    "the model's comparisons is valid from this node.")
            changes["comparison"] = default

    def advertise(self, model, ctx):
        current = _current(ctx)
        if not current or on_time(model, ctx):
            return []
        grain, window = single_axis(model, ctx)
        probe = dataclasses.replace(ctx, grain=grain, time=window)
        if not (ctx.comparison or model.default_comparison(probe)):
            return []
        return [Capability(self.name,
                           f"Break down change by {model.label_of(current)}",
                           (), "Change",
                           "Rank children by their share of the parent's "
                           "movement")]


# --------------------------------------------------------------------------
# Trend / Exception / Distribution
# --------------------------------------------------------------------------

@register_verb
class Trend(Verb):
    """Hold the scope fixed, show history."""
    name = "trend"
    member_scoped = True

    def apply(self, model, ctx, p, op):
        n = int(p.get("periods", p.get("months", 12)))
        member = p.get("member")
        filters, anchor = narrow(model, ctx, member)
        if len(ctx.grain) == 2 and anchor.start != anchor.end:
            anchor = TimeWindow.single(anchor.end, anchor.grain)
        scope = member or ctx.scope_label()
        grain = p.get("grain", ctx.time.grain)
        window = anchor.at_grain(grain) if grain != anchor.grain else anchor
        return ctx.evolve(
            self.step(f"Trend {scope} over {n} {grain}s", p, op),
            filters=filters, grain=(model.time_column,),
            time=window.trailing(n))

    def advertise(self, model, ctx):
        n = model.trend_periods(ctx.time.grain)
        return [Capability(self.name, f"Trend last {n} {ctx.time.grain}s",
                           (("periods", str(n)),), "Trend",
                           "Hold the scope fixed, show history")]


@register_verb
class Exceptions(_Reading):
    """Children unusual against their own history."""
    name = "exceptions"
    hierarchy_scoped = True
    result = EXCEPTIONS

    def advertise(self, model, ctx):
        if not _current(ctx) or on_time(model, ctx):
            return []
        return [Capability(self.name, "Find exceptions", (), "Exception",
                           "Children unusual against their own history")]


@register_verb
class Distribution(_Reading):
    """The population underneath this aggregate."""
    name = "distribution"
    needs_dimension = False
    result = DISTRIBUTION

    def advertise(self, model, ctx):
        if on_time(model, ctx):
            return []
        return [Capability(self.name, "Show distribution", (), "Distribution",
                           "The population underneath this aggregate")]


# --------------------------------------------------------------------------
# Time
# --------------------------------------------------------------------------

@register_verb
class SetGrain(Verb):
    """Re-bucket the calendar: month to quarter to year."""
    name = "set_grain"
    placement = "replace"
    preserves_kind = True

    def apply(self, model, ctx, p, op):
        grain = need(p, "grain", self.name)
        if grain not in model.grains:
            raise SemanticError(
                f"Time grain '{grain}' is not available for this dataset "
                f"(available: {', '.join(model.grains)})")
        span = ctx.time.at_grain(grain)
        # Re-keying a window can run past the end of the data (a year window
        # becomes twelve months, most of them empty). The caller passes the
        # last period that actually has data; period keys sort as text, so
        # clamping is a plain min().
        last = min(span.end, p["end"]) if p.get("end") else span.end
        if model.time_column in ctx.grain:
            window = TimeWindow(min(span.start, last), last, grain)
        else:
            window = TimeWindow.single(last, grain)
        return ctx.evolve(self.step(f"View by {grain}", p, op), time=window)

    def advertise(self, model, ctx):
        return [Capability(self.name, f"View by {g}", (("grain", g),),
                           "Trend", f"Re-bucket the time axis to {g}")
                for g in model.grains if g != ctx.time.grain]


@register_verb
class SetTime(Verb):
    """Set the time window explicitly."""
    name = "set_time"
    placement = "replace"
    preserves_kind = True

    def apply(self, model, ctx, p, op):
        start = need(p, "start", self.name)
        end = need(p, "end", self.name)
        grain = p.get("grain", ctx.time.grain)
        window = TimeWindow(start, end, grain)
        return ctx.evolve(self.step(f"Time {window.label}", p, op),
                          time=window)


# --------------------------------------------------------------------------
# Metric
# --------------------------------------------------------------------------

@register_verb
class SwitchMetric(Verb):
    """The same population, a different measure."""
    name = "switch_metric"
    placement = "replace"
    preserves_kind = True

    def apply(self, model, ctx, p, op):
        metric = need(p, "metric", self.name)
        spec = model.metric(metric)
        return ctx.evolve(self.step(f"Switch metric to {spec.label}", p, op),
                          metric=metric)

    def advertise(self, model, ctx):
        caps = []
        for name, m in model.metrics.items():
            if name == ctx.metric:
                continue
            if any(g in m.invalid_grains for g in ctx.grain):
                continue
            caps.append(Capability(self.name, f"Switch metric to {m.label}",
                                   (("metric", name),), "Metric", m.describes))
        return caps


__all__ = ["Operation"]
