"""A headless investigation session.

Everything an analyst can *do* to a workspace lives here: run a verb, decide
whether its result replaces the chart on top or opens below it, resolve the
chart kind it lands on, keep one selected mark per chart, explain, pin, close,
and write every move - dead ends included - to the journal.

No widget, no event loop. A front end (the Qt window, a web client, a
notebook) forwards gestures to a `Session` and renders what it says. That is
what makes the interaction rules testable, and what lets a second UI behave
exactly like the first.

Errors raise. A move the model refuses raises `SemanticError` - after the
dead end has been recorded, because a refusal is part of the story.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from ..data.source import DataSource
from ..engine.backend import Backend
from ..engine.commentary import Commentary
from ..engine.engine import TOTAL_KEY, AnalyticalEngine, normal_kind, title_for
from ..engine.explain import Evidence, ExplanationResult, explain
from ..semantic.specs import SemanticError, SemanticModel
from ..view import format as fmt
from . import journal as J
from .context import Context, LineageStep, TimeWindow
from .graph import InvestigationGraph, Node
from .journal import Journal, JournalEntry
from .operations import (BREAKDOWN, CHANGE, DISTRIBUTION, EXCEPTIONS,
                         MATRIX, TIMESERIES, Operation, aggregate_view, apply,
                         narrow, on_time, result_kind, verb as verb_of)


# How a verb behaves on the stack is declared by the verb itself (see
# `core.operations.Verb`); these read it, so a registered plugin verb is
# treated exactly like a built-in one.
def member_scoped(verb: str) -> bool:
    v = verb_of(verb)
    return bool(v and v.member_scoped)


def carries_change(verb: str) -> bool:
    v = verb_of(verb)
    return bool(v and v.carries_change)


def preserves_kind(verb: str) -> bool:
    v = verb_of(verb)
    return bool(v and v.preserves_kind)


def in_place(verb: str) -> bool:
    """Re-frames the view instead of narrowing it: replaces the chart on top
    of the stack rather than opening one below it."""
    v = verb_of(verb)
    return bool(v and v.placement == "replace")


def hierarchy_scoped(verb: str) -> bool:
    v = verb_of(verb)
    return bool(v and v.hierarchy_scoped)


GROUP_ORDER = ["Explain", "Composition", "Change", "Trend", "Exception",
               "Distribution", "Metric"]
LENSES = {
    "Composition": (BREAKDOWN, None),
    "Change": (CHANGE, "change_contribution"),
    "Trend": (TIMESERIES, "trend"),
    "Exception": (EXCEPTIONS, "exceptions"),
    "Distribution": (DISTRIBUTION, "distribution"),
}
LENS_OF_KIND = {kind: lens for lens, (kind, _) in LENSES.items()}
KIND_CRUMB = {CHANGE: "what moved", EXCEPTIONS: "exceptions",
              DISTRIBUTION: "distribution"}


@dataclass(frozen=True)
class Landing:
    """Where a move put the analyst."""
    node: Node
    reused: bool                  # landed on a chart that was already open
    below: bool                   # opened under the chart on top
    message: str = ""


@dataclass(frozen=True)
class MenuItem:
    """One entry in a mark's context menu.

    `action` is "run" for an analytical verb, otherwise a session action:
    "raise", "close", "branch" or "pin". `submenu` names the nested menu the
    item belongs in, if any. `lands` says where a verb's result goes - see
    `lands()` - so a front end can show it before the move is made.
    """
    label: str
    action: str = "run"
    verb: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    member: str | None = None
    source: str = "ui"
    hint: str = ""
    submenu: str = ""
    lands: str = ""


@dataclass(frozen=True)
class Menu:
    heading: str
    note: str                     # set when the menu came from a chart below
    groups: list[list[MenuItem]]  # separated in the rendered menu


class Session:
    """One analyst's investigation over one governed table."""

    def __init__(self, data: Backend | DataSource | pd.DataFrame,
                 model: SemanticModel, *, root: Context | None = None,
                 kind: str = BREAKDOWN, title: str = "") -> None:
        self.model = model
        self.engine = AnalyticalEngine(data, model)
        if not model.member_counts:
            model.profile_counts(self.engine.backend.distinct_counts(
                d.column for d in model.dimensions.values()))
        self.graph = InvestigationGraph()
        # What the analyst did, in order - the graph only keeps where they are.
        self.journal = Journal()
        self.commentary = Commentary(model, self.engine, self.journal)
        self.last_explanation: ExplanationResult | None = None
        # One selected mark per node: a child panel keeps its own selection.
        self._sel: dict[str, str | None] = {}
        self.journal_listeners: list[Callable[[JournalEntry], None]] = []

        # The landing view is whatever the model declares - or, opened from a
        # dashboard tile, that tile's Context, so the investigation starts
        # where the tile left off rather than at the model's front door.
        if root is None:
            root, title = root_context(model, self.engine)
            kind = BREAKDOWN
        landed = self.navigate(root, kind,
                               title=title or self.node_title(root, kind))
        self._log(J.START, context=landed.node.context, kind=landed.node.kind,
                  uid=landed.node.uid, title=landed.node.title)

    # ------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------

    @property
    def node(self) -> Node:
        """The node in focus - the chart on top of the stack. Every operation
        applies to it; acting on a chart below raises that one first."""
        n = self.graph.current_node
        assert n is not None
        return n

    def result(self, node: Node | None = None) -> Any:
        node = node or self.node
        return self.engine.execute(node.context, node.kind)

    def selected(self, uid: str | None = None) -> str | None:
        return self._sel.get(uid or self.graph.current or "")

    def select(self, uid: str, key: str | None) -> None:
        """Select a mark. Selecting in a chart lower down does not disturb the
        stack; only an operation raises a panel."""
        if uid in self.graph.nodes:
            self._sel[uid] = key

    def periods(self, grain: str) -> list[str]:
        return self.engine.periods(grain)

    @staticmethod
    def _as_member(node: Node, key: str | None) -> str | None:
        """A mark as the member a verb is scoped to - or None for marks that
        are not members: the one bar of a total, and the points of a
        distribution (single fact cells, with nothing under them)."""
        if key == TOTAL_KEY or node.kind == DISTRIBUTION:
            return None
        return key

    def lens_of(self, node: Node | None = None) -> str | None:
        return LENS_OF_KIND.get((node or self.node).kind)

    # ------------------------------------------------------------------
    # navigation
    # ------------------------------------------------------------------

    def navigate(self, ctx: Context, kind: str = BREAKDOWN, *,
                 op: Operation | None = None, parent: str | None = "__current__",
                 branch: bool = False, title: str = "",
                 in_place: bool = False) -> Landing:
        """Run a context and put its result on the stack.

        By default the result opens *below* the node it came from, which keeps
        the mother chart on top and its children visible beside each other.
        `in_place` is for operations that re-frame the same scope (a different
        metric, period or comparison): those replace the chart on top.
        """
        kind = normal_kind(self.model, ctx, kind)
        try:
            res = self.engine.execute(ctx, kind)
        except SemanticError as exc:
            if op is not None:
                self._log_blocked(str(exc), op.verb)
            raise
        parent_uid = self.graph.current if parent == "__current__" else parent
        existing = None if branch else self._existing_child(parent_uid, ctx, kind)
        if existing is not None:
            node = existing                       # already on the stack
        elif branch and parent_uid:
            node = self.graph.branch_from(
                parent_uid, ctx, kind=kind, op=op,
                title=title or self.node_title(ctx, kind))
        else:
            node = self.graph.add(ctx, kind=kind, op=op, parent=parent_uid,
                                  title=title or self.node_title(ctx, kind))
        member = op.param_map.get("member") if op else None
        # The clicked member stays selected only where it is still a mark: a
        # drill re-splits the scope, and "Mindanao" is not a Customer Segment.
        # Carrying it over would scope the next lens to a member that does
        # not exist, and the chart would come back empty.
        self._sel[node.uid] = (member if member is not None
                               and res.row(str(member)) is not None else None)
        reused = existing is not None

        if in_place or node.parent != parent_uid or parent_uid is None:
            self.graph.current = node.uid
            message = ""
            if in_place and parent_uid is not None and node.uid != parent_uid:
                # A re-framing takes the top slot, so say so - and how to get
                # back - rather than let the chart it replaced vanish silently.
                back = self.graph.get(node.parent).title if node.parent else ""
                # A comparison or period keeps the title, so name the change.
                what = crumb(self.model, node)
                head = (node.title if what.lower() in node.title.lower()
                        else f"{node.title} — {what}")
                message = (f"Replaced the top chart: {head}"
                           + (f"   ·   ↑ Parent goes back to {back}" if back else ""))
            return Landing(node, reused, below=False, message=message)

        # The mother keeps the top slot; the new view opens under it.
        self.graph.current = parent_uid
        if member:
            self._sel[parent_uid] = member        # mark the drilled-from bar
        verb = op.verb.replace("_", " ") if op else "view"
        return Landing(node, reused, below=True, message=(
            f"{'Already open' if reused else 'Opened'} below: {node.title}"
            f"   ·   raise it to the top to drill further ({verb})"))

    def _existing_child(self, parent_uid: str | None, ctx: Context,
                        kind: str) -> Node | None:
        """The same drill twice should scroll to the chart, not stack a copy."""
        if not parent_uid or parent_uid not in self.graph.nodes:
            return None
        for child in self.graph.child_nodes(parent_uid):
            if child.context.id == ctx.id and child.kind == kind:
                return child
        return None

    def goto(self, uid: str) -> Node:
        """Put a node on top of the stack."""
        if uid not in self.graph.nodes:
            raise KeyError(uid)
        moved = uid != self.graph.current
        node = self.graph.goto(uid)
        self.result(node)                         # raises if it no longer resolves
        if moved:
            self._log(J.RETURN, context=node.context, kind=node.kind,
                      uid=node.uid, title=node.title)
        return node

    def raise_node(self, uid: str) -> Landing | None:
        """Raise a chart from below to the top. None if it already is."""
        if uid == self.graph.current or uid not in self.graph.nodes:
            return None
        node = self.goto(uid)
        n = len(node.children)
        deeper = (f"   ·   {n} view{'s' if n != 1 else ''} already below it"
                  if n else "")
        return Landing(node, True, below=False, message=(
            f"Raised: {node.title}   ·   drills from here open beneath it"
            f"{deeper}"))

    def close(self, uid: str) -> str | None:
        """Take a chart - and anything drilled out of it - off the stack.

        The chart on top cannot be closed. Returns a message, or None if
        nothing was closed.
        """
        node = self.graph.nodes.get(uid)
        if node is None or node.uid == self.graph.current:
            return None
        title = node.title
        self._log(J.CLOSE, context=node.context, kind=node.kind, uid=uid,
                  title=title, count=len(list(self.graph.walk(uid))) - 1)
        gone = self.graph.remove(uid)
        for dead in gone:
            self._sel.pop(dead, None)
        buried = len(gone) - 1
        return f"Closed: {title}" + (
            f" and {buried} view{'s' if buried != 1 else ''} below it"
            if buried else "")

    def back(self) -> Landing | None:
        """Raise the parent of the chart on top. None at the root."""
        node = self.node
        return self.raise_node(node.parent) if node.parent else None

    # ------------------------------------------------------------------
    # operations
    # ------------------------------------------------------------------

    def run_from(self, uid: str | None, verb: str, params: dict[str, Any], *,
                 member: str | None = None, branch: bool = False,
                 source: str = "ui") -> Landing | ExplanationResult:
        """Run an operation on the panel it was invoked from.

        Operations always apply to the chart on top, so acting on a chart
        lower down raises it first - that is the promotion gesture, done
        implicitly.
        """
        if uid and uid in self.graph.nodes and uid != self.graph.current:
            if member:
                self._sel[uid] = member
            self.raise_node(uid)
        return self.run(verb, params, member=member, branch=branch,
                        source=source)

    def run(self, verb: str, params: dict[str, Any] | None = None, *,
            member: str | None = None, branch: bool = False,
            source: str = "ui") -> Landing | ExplanationResult:
        """Apply a verb to the chart on top. `explain` returns the
        explanation; every other verb returns where it landed."""
        member = self._as_member(self.node, member)
        if verb == "explain":
            return self.explain(member=member)
        p = dict(params or {})
        if member and member_scoped(verb):
            p["member"] = member
        if verb == "set_grain" and "end" not in p:
            # Only the data layer knows where the data actually stops.
            p["end"] = self.engine.latest(
                p.get("grain", self.node.context.time.grain))
            p.pop("latest", None)
        prior = self.node
        try:
            op = Operation.of(verb, source, **p)
            new_ctx = apply(self.model, prior.context, op)
        except SemanticError as exc:
            self._log_blocked(str(exc), verb, p)
            raise
        except Exception as exc:                              # pragma: no cover
            self._log_blocked(str(exc), verb, p)
            raise SemanticError(str(exc)) from exc
        kind = landing_kind(self.model, prior.kind, new_ctx, op)
        natural = result_kind(self.model, new_ctx, op)
        try:
            landed = self.navigate(new_ctx, kind, op=op, branch=branch,
                                   in_place=in_place(verb))
        except SemanticError as exc:
            if kind == natural:
                raise
            # The lens was only carried over, and it cannot be read here -
            # too little history for exceptions, say. The move itself is
            # fine: land on its own reading, and say why the lens was dropped.
            self.journal.entries.pop()            # the blocked entry
            kind = natural
            landed = self.navigate(new_ctx, kind, op=op, branch=branch,
                                   in_place=in_place(verb))
            landed = Landing(landed.node, landed.reused, landed.below,
                             message=f"{landed.message or landed.node.title}"
                                     f"   ·   Shown as a "
                                     f"{kind.replace('_', ' ')}: {exc}")
        self._log_landing(J.OPERATION, landed, prior, verb=verb, params=p,
                          source=source, context=new_ctx, kind=kind,
                          branch=branch)
        return landed

    def activate(self, uid: str, key: str) -> Landing | None:
        """Double-click: the most natural drill from here, opened below."""
        node = self.graph.nodes.get(uid) or self.node
        if self._as_member(node, key) is None:
            return None                   # nothing under a single point
        ctx = node.context
        current = ctx.grain[0] if ctx.grain else None
        child = self.model.child_dimension(current) if current else None
        source = f"mark:{key}/dblclick"
        if child:
            return self.run_from(node.uid, "drill_down", {"dimension": child},
                                 member=key, source=source)
        alts = self.model.alternative_dimensions(ctx)
        if alts:
            return self.run_from(node.uid, "decompose", {"dimension": alts[0]},
                                 member=key, source=source)
        return None

    def branch_options(self) -> list[str]:
        """Dimensions a competing hypothesis could break the focus down by."""
        return self.model.alternative_dimensions(self.node.context)[:6]

    def branch(self, dimension: str) -> Landing:
        """Open a competing hypothesis beside the current path."""
        return self.run("decompose", {"dimension": dimension},
                        member=self.selected(), branch=True, source="branch")

    def toggle_pin(self, uid: str | None = None) -> str:
        """Pin or unpin a node (the focus by default), raising it first."""
        if uid and uid != self.graph.current:
            self.raise_node(uid)
        node = self.node
        if node.pinned:
            self.graph.unpin(node.uid)
            self._log(J.UNPIN, context=node.context, kind=node.kind,
                      uid=node.uid, title=node.title)
            return "Unpinned"
        res = self.result(node)
        metric = self.model.metric(node.context.metric)
        self.graph.pin(node.uid,
                       f"{metric.label} {fmt.value(metric, res.total, short=True)}")
        # The pin's note is generated from the reading, so the commentary
        # tells the reading itself rather than quoting it back.
        self._log(J.PIN, context=node.context, kind=node.kind,
                  uid=node.uid, title=node.title)
        return "Pinned for comparison and the report"

    # -- toolbar ---------------------------------------------------------

    def set_metric(self, name: str) -> Landing | None:
        if name == self.node.context.metric:
            return None
        return self.run("switch_metric", {"metric": name}, source="toolbar")

    def set_period(self, key: str) -> Landing | None:
        """Read a different period. A trend keeps its length and ends there."""
        ctx = self.node.context
        if ctx.grain and ctx.grain[0] == self.model.time_column:
            from . import timegrain as tg
            return self.run("set_time",
                            {"start": tg.add(key, ctx.time.grain,
                                             -(ctx.time.length - 1)),
                             "end": key, "grain": ctx.time.grain},
                            source="toolbar")
        if (ctx.time.start, ctx.time.end) == (key, key):
            return None
        return self.run("set_time", {"start": key, "end": key,
                                     "grain": ctx.time.grain}, source="toolbar")

    def set_time_grain(self, grain: str) -> Landing | None:
        if grain == self.node.context.time.grain:
            return None
        return self.run("set_grain", {"grain": grain, "latest": True},
                        source="toolbar")

    def set_comparison(self, name: str | None) -> Landing | None:
        if name == self.node.context.comparison:
            return None
        if name is None:
            return self.run("clear_comparison", {}, source="toolbar")
        return self.run("compare", {"period": name}, source="toolbar")

    def lens(self, name: str) -> Landing | None:
        """Read the focus through a lens: composition, change, trend, ..."""
        kind, verb = LENSES[name]
        if verb is None:
            ctx = self.node.context
            if ctx.grain and ctx.grain[0] == self.model.time_column:
                alts = self.model.alternative_dimensions(ctx)
                if not alts:
                    return None
                return self.run("decompose", {"dimension": alts[0]},
                                source="lens")
            prior = self.node
            landed = self.navigate(ctx, BREAKDOWN,
                                   title=self.node_title(ctx, BREAKDOWN))
            self._log_landing(J.OPERATION, landed, prior, verb="composition",
                              source="lens", context=ctx, kind=BREAKDOWN)
            return landed
        n = self.model.trend_periods(self.node.context.time.grain)
        params: dict[str, Any] = {"periods": n} if verb == "trend" else {}
        member = self.selected()
        ctx = self.node.context
        if verb in ("change_contribution", "exceptions") and (
                not ctx.grain
                or (len(ctx.grain) == 1 and on_time(self.model, ctx))):
            return self._lens_by_first_dimension(verb, member)
        grain = self.node.context.grain
        if (hierarchy_scoped(verb) and member is not None
                and not (grain and self.model.child_dimension(grain[0]))):
            # A leaf member cannot be broken down further; read the whole
            # view instead of blocking the lens.
            member = None
        return self.run(verb, params, member=member, source="lens")

    def _lens_by_first_dimension(self, verb: str, member: str | None
                                 ) -> Landing | None:
        """A change or exceptions lens on a total or a time series has
        nothing to attribute to. Split it by the first dimension on offer -
        as the composition lens does - at the selected point of a series
        (the latest by default), and land that one chart under it."""
        prior = self.node
        ctx = prior.context
        alts = self.model.alternative_dimensions(ctx)
        if not alts:
            return None
        params: dict[str, Any] = {"dimension": alts[0]}
        if ctx.grain:                              # a time series
            params["member"] = member or ctx.time.end
        try:
            split = apply(self.model, ctx, Operation.of(
                "decompose", "lens", **params))
            op = Operation.of(verb, "lens")
            new_ctx = apply(self.model, split, op)
        except SemanticError as exc:
            self._log_blocked(str(exc), verb)
            raise
        kind = landing_kind(self.model, prior.kind, new_ctx, op)
        landed = self.navigate(new_ctx, kind, op=op)
        self._log_landing(J.OPERATION, landed, prior, verb=verb, params={},
                          source="lens", context=new_ctx, kind=kind)
        return landed

    # ------------------------------------------------------------------
    # explain
    # ------------------------------------------------------------------

    def explain(self, *, member: str | None = None) -> ExplanationResult:
        """Explain the focus - or one member of it. On a time series the
        member is a period: the point is explained, the latest by default."""
        member = self._as_member(self.node, member)
        ctx = self.node.context
        if len(ctx.grain) == 1 and on_time(self.model, ctx):
            period = member or ctx.time.end
            ctx = ctx.evolve(LineageStep(
                "collapse", f"Read {period}", (("period", period),),
                "explain"), grain=(),
                time=TimeWindow.single(period, ctx.time.grain))
            member = None
        if member and len(ctx.grain) == 2:
            # A cell of a matrix: pin both of its axes, then explain the value.
            filters, window = narrow(self.model, ctx, member)
            ctx = ctx.evolve(LineageStep(
                "focus", f"Focus on {member}", (("member", member),),
                "explain"), filters=filters, grain=(), time=window)
            member = None
        ctx = aggregate_view(self.model, ctx)
        if member:
            dim = ctx.grain[0] if ctx.grain else None
            if dim and dim != self.model.time_column:
                ctx = ctx.with_filter(dim, member, LineageStep(
                    "focus", f"Focus on {member}",
                    (("dimension", dim), ("member", member)), "explain"))
        params = {"member": member} if member else None
        try:
            exp = explain(self.engine, self.model, ctx)
        except SemanticError as exc:
            self._log_blocked(str(exc), "explain", params)
            raise
        self._log(J.EXPLAIN, context=ctx, kind=BREAKDOWN, uid=self.node.uid,
                  title=self.node.title, params=params)
        self.last_explanation = exp
        return exp

    def open_evidence(self, ev: Evidence) -> Landing | None:
        """Open the view a piece of evidence points at."""
        if ev.target is None:
            return None
        op = Operation.of("focus", "explain-evidence",
                          **({"dimension": ev.dimension, "member": ev.member}
                             if ev.dimension and ev.member else {}))
        ctx = ev.target
        # The evidence says how it wants to be read; a time grain settles it.
        kind = ev.target_kind or BREAKDOWN
        if ctx.grain and ctx.grain[0] == self.model.time_column:
            kind = TIMESERIES
        prior = self.node
        landed = self.navigate(ctx, kind, op=op if ev.member else None,
                               title=ev.headline)
        self._log_landing(J.EVIDENCE, landed, prior, context=ctx, kind=kind,
                          detail=ev.headline)
        return Landing(landed.node, landed.reused, landed.below,
                       message=f"Opened evidence: {ev.headline}")

    # ------------------------------------------------------------------
    # the grammar, advertised
    # ------------------------------------------------------------------

    def menu(self, uid: str | None = None, key: str | None = None) -> Menu:
        """The context menu for a mark (or a chart's background).

        Built from the semantic model's capabilities, so the same selected
        context exposes the same grammar no matter which chart type - or
        which panel of the stack - it was selected from.
        """
        node = self.graph.nodes.get(uid or "") or self.node
        if key is not None:
            self._sel[node.uid] = key
        ctx = node.context
        below = node.uid != self.graph.current

        heading = str(ctx)
        if key is not None:
            row = self.result(node).row(key)
            heading = row.label if row is not None else key
            if row is not None:
                metric = self.model.metric(ctx.metric)
                heading += f" — {fmt.value(metric, row.value, short=True)}"
        # The total bar, or one point of a distribution, offers what the
        # whole view offers; no item is scoped to it as if it were a member.
        key = self._as_member(node, key)

        source = f"view:{node.uid}/mark={key}" if key else f"view:{node.uid}"
        by_group: dict[str, list] = {}
        for cap in self.model.capabilities(ctx):
            by_group.setdefault(cap.group, []).append(cap)

        groups: list[list[MenuItem]] = []
        for group in GROUP_ORDER:
            caps_in = by_group.get(group)
            if not caps_in:
                continue
            nested = group == "Metric" and len(caps_in) > 2
            items: list[MenuItem] = []
            for cap in caps_in:
                label = cap.label
                member = key
                if node.kind == CHANGE and cap.verb == "change_contribution":
                    continue      # already read as change; a split says it
                if node.kind == CHANGE and key is not None and \
                        carries_change(cap.verb):
                    dim = self.model.label_of(cap.param_map["dimension"])
                    label = f"Break down {key}'s change by {dim}"
                elif key is not None and cap.verb == "cross":
                    # A member crosses along its own hierarchy; a leaf, or a
                    # point in time, has none, so the item is the chart's.
                    grain = ctx.grain[0] if len(ctx.grain) == 1 else None
                    child = (self.model.child_dimension(grain)
                             if grain and grain != self.model.time_column
                             else None)
                    with_ = label.replace("Cross with ", "")
                    if child is None:
                        label, member = f"Cross this chart with {with_}", None
                    else:
                        label = (f"Cross {key} by "
                                 f"{self.model.label_of(child)} × {with_}")
                elif key is not None and hierarchy_scoped(cap.verb):
                    # Within one member these read along its hierarchy
                    # only; a leaf member has nowhere to go.
                    child = (self.model.child_dimension(ctx.grain[0])
                             if ctx.grain else None)
                    if child is None:
                        continue
                    by = self.model.label_of(child)
                    label = (f"Break down {key}'s change by {by}"
                             if cap.verb == "change_contribution" else
                             f"Find exceptions within {key} by {by}")
                elif key is not None and member_scoped(cap.verb):
                    label = _member_label(cap.verb, label, key,
                                          ctx.time.grain, cap.param_map)
                if nested:
                    label = label.replace("Switch metric to ", "")
                items.append(MenuItem(
                    label, verb=cap.verb, params=dict(cap.param_map),
                    member=member, source=source, hint=cap.hint or "",
                    submenu="Switch metric" if nested else "",
                    lands=lands(cap.verb)))
            if items:
                groups.append(items)

        tail: list[MenuItem] = []
        if key is not None:
            dim = ctx.grain[0] if len(ctx.grain) == 1 else None
            if dim and dim != self.model.time_column:
                tail.append(MenuItem(
                    f"Focus on {key} (keep this grain)", verb="focus",
                    params={"dimension": dim, "member": key}, source=source,
                    lands=lands("focus")))
        if below:
            tail.append(MenuItem("Raise this chart to the top", action="raise"))
            tail.append(MenuItem("Close this chart", action="close"))
        tail.append(MenuItem("Branch from here…", action="branch"))
        tail.append(MenuItem("Unpin this node" if node.pinned
                             else "Pin this node", action="pin"))
        groups.append(tail)

        note = ("From a chart below - choosing an item raises it to the top"
                if below else "")
        return Menu(heading, note, groups)

    def invoke(self, uid: str, item: MenuItem) -> Landing | ExplanationResult | str | None:
        """Carry out a menu item chosen on `uid`'s panel. "branch" needs a
        dimension, so the front end asks for one and calls `branch()`."""
        if item.action == "run":
            return self.run_from(uid, item.verb, item.params,
                                 member=item.member, source=item.source)
        if item.action == "raise":
            return self.raise_node(uid)
        if item.action == "close":
            return self.close(uid)
        if item.action == "pin":
            return self.toggle_pin(uid)
        return None

    # ------------------------------------------------------------------
    # reading
    # ------------------------------------------------------------------

    def mark_summary(self, uid: str, key: str) -> str:
        """One line about a mark: its value, share and change."""
        node = self.graph.nodes.get(uid) or self.node
        row = self.result(node).row(key)
        if row is None:
            return ""
        metric = self.model.metric(node.context.metric)
        bits = [f"{row.label}: {fmt.value(metric, row.value)}"]
        if row.share is not None:
            bits.append(f"{row.share:.1%} of total")
        if row.delta is not None:
            bits.append(f"{fmt.signed(metric, row.delta)} vs reference")
        return "  ·  ".join(bits)

    def status(self, node: Node | None = None) -> str:
        """The focus in one line: scope, total, change, cost."""
        node = node or self.node
        res = self.result(node)
        metric = self.model.metric(node.context.metric)
        total = fmt.value(metric, res.total)
        delta = (f"   ·   {fmt.signed(metric, res.delta_total)} vs reference"
                 if res.delta_total is not None else "")
        return (f"{node.context}   ·   {total}{delta}   ·   {res.fact_rows:,} "
                f"fact rows in {res.computed_ms:.0f} ms   ·   cache "
                f"{self.engine.cache_size()}")

    def crumb(self, node: Node) -> str:
        return crumb(self.model, node)

    def node_title(self, ctx: Context, kind: str) -> str:
        return title_for(self.model, ctx, normal_kind(self.model, ctx, kind))

    # ------------------------------------------------------------------
    # journal
    # ------------------------------------------------------------------

    def _log_landing(self, action: str, landed: Landing, prior: Node,
                     **fields: Any) -> None:
        """Record a move that landed on a chart.

        A move that lands on a chart already open is recorded as `reused`;
        the commentary does not retell a repeat. If the repeat did move the
        focus - an in-place move back onto an existing chart - that is a real
        step, recorded as a return to it.
        """
        node = landed.node
        self._log(action, uid=node.uid, title=node.title,
                  prior=prior.context, prior_kind=prior.kind,
                  reused=landed.reused, **fields)
        if landed.reused and self.graph.current == node.uid != prior.uid:
            self._log(J.RETURN, context=node.context, kind=node.kind,
                      uid=node.uid, title=node.title)

    def _log_blocked(self, reason: str, verb: str,
                     params: dict[str, Any] | None = None) -> None:
        """A dead end is part of the story; it goes in the commentary too."""
        if verb and self.graph.current:
            node = self.node
            self._log(J.BLOCKED, verb=verb, params=params or {},
                      context=node.context, kind=node.kind, uid=node.uid,
                      title=node.title, detail=reason)

    def _log(self, action: str, **fields: Any) -> None:
        # Where the node sits in the map, captured now: a closed chart leaves
        # the graph, but its moves keep their place in the tree.
        uid = fields.get("uid")
        if uid in self.graph.nodes and "parent" not in fields:
            fields["parent"] = self.graph.get(uid).parent
        entry = self.journal.record(action, **fields)
        for listener in self.journal_listeners:
            listener(entry)


