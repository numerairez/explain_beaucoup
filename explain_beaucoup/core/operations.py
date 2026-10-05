"""The analytical grammar: typed operations that turn one Context into another.

Operations act on Contexts, never on charts. That is what lets the same engine
serve a Qt desktop app, a notebook, a report or an AI agent (design plan
sections 4 and 13).

Each verb is a `Verb` in the `VERBS` registry. A verb owns everything the
framework needs to know about it - how it changes a Context, what it
advertises from a node, whether it can be scoped to a mark, where its result
lands - so adding one is one class and one `register_verb` call, not an edit
in every layer. The built-in verbs live in `verbs.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Literal, Mapping

from ..semantic.specs import Capability, SemanticError, SemanticModel
from .context import Context, LineageStep, TimeWindow, split_cell

# Result kinds an operation can produce. The renderer maps these to views.
BREAKDOWN = "breakdown"
TIMESERIES = "timeseries"
CHANGE = "change_contribution"
DISTRIBUTION = "distribution"
EXCEPTIONS = "exceptions"
MATRIX = "matrix"


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
# The verb contract
# --------------------------------------------------------------------------

class Verb:
    """One verb of the grammar.

    Subclasses set the class attributes and implement `apply`; `advertise`
    and `kind` are optional.
    """

    name: ClassVar[str] = ""
    # Can be scoped to the mark under the cursor (the session passes `member`).
    member_scoped: ClassVar[bool] = False
    # "replace" re-frames the chart on top; "below" opens a chart under it.
    placement: ClassVar[Literal["replace", "below"]] = "below"
    # Re-reads the same node, so the result keeps the prior node's kind.
    preserves_kind: ClassVar[bool] = False
    # Scoped to one member, reads along that member's hierarchy child.
    hierarchy_scoped: ClassVar[bool] = False
    # Splits the same population a different way. A node read as change
    # keeps being read as change, so a split of a movement is a movement.
    carries_change: ClassVar[bool] = False

    def apply(self, model: SemanticModel, ctx: Context, p: dict[str, Any],
              op: Operation) -> Context:
        raise NotImplementedError

    def advertise(self, model: SemanticModel, ctx: Context) -> list[Capability]:
        """The menu entries this verb offers from `ctx`. Only offer what
        `apply` + `model.validate` would accept."""
        return []

    def kind(self, model: SemanticModel, ctx: Context) -> str | None:
        """The result family this verb asks for, when the grain does not
        settle it. None means "whatever the grain implies"."""
        return None

    # -- helpers for subclasses -------------------------------------------

    def step(self, summary: str, p: Mapping[str, Any], op: Operation
             ) -> LineageStep:
        return LineageStep(self.name, summary, tuple(sorted(p.items())),
                           op.source)


VERBS: dict[str, Verb] = {}


def register_verb(verb: Verb | type[Verb]) -> Verb:
    """Add a verb to the grammar (usable as a class decorator). Registration
    order is menu order within a group."""
    inst = verb() if isinstance(verb, type) else verb
    if not inst.name:
        raise OperationError(f"{type(inst).__name__} has no name")
    VERBS[inst.name] = inst
    return verb                                   # type: ignore[return-value]


def verb(name: str) -> Verb | None:
    return VERBS.get(name)


# --------------------------------------------------------------------------
# Shared rules
# --------------------------------------------------------------------------

def need(params: Mapping[str, Any], key: str, verb: str) -> Any:
    if key not in params:
        raise OperationError(f"'{verb}' requires parameter '{key}'")
    return params[key]


_need = need


def on_time(model: SemanticModel, ctx: Context) -> bool:
    """True when the node's primary axis is the calendar."""
    return bool(ctx.grain) and ctx.grain[0] == model.time_column


def narrow(model: SemanticModel, ctx: Context,
           member: Any) -> tuple[dict[str, str], TimeWindow]:
    """Narrow the scope to the mark the user selected.

    On a dimensional axis the member is a filter. On a *time* axis the member
    IS a period, so it re-keys the window instead - time lives in `ctx.time`,
    never in `ctx.filters`. Dropping it would break the whole plotted window
    down by the new dimension, which is not what clicking one point means.

    On a matrix the member is a cell, and both of its axes are pinned.
    """
    filters = ctx.filter_map
    if member is None or not ctx.grain:
        return filters, ctx.time
    parts = split_cell(member) if len(ctx.grain) == 2 else (str(member),)
    axes = ctx.grain if len(parts) == len(ctx.grain) else ctx.grain[:1]
    if len(parts) != len(axes):                 # not a cell of this matrix
        parts = (str(member),)
    window = ctx.time
    for axis, m in zip(axes, parts):
        if axis == model.time_column:
            window = TimeWindow.single(m, ctx.time.grain)
        else:
            filters[axis] = m
    return filters, window


_narrow = narrow


def single_axis(model: SemanticModel, ctx: Context
                ) -> tuple[tuple[str, ...], TimeWindow]:
    """The one-axis reading of a node: a matrix keeps its first dimensional
    axis, and a time axis it drops collapses the window to its last period -
    the aggregate a single value describes."""
    if len(ctx.grain) < 2:
        return ctx.grain, ctx.time
    dims = tuple(g for g in ctx.grain if g != model.time_column)
    window = (TimeWindow.single(ctx.time.end, ctx.time.grain)
              if model.time_column in ctx.grain else ctx.time)
    return dims[:1], window


def aggregate_view(model: SemanticModel, ctx: Context) -> Context:
    """`ctx` read as one aggregate value per first-axis member."""
    grain, window = single_axis(model, ctx)
    if (grain, window) == (ctx.grain, ctx.time):
        return ctx
    return ctx.evolve(LineageStep("collapse", "Read as one period", ()),
                      grain=grain, time=window)


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------

def apply(model: SemanticModel, ctx: Context, op: Operation) -> Context:
    """Execute a typed operation, returning a NEW context node.

    Raises SemanticError if the resulting node would not be meaningful - the
    framework blocks the analysis before any chart or narrative is generated.
    """
    v = VERBS.get(op.verb)
    if v is None:
        raise OperationError(f"Unknown analytical verb '{op.verb}'")
    new = v.apply(model, ctx, op.param_map, op)
    model.validate(new)
    return new


def capabilities(model: SemanticModel, ctx: Context) -> list[Capability]:
    """The analytical grammar available from this node, filtered to what
    the metadata says is meaningful here."""
    caps = [Capability("explain", "Explain this", (), "Explain",
                       "Rank deterministic evidence for this value")]
    for v in VERBS.values():
        caps.extend(v.advertise(model, ctx))
    return caps


def result_kind(model: SemanticModel, ctx: Context, op: Operation | None) -> str:
    """Which family of result a node should be read as."""
    if len(ctx.grain) == 2:
        return MATRIX
    if on_time(model, ctx):
        return TIMESERIES
    if op is not None and op.verb in VERBS:
        k = VERBS[op.verb].kind(model, ctx)
        if k:
            return k
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


# The built-in grammar. Imported last: it subclasses `Verb` from this module.
from . import verbs as _builtin_verbs  # noqa: E402,F401
