"""The analytical grammar: typed operations that turn one Context into another.

Operations act on Contexts, never on charts. That is what lets the same engine
serve a Qt desktop app, a notebook, a report or an AI agent (design plan
sections 4 and 13).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Mapping

from ..semantic.specs import SemanticError, SemanticModel
from .context import Context, LineageStep, TimeWindow

# Result kinds an operation can produce. The renderer maps these to views.
BREAKDOWN = "breakdown"
TIMESERIES = "timeseries"
CHANGE = "change_contribution"
DISTRIBUTION = "distribution"
EXCEPTIONS = "exceptions"


@dataclass(frozen=True)
class Operation:
    """A typed edge in the investigation graph."""

    verb: str
    params: tuple[tuple[str, Any], ...] = ()
    source: str = ""          # provenance: where the gesture came from

    @staticmethod
    def of(verb: str, source: str = "", **params: Any) -> "Operation":
        return Operation(verb, tuple(sorted(params.items())), source)

    @property
    def param_map(self) -> dict[str, Any]:
        return dict(self.params)


class OperationError(Exception):
    pass


# --------------------------------------------------------------------------
# The verbs
# --------------------------------------------------------------------------

def _need(params: Mapping[str, Any], key: str, verb: str) -> Any:
    if key not in params:
        raise OperationError(f"'{verb}' requires parameter '{key}'")
    return params[key]


def _narrow(model: SemanticModel, ctx: Context,
            member: Any) -> tuple[dict[str, str], TimeWindow]:
    """Narrow the scope to the mark the user selected.

    On a dimensional grain the member is a filter. On a *time* grain the member
    IS a period, so it re-keys the window instead - time lives in `ctx.time`,
    never in `ctx.filters`. Dropping it would break the whole plotted window
    down by the new dimension, which is not what clicking one point means.
    """
    filters = ctx.filter_map
    current = ctx.grain[0] if ctx.grain else None
    if member is None or not current:
        return filters, ctx.time
    if current == model.time_column:
        return filters, TimeWindow.single(str(member), ctx.time.grain)
    filters[current] = member
    return filters, ctx.time


def apply(model: SemanticModel, ctx: Context, op: Operation) -> Context:
    """Execute a typed operation, returning a NEW context node.

    Raises SemanticError if the resulting node would not be meaningful - the
    framework blocks the analysis before any chart or narrative is generated.
    """
    p = op.param_map
    verb = op.verb

    if verb == "drill_down":
        dim = _need(p, "dimension", verb)
        member = p.get("member")
        current = ctx.grain[0] if ctx.grain else None
        expected = model.child_dimension(current) if current else None
        if current and expected and dim != expected:
            raise SemanticError(
                f"'{model.label_of(dim)}' is not the structural child of "
                f"'{model.label_of(current)}' (expected "
                f"'{model.label_of(expected)}')")
        filters, window = _narrow(model, ctx, member)
        label = f"Drill to {model.label_of(dim)}"
        if member:
            label = f"Drill into {member} by {model.label_of(dim)}"
        new = ctx.evolve(
            LineageStep(verb, label, tuple(sorted(p.items())), op.source),
            filters=filters, grain=(dim,), time=window)

    elif verb == "decompose":
        dim = _need(p, "dimension", verb)
        member = p.get("member")
        filters, window = _narrow(model, ctx, member)
        scope = f"{member} " if member else ""
        new = ctx.evolve(
            LineageStep(verb, f"Explain {scope}by {model.label_of(dim)}",
                        tuple(sorted(p.items())), op.source),
            filters=filters, grain=(dim,), time=window)

    elif verb == "drill_up":
        current = ctx.grain[0] if ctx.grain else None
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
        new = ctx.evolve(LineageStep(verb, summary, (), op.source),
                         filters=filters, grain=grain)

    elif verb == "focus":
        # Narrow the scope but keep the grain - "show me only this member".
        dim = _need(p, "dimension", verb)
        member = _need(p, "member", verb)
        new = ctx.with_filter(
            dim, member,
            LineageStep(verb, f"Focus on {member}", tuple(sorted(p.items())),
                        op.source))

    elif verb == "compare":
        period = _need(p, "period", verb)
        new = ctx.evolve(
            LineageStep(verb, f"Compare {period.upper()}",
                        tuple(sorted(p.items())), op.source),
            comparison=period)

    elif verb == "trend":
        n = int(p.get("periods", p.get("months", 12)))
        member = p.get("member")
        filters, anchor = _narrow(model, ctx, member)
        scope = member or ctx.scope_label()
        grain = p.get("grain", ctx.time.grain)
        window = anchor.at_grain(grain) if grain != anchor.grain else anchor
        new = ctx.evolve(
            LineageStep(verb, f"Trend {scope} over {n} {grain}s",
                        tuple(sorted(p.items())), op.source),
            filters=filters,
            grain=(model.time_column,),
            time=window.trailing(n))

    elif verb == "switch_metric":
        metric = _need(p, "metric", verb)
        spec = model.metric(metric)
        new = ctx.evolve(
            LineageStep(verb, f"Switch metric to {spec.label}",
                        tuple(sorted(p.items())), op.source),
            metric=metric)

    elif verb in ("contribution", "change_contribution", "exceptions",
                  "distribution"):
        current_grain = ctx.grain[0] if ctx.grain else None
        if verb != "distribution" and (
                current_grain is None or current_grain == model.time_column):
            raise SemanticError(
                f"'{verb.replace('_', ' ')}' needs a dimensional grain to "
                f"break the value down by - this node is at "
                f"{'the total' if current_grain is None else 'time'} grain. "
                f"Break the value down by a dimension first.")
        # These keep the analytical state and change how the node is *read*;
        # the member (if any) narrows scope first.
        member = p.get("member")
        filters, window = _narrow(model, ctx, member)
        changes: dict[str, Any] = {"filters": filters, "time": window}
        if verb == "change_contribution" and not ctx.comparison:
            changes["comparison"] = "mom"
        new = ctx.evolve(
            LineageStep(verb, verb.replace("_", " ").title(),
                        tuple(sorted(p.items())), op.source), **changes)

    elif verb == "set_time":
        start = _need(p, "start", verb)
        end = _need(p, "end", verb)
        grain = p.get("grain", ctx.time.grain)
        window = TimeWindow(start, end, grain)
        new = ctx.evolve(
            LineageStep(verb, f"Time {window.label}",
                        tuple(sorted(p.items())), op.source),
            time=window)

    elif verb == "set_grain":
        grain = _need(p, "grain", verb)
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
        if ctx.grain and ctx.grain[0] == model.time_column:
            window = TimeWindow(min(span.start, last), last, grain)
        else:
            window = TimeWindow.single(last, grain)
        new = ctx.evolve(
            LineageStep(verb, f"View by {grain}",
                        tuple(sorted(p.items())), op.source),
            time=window)

    elif verb == "clear_comparison":
        new = ctx.evolve(LineageStep(verb, "Clear comparison", (), op.source),
                         comparison=None)

    else:
        raise OperationError(f"Unknown analytical verb '{verb}'")

    model.validate(new)
    return new


def result_kind(model: SemanticModel, ctx: Context, op: Operation | None) -> str:
    """Which family of result a node should be read as."""
    if ctx.grain and ctx.grain[0] == model.time_column:
        return TIMESERIES
    if op is not None:
        if op.verb == "change_contribution":
            return CHANGE
        if op.verb == "exceptions":
            return EXCEPTIONS
        if op.verb == "distribution":
            return DISTRIBUTION
    return BREAKDOWN


def describe(model: SemanticModel, op: Operation) -> str:
    p = op.param_map
    if "dimension" in p:
        return f"{op.verb} -> {model.label_of(p['dimension'])}"
    if "metric" in p:
        return f"{op.verb} -> {model.metric(p['metric']).label}"
    if "period" in p:
        return f"{op.verb} -> {str(p['period']).upper()}"
    return op.verb