def landing_kind(model: SemanticModel, prior_kind: str, ctx: Context,
                 op: Operation) -> str:
    """The result family a move lands on, read from the node it came from.

    The one place this is decided - the session, dashboard replay and the
    "How it works" lab all ask here:

    * the grain settles it first: two axes are a matrix, time a time series;
    * a verb that re-frames the same node (a metric, period, comparison or
      time grain) keeps the lens it was applied to;
    * a verb that splits the population again keeps a *change* reading, so
      breaking a +1.6M movement down shows how the 1.6M divides - not the two
      periods side by side;
    * a change reading needs a comparison and one dimension to attribute the
      movement to; without them it falls back to a breakdown.
    """
    kind = result_kind(model, ctx, op)
    if kind == BREAKDOWN:
        if preserves_kind(op.verb) and prior_kind not in (TIMESERIES, MATRIX):
            kind = prior_kind
        elif carries_change(op.verb) and prior_kind == CHANGE:
            kind = CHANGE
    if kind == CHANGE and not (ctx.comparison and len(ctx.grain) == 1
                               and not on_time(model, ctx)):
        kind = BREAKDOWN
    return kind


def root_context(model: SemanticModel, engine: AnalyticalEngine
                 ) -> tuple[Context, str]:
    """Where an investigation starts by default, and what to call it: the
    model's default metric, its landing grain, the latest period."""
    grain = model.first_grain()
    metric = model.metric(model.default_metric)
    root = Context.new(
        model.dataset, model.default_metric,
        TimeWindow.single(engine.latest(model.time_grain), model.time_grain),
        grain=grain)
    return root, (f"{metric.label} by {model.label_of(grain[0])}"
                  if grain else metric.label)


def lands(verb: str) -> str:
    """Where a verb's result goes on the stack: "replace" takes the top slot,
    "below" opens under the chart on top, "" is not a chart at all."""
    if verb == "explain":
        return ""
    return "replace" if in_place(verb) else "below"


def crumb(model: SemanticModel, node: Node) -> str:
    """Name what the step *did*, so two nodes at the same grain don't read
    as the same crumb."""
    ctx = node.context
    op = node.op
    if op and op.param_map.get("member"):
        return str(op.param_map["member"])
    if node.kind in KIND_CRUMB:
        return KIND_CRUMB[node.kind]
    if node.kind == MATRIX and len(ctx.grain) == 2:
        return " × ".join(model.label_of(g) for g in ctx.grain)
    if op and op.verb == "set_grain":
        return f"by {ctx.time.grain}"
    if op and op.verb == "compare":
        return f"vs {str(op.param_map.get('period', '')).upper()}"
    if op and op.verb == "clear_comparison":
        return "no comparison"
    if op and op.verb == "switch_metric":
        return model.metric(ctx.metric).label
    if op and op.verb == "set_time":
        return ctx.time.label
    if ctx.grain:
        g = ctx.grain[0]
        return ("Trend" if g == model.time_column
                else f"by {model.label_of(g)}")
    return model.metric(ctx.metric).label


def _member_label(verb: str, label: str, key: str, grain: str = "month",
                  params: dict[str, Any] | None = None) -> str:
    if verb == "drill_down":
        return label.replace("Drill down to", f"Drill into {key} by")
    if verb == "decompose":
        return label.replace("Break down by", f"Break {key} down by")
    if verb == "trend":
        n = (params or {}).get("periods", 12)
        return f"Trend {key} over {n} {grain}s"
    if verb == "distribution":
        return f"Show distribution within {key}"
    return label
