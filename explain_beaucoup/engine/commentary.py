"""Running commentary: the analyst's moves, told as they happen.

`narrate_investigation` describes where the investigation *is* - the path to
the current node. This module describes what the analyst *did*: every drill,
comparison, return, dead end and pin, in order.

Every analytical move is asked for a reason, so every beat has three parts:

    move      what was done              "Drilled into Luzon by Province."
    question  why anyone does that       "What is driving Revenue in Luzon?"
    answer    what the numbers say       "Metro Manila (-₱337K) and ... drive
                                          83% of the decline; Batangas (+₱40K)
                                          pulled the other way."

The answer is the point. A drill or a breakdown is asked to find what moves
the number, so it is answered with drivers and offsets, not with whichever
member happens to be largest.

It is deterministic in the same sense as the rest of the engine. A beat is a
pure function of a JournalEntry and the numbers the engine computes for the
entry's Context, so the same journal always yields the same commentary, word
for word. The entry snapshots its Context, so a beat still renders after its
chart has been closed.

Phrasing lives in `PHRASES`, one function per verb. Adding a verb means adding
one entry there; an unknown verb falls back to its lineage summary rather than
failing.
"""

from __future__ import annotations

import dataclasses
import html
import re
from dataclasses import dataclass
from typing import Any, Callable, NamedTuple

from ..core import journal as J
from ..core import timegrain as tg
from ..core.context import Context
from ..core.journal import Journal, JournalEntry
from ..core.operations import BREAKDOWN, EXCEPTIONS, TIMESERIES
from ..semantic.specs import SemanticError, SemanticModel
from ..view import format as fmt
from .engine import AnalyticalEngine, ResultHandle, Row, comparison_label

# How far from its own baseline a member must be before it "stands out".
NOTABLE_Z = 2.0
# Drivers and offsets named per beat - enough to answer, few enough to read.
MAX_DRIVERS = 3
MAX_OFFSETS = 2
# A member moving less than this share of the total movement is noise, not a
# driver (the first driver is always named).
MIN_SHARE_OF_MOVE = 0.05
# Members named when there is no movement to explain, only a composition.
MAX_CONTRIBUTORS = 3
# Layouts: nested like the investigation map, or the journal in order.
TREE = "tree"
TIMELINE = "timeline"


class Told(NamedTuple):
    """What a phraser returns: the move, the question it asks, its answer."""

    move: str
    question: str = ""
    answer: str = ""


@dataclass(frozen=True)
class Beat:
    """One entry of commentary, tied back to the move that produced it."""

    seq: int
    action: str
    move: str                 # markdown; `[..](node:uid)` links to a chart
    question: str
    answer: str
    uid: str | None
    context_id: str | None
    selected: str = ""        # where the clicked member stood, before the move
    parent: str | None = None  # the chart's parent in the map, when recorded
    number: int = 0           # position among the beats actually shown

    @property
    def text(self) -> str:
        """The beat as a single line of prose."""
        q = f"_{self.question}_" if self.question else ""
        return " ".join(b for b in (self.move, self.selected, q, self.answer)
                        if b)

    def markdown(self) -> str:
        """The move (and what was clicked) on the list line; question and
        answer beneath it."""
        head = f"{self.move} {self.selected}" if self.selected else self.move
        if not (self.question or self.answer):
            return head
        body = " ".join(b for b in (
            f"_{self.question}_" if self.question else "", self.answer) if b)
        return f"{head}\n\n{body}"


@dataclass(frozen=True)
class Movers:
    """Who drove a movement and who pushed against it."""

    res: ResultHandle
    delta: float
    drivers: tuple[tuple[Row, float], ...]      # (row, its delta)
    offsets: tuple[tuple[Row, float], ...]
    driver_share: float | None                  # of the total move; additive only
    implied: bool                               # comparison chosen for the answer
    moved: int = 0                              # members that moved at all


Phraser = Callable[["_Say"], Told]


class _Say:
    """What a phraser can see: the entry and cached engine readings."""

    def __init__(self, model: SemanticModel, engine: AnalyticalEngine,
                 entry: JournalEntry) -> None:
        self.model = model
        self.engine = engine
        self.e = entry
        self.p = entry.param_map
        # A member only scopes a move if it was a mark on the chart the move
        # came from. On a chart with no split there is nothing to click, and
        # the grammar drops it - so must the commentary.
        prior = entry.prior
        if "member" in self.p and prior is not None and not prior.grain:
            self.p.pop("member")

    # -- readings ----------------------------------------------------------

    def read(self, ctx: Context | None = None, kind: str | None = None
             ) -> ResultHandle | None:
        ctx = ctx or self.e.context
        if ctx is None:
            return None
        try:
            return self.engine.execute(ctx, kind or self.e.kind or BREAKDOWN)
        except SemanticError:
            return None

    @property
    def res(self) -> ResultHandle | None:
        return self.read()

    @property
    def ctx(self) -> Context:
        assert self.e.context is not None
        return self.e.context

    @property
    def metric(self):
        return self.model.metric(self.ctx.metric)

    @property
    def split(self) -> str | None:
        """The dimension this node is broken down by, if it is one."""
        d = self.ctx.grain[0] if self.ctx.grain else None
        return None if d == self.model.time_column else d

    # -- analysis ----------------------------------------------------------

    def _edges(self) -> tuple[str, str, tg.Grain]:
        """First and last period of the data, at the data's own grain."""
        ts = self.engine.source.timestamps
        n = self.engine.source.native_grain
        return tg.key_of(ts.min(), n), tg.key_of(ts.max(), n), n

    def partial(self, window: Any) -> bool:
        first, last, n = self._edges()
        lo, hi = window.bounds
        return tg.key_of(hi, n) > last or tg.key_of(lo, n) < first

    def missing(self, window: Any) -> bool:
        first, last, n = self._edges()
        lo, hi = window.bounds
        return tg.key_of(hi, n) < first or tg.key_of(lo, n) > last

    def issue(self, ctx: Context) -> tuple[str, str] | None:
        """Why a reading of `ctx` cannot be taken at face value, if it can't.

        A period that is still running, or a reference with no data behind
        it, turns a comparison into an artefact of the calendar. The
        commentary says so instead of naming "drivers" of missing time.
        """
        first, last, n = self._edges()
        now = ctx.time
        if ctx.comparison:
            ref = comparison_label(self.model, ctx)
            before = self.model.comparison(ctx.comparison).shift(now)
            if self.missing(before):
                return ("missing", f"There is no data for {ref} - the data "
                        f"starts {tg.label_of(first, n)} - so there is "
                        f"nothing to measure against.")
            if self.partial(now):
                return ("partial", f"Careful: {now.label} only runs through "
                        f"{tg.label_of(last, n)}, so an incomplete period is "
                        f"being set against a complete one - the gap is not "
                        f"like for like.")
            if self.partial(before):
                return ("partial", f"Careful: {ref} is only partly in the "
                        f"data, so the gap is not like for like.")
        elif self.partial(now):
            return ("partial", f"{now.label} only runs through "
                    f"{tg.label_of(last, n)} so far.")
        return None

    def with_reference(self, ctx: Context) -> tuple[Context, bool] | None:
        """The context with a comparison: its own, else the model's first
        valid one that has data behind it. A breakdown is asked what drives
        the number, and that is only answerable against a reference."""
        if ctx.comparison:
            return ctx, False
        for c in self.model.comparisons:
            cand = dataclasses.replace(ctx, comparison=c.name)
            if self.model.is_valid(cand)[0] and self.issue(cand) is None:
                return cand, True
        return None

    def movers(self, ctx: Context | None = None) -> Movers | None:
        """Drivers and offsets of the movement across this node's split."""
        ctx = ctx or self.ctx
        if not ctx.grain or ctx.grain[0] == self.model.time_column:
            return None
        ref = self.with_reference(ctx)
        if ref is None:
            return None
        cctx, implied = ref
        if self.issue(cctx) is not None:
            return None
        res = self.read(cctx, BREAKDOWN)
        if res is None or res.delta_total is None:
            return None
        ratio = self.metric.is_ratio
        moves: list[tuple[Row, float]] = []
        for r in res.rows:
            d = r.delta
            if d is None and not ratio:
                # A member that vanished lost all of its prior value; one that
                # appeared gained all of its current value.
                if r.value is None and r.prior:
                    d = -r.prior
                elif r.prior is None and r.value:
                    d = r.value
            if d:
                moves.append((r, d))
        total = res.delta_total
        sign = 1 if total >= 0 else -1
        floor = abs(total) * MIN_SHARE_OF_MOVE if total else 0.0
        with_ = sorted((m for m in moves if m[1] * sign > 0),
                       key=lambda m: (-abs(m[1]), m[0].key))
        against = sorted((m for m in moves if m[1] * sign < 0),
                         key=lambda m: (-abs(m[1]), m[0].key))
        drivers = tuple(m for i, m in enumerate(with_[:MAX_DRIVERS])
                        if i == 0 or abs(m[1]) >= floor)
        offsets = tuple(m for m in against[:MAX_OFFSETS] if abs(m[1]) >= floor)
        share = (sum(d for _, d in drivers) / total
                 if total and not ratio else None)
        return Movers(res, total, drivers, offsets, share, implied, len(moves))

    # -- phrases -----------------------------------------------------------

    def v(self, x: float | None) -> str:
        return fmt.value(self.metric, x, short=True)

    def sv(self, x: float | None) -> str:
        # A percentage moves in points; "+0.3%" would read as a relative change.
        if self.metric.fmt == "percent" and x is not None:
            return f"{x * 100:+.2f} pts"
        return fmt.signed(self.metric, x)

    def dim(self, d: str | None) -> str:
        return self.model.label_of(d)

    def link(self, text: str) -> str:
        return f"[{text}](node:{self.e.uid})" if self.e.uid else f"**{text}**"

    @property
    def period_member(self) -> bool:
        """The member was a point on a time series - a period, not a filter."""
        prior = self.e.prior
        return bool(self.p.get("member") and prior is not None and prior.grain
                    and prior.grain[0] == self.model.time_column)

    def member_label(self) -> str:
        m = str(self.p.get("member", ""))
        if self.period_member:
            return tg.label_of(m, self.e.prior.time.grain)  # type: ignore[union-attr]
        return m

    def of_member(self) -> str:
        return f" **{self.member_label()}**" if self.p.get("member") else ""

    def who(self) -> str:
        """The member acted on, else the scope - with a leading space."""
        if self.p.get("member") and not self.period_member:
            return self.of_member()
        if self.period_member:
            scope = (f"**{self.ctx.scope_label()}** in "
                     if self.ctx.filters else "")
            return f" {scope}**{self.member_label()}**"
        if self.ctx.filters:
            return f" **{self.ctx.scope_label()}**"
        return " the whole book"

    def where(self) -> str:
        """`in Luzon` / `across the whole book`, with a leading space."""
        who = self.who()
        return " across the whole book" if who == " the whole book" \
            else f" in{who}"

    def change(self, delta: float, pct: float | None) -> str:
        """`down ₱835.1K (-1.8%)` - direction in words, magnitude unsigned."""
        if delta == 0:
            return "flat"
        word = "up" if delta > 0 else "down"
        size = self.sv(abs(delta))[1:]
        tail = (f" ({pct:+.1%})" if pct is not None
                and self.metric.fmt != "percent" else "")
        return f"{word} {size}{tail}"

    def movement(self, res: ResultHandle) -> str:
        """`down ₱805K (-1.3%) against the previous month`."""
        if res.delta_total is None:
            return ""
        iss = self.issue(res.context) if res.kind != TIMESERIES else None
        if iss and iss[0] == "missing":
            return ""
        ref = comparison_label(self.model, res.context) or "the prior period"
        if res.delta_total == 0:
            return f"flat against {ref}"
        word = "up" if res.delta_total > 0 else "down"
        if self.metric.fmt == "percent":
            return f"{word} {self.sv(abs(res.delta_total))[1:]} against {ref}"
        pct = (f" ({res.delta_total / res.prior_total:+.1%})"
               if res.prior_total else "")
        return f"{word} {self.v(abs(res.delta_total))}{pct} against {ref}"

    def level(self, res: ResultHandle | None = None) -> str:
        """`Revenue is ₱60.7M, down ... against the previous month.`"""
        res = res or self.res
        if res is None or res.total is None or res.kind == TIMESERIES:
            return ""
        mv = self.movement(res)
        iss = self.issue(res.context)
        return f"{self.metric.label} is {self.v(res.total)}" + \
            (f", {mv}." if mv else ".") + (f" {iss[1]}" if iss else "")

    def level_ref(self) -> str:
        """The level, measured against a reference even when the chart has
        none set - so "how is it doing" gets a direction, not just a size."""
        ref = self.with_reference(self.ctx)
        if ref is None:
            return self.level()
        cctx, implied = ref
        res = self.read(cctx, BREAKDOWN)
        if res is None:
            return self.level()
        return self.level(res) + (_implied_note(self, res) if implied else "")

    def drivers_answer(self, mv: Movers) -> str:
        """The answer to "what is driving it": drivers, then offsets."""
        res = mv.res
        lead = f"{self.metric.label} is {self.v(res.total)}, " \
               f"{self.movement(res)}."
        if not mv.drivers:
            return lead + " No member moved enough to call a driver."
        change = "increase" if mv.delta > 0 else "decline"
        names = _join([f"{r.label} ({self.sv(d)})" for r, d in mv.drivers])
        verb = "drives" if len(mv.drivers) == 1 else "drive"
        members = [r for r in res.rows if r.value is not None]
        if len(members) == 1:
            return (lead + f" {members[0].label} is the only "
                    f"{self.dim(res.dimension)} here, so it is the whole "
                    f"movement." + (_implied_note(self, res) if mv.implied
                                    else ""))
        if len(mv.drivers) > 1 and mv.moved == len(mv.drivers):
            # Everyone went the same way: a share of 100% says nothing.
            out = (f" Every {self.dim(res.dimension)} "
                   f"{'rose' if mv.delta > 0 else 'fell'}: {names}.")
            if mv.implied:
                out += _implied_note(self, res)
            return lead + out
        if mv.driver_share is None:
            out = f" {names} {verb} the {change}."
        elif mv.driver_share > 1.005:
            out = (f" {names} {'moves' if len(mv.drivers) == 1 else 'move'} "
                   f"more than the whole {change} ({mv.driver_share:.0%} of it).")
        else:
            out = f" {names} {verb} {mv.driver_share:.0%} of the {change}."
        if mv.offsets:
            names = _join([f"{r.label} ({self.sv(d)})" for r, d in mv.offsets])
            out += f" {names} pulled the other way."
        else:
            out += " Nothing pulled the other way."
        if mv.implied:
            out += _implied_note(self, res)
        return lead + out

    def composition_answer(self, res: ResultHandle | None = None) -> str:
        """When there is no movement to explain: who makes up the total."""
        res = res or self.res
        if res is None or res.total is None:
            return ""
        present = [r for r in res.rows if r.value is not None]
        rows = present[:MAX_CONTRIBUTORS]
        if not rows:
            return self.level(res)
        if self.metric.is_ratio:
            names = _join([f"{r.label} ({self.v(r.value)})" for r in rows])
            return f"{self.level(res)} Highest: {names}."
        names = _join([f"{r.label} ({r.share:.0%})" for r in rows
                       if r.share is not None])
        if len(present) == 1:
            return f"{self.level(res)} {rows[0].label} is all of it."
        if len(rows) == len(present):
            return f"{self.level(res)} It splits into {names}."
        top = sum(r.share or 0.0 for r in rows)
        return f"{self.level(res)} {names} make up {top:.0%} of it."

    def breakdown_answer(self, ctx: Context | None = None) -> str:
        """Drivers and offsets where there is a movement, else composition."""
        mv = self.movers(ctx)
        if mv is not None:
            return self.drivers_answer(mv)
        return self.composition_answer(self.read(ctx) if ctx else None)

    def selection(self) -> str:
        """Where the clicked member stood in the chart it was clicked in.

        The move says what was done *to* Visayas; this says what Visayas
        *was* - its level, its weight, its rank and how it was moving - read
        from the node the gesture came from, so the reader knows why it was
        worth clicking.
        """
        member = self.p.get("member")
        prior = self.e.prior
        if member is None or prior is None or not prior.grain:
            return ""
        name = f"**{member}**"
        if prior.grain[0] == self.model.time_column:
            return self._period_selection(prior, str(member), name)
        ref = self.with_reference(prior)
        res = self.read(ref[0] if ref else prior, BREAKDOWN)
        row = res.row(str(member)) if res is not None else None
        if res is None or row is None or row.value is None:
            return ""
        metric = self.model.metric(prior.metric)
        of = metric.label + (f" in {prior.scope_label()}" if prior.filters
                             else "")
        ranked = [r for r in res.rows if r.value is not None]
        rank = next(i for i, r in enumerate(ranked, 1) if r.key == row.key)
        bits = [f"{self.v(row.value)}"]
        weight = []
        only = len(ranked) == 1
        if only:
            where = (f" in {prior.scope_label()}" if prior.filters else "")
            weight.append(f"the only {self.dim(prior.grain[0])}{where}")
        elif row.share is not None:
            weight.append(f"{row.share:.0%} of {of}")
        if not only:
            weight.append(f"{_ordinal(rank)} of {len(ranked)} by "
                          f"{self.dim(prior.grain[0])}")
        if weight:
            bits.append(f"({', '.join(weight)})")
        out = f"{name} stood at {' '.join(bits)}"
        if (row.delta is not None and row.prior is not None
                and self.issue(res.context) is None):
            out += (f", {self.change(row.delta, row.delta_pct)} against "
                    f"{comparison_label(self.model, res.context)}")
            total = res.delta_total
            if total and not metric.is_ratio and row.delta and not only:
                change = "increase" if total > 0 else "decline"
                if row.delta * total > 0:
                    out += f" - {row.delta / total:.0%} of the {change}"
                else:
                    out += f" - moving against the overall {change}"
        elif (row.prior is None and res.context.comparison
              and self.issue(res.context) is None):
            out += (f", with nothing in "
                    f"{comparison_label(self.model, res.context)}")
        return out + "."

    def _period_selection(self, prior: Context, key: str, name: str) -> str:
        """A clicked point on a time series: its level and its step."""
        res = self.read(prior, TIMESERIES)
        row = res.row(key) if res is not None else None
        if row is None or row.value is None:
            return ""
        scope = f" for {prior.scope_label()}" if prior.filters else ""
        out = f"**{row.label}** stood at {self.v(row.value)}{scope}"
        if row.delta is not None:
            ref = (comparison_label(self.model, prior) if prior.comparison
                   else f"the {prior.time.grain} before")
            out += f", {self.change(row.delta, row.delta_pct)} against {ref}"
        vals = sorted((r.value for r in res.rows if r.value is not None),
                      reverse=True)
        if len(vals) > 2 and row.value in (vals[0], vals[-1]):
            out += (f" - the {'high' if row.value == vals[0] else 'low'} of "
                    f"the {len(vals)} {prior.time.grain}s shown")
        if dict(row.extra).get("partial"):
            out += ", though only partly in the data"
        return out + "."

    def driving_question(self) -> str:
        return f"What is driving {self.metric.label}{self.where()}?"


def _implied_note(s: "_Say", res: ResultHandle) -> str:
    return (" _(No comparison is set on the chart; measured against "
            f"{comparison_label(s.model, res.context)}.)_")


def _ordinal(n: int) -> str:
    if n == 1:
        return "largest"
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(
        n % 10, "th")
    return f"{n}{suffix}"


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


# --------------------------------------------------------------------------
# Session moves
# --------------------------------------------------------------------------

def _start(s: _Say) -> Told:
    return Told(f"Opened on {s.link(s.e.title or str(s.ctx))} for "
                f"{s.ctx.time.label}.",
                f"Where does {s.metric.label} come from?",
                s.composition_answer())


def _return(s: _Say) -> Told:
    return Told(f"Turned to {s.link(s.e.title)}.", "", s.level())


def _composition(s: _Say) -> Told:
    return Told(f"Switched to the {s.link('composition')} by "
                f"{s.dim(s.split)}.",
                f"What makes up {s.metric.label}{s.where()}?",
                s.composition_answer())


def _close(s: _Say) -> Told:
    n = s.e.count
    more = ("" if not n else ", and the view drilled out of it" if n == 1
            else f", and the {n} views drilled out of it")
    return Told(f"Set aside **{s.e.title}**{more}.")


def _pin(s: _Say) -> Told:
    # A note is the analyst's own words; without one, the finding is the
    # reading itself.
    note = f": “{s.e.detail}”" if s.e.detail else ""
    return Told(f"Pinned {s.link(s.e.title)} as a finding{note}.", "",
                "" if s.e.detail else s.level())


def _unpin(s: _Say) -> Told:
    return Told(f"Unpinned {s.link(s.e.title)} - no longer a finding.")


def _explain(s: _Say) -> Told:
    from .explain import explain              # heavy; only when asked
    move = "Asked for an explanation."
    question = f"Why does {s.metric.label}{s.where()} look this way?"
    try:
        exp = explain(s.engine, s.model, s.ctx)
    except SemanticError as exc:
        return Told(move, question, f"It could not be explained: {exc}")
    head = ""
    if exp.delta is not None and exp.prior_total:
        word = "up" if exp.delta > 0 else "down"
        head = (f"{s.metric.label} is {word} {s.v(abs(exp.delta))} "
                f"({exp.delta_pct:+.1%}) against "
                f"{comparison_label(s.model, exp.context)}. ")
    if not exp.evidence:
        return Told(move, question,
                    head + "Nothing in the breakdowns tested stood out.")
    lines = "; ".join(ev.headline for ev in exp.top(3))
    best = (f" {s.dim(exp.best_dimension)} explains the most of the "
            f"{len(exp.dimensions_tested)} breakdowns tested."
            if exp.best_dimension else "")
    return Told(move, question, f"{head}The strongest evidence: {lines}.{best}")


def _evidence(s: _Say) -> Told:
    to = ("" if s.e.title == s.e.detail
          else f" to {s.link(s.e.title)}")
    head = (s.link(f"“{s.e.detail}”") if not to else f"“{s.e.detail}”")
    if s.split:
        return Told(f"Followed the evidence {head}{to}.",
                    s.driving_question(), s.breakdown_answer())
    return Told(f"Followed the evidence {head}{to}.",
                f"How does {s.metric.label}{s.where()} look up close?",
                s.level() or _trend(s).answer)


# What a blocked verb was trying to do, as an infinitive.
ATTEMPTS = {
    "drill_down": "drill down", "drill_up": "drill up",
    "decompose": "break it down", "focus": "focus",
    "compare": "add a comparison", "trend": "trace a trend",
    "switch_metric": "switch metric", "set_time": "move the window",
    "set_grain": "change the calendar", "explain": "explain it",
    "change_contribution": "find what moved it",
    "exceptions": "look for exceptions",
    "distribution": "open the distribution",
}


def _blocked(s: _Say) -> Told:
    what = ATTEMPTS.get(s.e.verb, s.e.verb.replace("_", " ") or "do that")
    reason = s.e.detail.rstrip(".")
    return Told(f"Tried to {what}{s.of_member()} - blocked by the model: "
                f"{reason}.")


# --------------------------------------------------------------------------
# Analytical verbs
# --------------------------------------------------------------------------

def _drill_down(s: _Say) -> Told:
    d = s.dim(s.p.get("dimension"))
    move = (f"Drilled into{s.of_member()} by {s.link(d)}."
            if s.p.get("member") else f"Drilled down to {s.link(d)}.")
    return Told(move, s.driving_question(), s.breakdown_answer())


def _decompose(s: _Say) -> Told:
    d = s.dim(s.p.get("dimension"))
    if s.e.branch:
        return Told(f"Opened a competing hypothesis: broke{s.who()} down by "
                    f"{s.link(d)} instead.",
                    f"Does {d} explain {s.metric.label}{s.where()} better?",
                    s.breakdown_answer())
    return Told(f"Broke{s.who()} down by {s.link(d)}.",
                s.driving_question(), s.breakdown_answer())


def _drill_up(s: _Say) -> Told:
    to = s.dim(s.split) if s.split else "the total"
    scope = (f" for **{s.ctx.scope_label()}**" if s.ctx.filters else "")
    return Told(f"Stepped back up to {s.link(to)}{scope}.",
                "How does it sit in the bigger picture?",
                s.breakdown_answer() if s.split else s.level())


def _focus(s: _Say) -> Told:
    return Told(f"Focused on{s.of_member()}, keeping the "
                f"{s.dim(s.split)} view.",
                f"How is{s.of_member() or s.who()} doing on its own?",
                s.level_ref())


def _compare(s: _Say) -> Told:
    ref = comparison_label(s.model, s.ctx) or str(s.p.get("period", "")).upper()
    return Told(f"Set the reference to {s.link(ref)}.",
                f"Has {s.metric.label}{s.where()} moved against {ref}?",
                s.breakdown_answer() if s.split else s.level())


def _clear_comparison(s: _Say) -> Told:
    return Told("Dropped the comparison.", "", s.level())


def _switch_metric(s: _Say) -> Told:
    was = (f" from {s.model.metric(s.e.prior.metric).label}"
           if s.e.prior is not None else "")
    return Told(f"Switched the metric{was} to {s.link(s.metric.label)}.",
                f"What does {s.metric.label} say{s.where()}?",
                s.breakdown_answer() if s.split else s.level())


def _set_time(s: _Say) -> Told:
    move = f"Moved the window to {s.link(s.ctx.time.label)}."
    prior = s.e.prior
    question = (f"How does {s.ctx.time.label} compare with "
                f"{prior.time.label}?" if prior is not None
                else f"How does {s.ctx.time.label} look?")
    if s.e.kind == TIMESERIES:
        # A trend over a different window is a different trend; tell it.
        return Told(move, f"How does the trend read over "
                          f"{s.ctx.time.label}?", _trend(s).answer)
    answer = s.breakdown_answer() if s.split else s.level()
    prev = s.read(prior, BREAKDOWN) if prior is not None else None
    now = s.res
    if (prev is not None and prev.total is not None and now is not None
            and now.total is not None
            and prior.metric == s.ctx.metric):            # type: ignore[union-attr]
        d = now.total - prev.total
        pct = d / prev.total if prev.total else None
        side = "above" if d > 0 else "below"
        answer += (f" That is {s.change(d, pct).split(' ', 1)[1]} {side} "
                   f"{prior.time.label} ({s.v(prev.total)})."  # type: ignore[union-attr]
                   if d else f" The same as {prior.time.label}.")  # type: ignore[union-attr]
        if s.partial(s.ctx.time) or s.partial(prior.time):  # type: ignore[union-attr]
            answer += " One of the two periods is incomplete."
    return Told(move, question, answer)


def _set_grain(s: _Say) -> Told:
    g = s.ctx.time.grain
    move = f"Switched the calendar to {s.link(g + 's')}."
    if s.e.kind == TIMESERIES:
        # A trend re-read at a coarser grain is a new trend; tell it as one.
        told = _trend(s)
        return Told(move, f"How does the trend read by {g}?", told.answer)
    return Told(move, f"How does {s.ctx.time.label} read as a {g}?",
                s.breakdown_answer() if s.split else s.level())


def _trend(s: _Say) -> Told:
    res = s.res
    n = len(s.ctx.time.periods)
    move = f"Traced{s.who()} over {s.link(f'{n} {s.ctx.time.grain}s')}."
    question = f"How has {s.metric.label}{s.where()} moved over time?"
    every = [r for r in (res.rows if res else ()) if r.value is not None]
    # A period still running is not a low, a fall or a sharp move - it is
    # unfinished. Judge the trend on complete periods, then say what's pending.
    pts = [r for r in every if not dict(r.extra).get("partial")]
    partial = [r for r in every if dict(r.extra).get("partial")]
    if len(pts) < 2:
        told = "; ".join(
            f"{r.label} {s.v(r.value)}"
            + (" (incomplete)" if dict(r.extra).get("partial") else "")
            for r in every)
        return Told(move, question,
                    (f"Too few complete {s.ctx.time.grain}s to call a trend: "
                     f"{told}." if every else "No data in this window."))
    first, last = pts[0], pts[-1]
    peak = max(pts, key=lambda r: (r.value, r.key))
    low = min(pts, key=lambda r: (r.value, r.key))
    change = (f" ({(last.value - first.value) / first.value:+.1%})"
              if first.value else "")
    word = ("rose" if last.value > first.value else
            "fell" if last.value < first.value else "held")
    out = (f"It {word} from {s.v(first.value)} in {first.label} to "
           f"{s.v(last.value)} in {last.label}{change}; high "
           f"{s.v(peak.value)} in {peak.label}, low {s.v(low.value)} in "
           f"{low.label}.")
    # The single biggest step is usually the question behind a trend.
    steps = [(b.value - a.value, b) for a, b in zip(pts, pts[1:])]
    if steps:
        d, at = max(steps, key=lambda t: (abs(t[0]), t[1].key))
        if d:
            out += (f" The sharpest move was {s.sv(d)} into {at.label}.")
    for r in partial:
        out += (f" {r.label} is still incomplete ({s.v(r.value)} so far) and "
                f"is left out of that reading.")
    return Told(move, question, out)


def _change_contribution(s: _Say) -> Told:
    return Told(f"Asked what moved{s.who()} by {s.link(s.dim(s.split))}.",
                f"Which {s.dim(s.split)} drove the change, and which held "
                f"it back?",
                s.breakdown_answer())


def _exceptions(s: _Say) -> Told:
    res = s.read(kind=EXCEPTIONS)
    move = f"Looked for {s.link('exceptions')} within{s.who()}."
    question = "Is anything unusual against its own history?"
    if res is None or not res.rows:
        return Told(move, question, "Not enough history to judge.")
    notable = [r for r in res.rows
               if abs(dict(r.extra).get("z", 0.0)) >= NOTABLE_Z]

    def one(r: Row) -> str:
        x = dict(r.extra)
        return (f"{r.label} at {s.v(r.value)} against a usual "
                f"{s.v(x.get('baseline'))} (z = {x.get('z', 0.0):+.1f})")

    if not notable:
        return Told(move, question,
                    f"No. The largest deviation is {one(res.rows[0])}, "
                    f"inside ±{NOTABLE_Z:g}σ.")
    high = [r for r in notable if dict(r.extra).get("z", 0.0) > 0]
    low = [r for r in notable if dict(r.extra).get("z", 0.0) < 0]
    bits = []
    if high:
        bits.append("Unusually high: " + _join([one(r) for r in high[:3]]) + ".")
    if low:
        bits.append("Unusually low: " + _join([one(r) for r in low[:3]]) + ".")
    return Told(move, question, " ".join(bits))


def _distribution(s: _Say) -> Told:
    res = s.res
    move = f"Opened the {s.link('distribution')} underneath{s.who()}."
    question = (f"What sits underneath the {s.metric.label} total"
                f"{s.where()}?")
    rows = sorted((r for r in (res.rows if res else ()) if r.value is not None),
                  key=lambda r: (-r.value, r.key))           # type: ignore[operator]
    if not rows:
        return Told(move, question, "Nothing to distribute at this scope.")
    vals = sorted(r.value for r in rows)                     # type: ignore[type-var]

    def q(p: float) -> float:           # same quantile rule as the engine
        return vals[min(int(p * (len(vals) - 1)), len(vals) - 1)]

    cells = [s.dim(d) for d in s.model.cell_dimensions()
             if d not in s.ctx.filter_map]
    out = (f"{len(rows):,} cells of {' × '.join(cells)}. Half are below "
           f"{s.v(q(.5))}; the middle 80% run from {s.v(q(.1))} to "
           f"{s.v(q(.9))}.")
    top = rows[0]
    out += f" The largest is {top.label} at {s.v(top.value)}"
    if res is not None and res.total and not s.metric.is_ratio:
        k = max(1, len(rows) // 10)
        top_share = sum(r.value for r in rows[:k]) / res.total  # type: ignore[misc]
        out += (f"; the top {k:,} cell{'s' if k != 1 else ''} hold "
                f"{top_share:.0%} of the total")
    return Told(move, question, out + ".")


PHRASES: dict[str, Phraser] = {
    J.START: _start,
    J.RETURN: _return,
    J.CLOSE: _close,
    J.PIN: _pin,
    J.UNPIN: _unpin,
    J.EXPLAIN: _explain,
    J.EVIDENCE: _evidence,
    J.BLOCKED: _blocked,
    "composition": _composition,
    "drill_down": _drill_down,
    "decompose": _decompose,
    "drill_up": _drill_up,
    "focus": _focus,
    "compare": _compare,
    "clear_comparison": _clear_comparison,
    "switch_metric": _switch_metric,
    "set_time": _set_time,
    "set_grain": _set_grain,
    "trend": _trend,
    "change_contribution": _change_contribution,
    "exceptions": _exceptions,
    "distribution": _distribution,
}


def _fallback(s: _Say) -> Told:
    step = s.ctx.lineage[-1].summary if s.ctx.lineage else s.e.verb
    return Told(f"{step}.", "", s.level())


# --------------------------------------------------------------------------
# Public
# --------------------------------------------------------------------------

def narrate_entry(model: SemanticModel, engine: AnalyticalEngine,
                  entry: JournalEntry) -> Beat:
    """Phrase one journal entry. Pure: same entry, same engine, same text."""
    key = entry.verb if entry.action == J.OPERATION else entry.action
    phraser = PHRASES.get(key, _fallback)
    told = phraser(_Say(model, engine, entry))
    move = told.move
    if entry.action == J.OPERATION and entry.reused:
        move += " That chart was already open, so no new one was added."
    selected = (_Say(model, engine, entry).selection()
                if entry.action == J.OPERATION else "")
    return Beat(entry.seq, entry.action, move, told.question, told.answer,
                entry.uid, entry.context.id if entry.context else None,
                selected, entry.parent)


class Commentary:
    """Beats for a journal, memoised by sequence number.

    A beat never changes once written - its entry is frozen and the engine is
    deterministic - so each entry is phrased exactly once however often the
    panel redraws.
    """

    def __init__(self, model: SemanticModel, engine: AnalyticalEngine,
                 journal: Journal) -> None:
        self.model = model
        self.engine = engine
        self.journal = journal
        self._beats: dict[int, Beat | None] = {}
        self._answers: dict[str, int] = {}      # answer -> number first told

    def beats(self) -> list[Beat]:
        """The beats worth showing, numbered in order.

        The journal is append-only, so each beat is settled once, against the
        beats before it: that is what lets it drop repetition and stay
        deterministic.
        """
        out: list[Beat] = []
        prev: JournalEntry | None = None
        for entry in self.journal:
            if entry.seq not in self._beats:
                self._beats[entry.seq] = self._settle(entry, prev, out)
            beat = self._beats[entry.seq]
            if beat is not None:
                out.append(beat)
            prev = entry
        return out

    def _settle(self, entry: JournalEntry, prev: JournalEntry | None,
                told: list[Beat]) -> Beat | None:
        # Raising the chart a move just opened is how the UI moves on, not a
        # step in the investigation.
        if (entry.action == J.RETURN and prev is not None
                and prev.uid == entry.uid):
            return None
        beat = narrate_entry(self.model, self.engine, entry)
        beat = dataclasses.replace(beat, number=len(told) + 1)
        # Clicking the same member twice in a row does not need its standing
        # told twice.
        if beat.selected and told and told[-1].selected == beat.selected:
            beat = dataclasses.replace(beat, selected="")
        # The same reading reached by a different move is pointed back to, not
        # retold in full.
        if beat.question and beat.answer:
            core = _IMPLIED.sub("", beat.answer).strip()
            first = self._answers.get(core)
            if first is not None:
                lead = core.split(". ")[0].rstrip(".")
                beat = dataclasses.replace(
                    beat, answer=f"{lead}. Same reading as #{first}.")
            else:
                self._answers[core] = beat.number
        return beat

    # -- layout ------------------------------------------------------------

    def outline(self, layout: str = TREE) -> list[Line]:
        """The beats as display lines.

        TIMELINE is the journal in order. TREE follows the investigation map:
        the move that opened a chart heads it, and everything done from that
        chart - the views drilled out of it, and the pins, returns and
        explanations made on it - sits one level beneath, in the order it
        happened. Step numbers keep the chronology readable across branches.
        """
        beats = self.beats()
        if layout == TIMELINE:
            return [Line(0, b, True) for b in beats]
        heads: dict[str, Beat] = {}
        under: dict[str | None, list[tuple[str, Beat]]] = {None: []}
        for beat in beats:
            uid = beat.uid
            if uid and uid not in heads:
                heads[uid] = beat
                under.setdefault(uid, [])
                parent = beat.parent if beat.parent in heads else None
                under[parent].append(("node", beat))
            else:
                under[uid if uid in heads else None].append(("note", beat))
        out: list[Line] = []

        def walk(key: str | None, depth: int) -> None:
            for role, beat in under.get(key, []):
                out.append(Line(depth, beat, role == "node"))
                if role == "node":
                    walk(beat.uid, depth + 1)

        walk(None, 0)
        return out

    def markdown(self, *, live: set[str] | None = None, heading: str = "",
                 layout: str = TIMELINE) -> str:
        """The commentary as markdown. Links to charts that no longer exist
        (not in `live`) are flattened to bold text."""
        lines = [heading, ""] if heading else []
        for n, line in enumerate(self.outline(layout), 1):
            b = line.beat
            if layout == TIMELINE:
                item = f"{n}. "
            else:
                item = "  " * line.depth + f"- **#{b.number}** "
            # The answer is a second paragraph of the same list item, so it is
            # indented to the item's content column.
            col = len(item) if layout == TIMELINE else 2 * line.depth + 2
            text = _live(b, b.markdown(), live)
            lines.append(item + text.replace("\n\n", "\n\n" + " " * col))
            lines.append("")
        if not self.journal.entries:
            lines.append("_Nothing investigated yet._")
        return "\n".join(lines).rstrip() + "\n"

    def html(self, theme: Any, *, live: set[str] | None = None,
             layout: str = TREE, indent: int = 18) -> str:
        """The commentary for the in-app panel: indented like the map, with
        the newest beat marked so it can be found in a deep tree."""
        t = theme
        rows = self.outline(layout)
        if not rows:
            return (f"<p style='color:{t.muted}'><i>Nothing investigated "
                    f"yet.</i></p>")
        newest = max(line.beat.seq for line in rows)
        out = [f"<body style='color:{t.ink}'>"]
        for line in rows:
            b = line.beat
            pad = line.depth * indent
            if layout == TIMELINE:
                glyph = ""
            else:
                glyph = "└ " if line.head and line.depth else \
                    "· " if not line.head else ""
            bg = f"background-color:{t.plane};" if b.seq == newest else ""
            weight = "600" if line.head else "400"
            head = _html(_live(b, b.move, live), t)
            sel = (" <span style='font-weight:400'>"
                   f"{_html(_live(b, b.selected, live), t)}</span>"
                   if b.selected else "")
            out.append(
                f"<a name='s{b.seq}'></a>"
                f"<p style='margin-left:{pad}px; margin-top:"
                f"{10 if line.head else 4}px; margin-bottom:0; {bg}'>"
                f"<span style='color:{t.muted}'>{glyph}#{b.number}</span> "
                f"<span style='font-weight:{weight}'>{head}</span>{sel}</p>")
            if b.question or b.answer:
                q = f"<i>{_html(b.question, t)}</i> " if b.question else ""
                a = _html(_live(b, b.answer, live), t)
                out.append(
                    f"<p style='margin-left:{pad + indent}px; margin-top:2px;"
                    f" margin-bottom:0; color:{t.ink_2}; {bg}'>{q}{a}</p>")
        out.append("</body>")
        return "".join(out)


class Line(NamedTuple):
    depth: int
    beat: Beat
    head: bool          # the move that opened a chart, vs. a note on one


_IMPLIED = re.compile(r" _\(No comparison is set on the chart;[^)]*\)_")


def _live(beat: Beat, text: str, live: set[str] | None) -> str:
    """Drop the link to a chart that has since been closed."""
    if live is not None and beat.uid and beat.uid not in live:
        return _unlink(text.replace(f"](node:{beat.uid})", "]"))
    return text


def _html(md: str, theme: Any) -> str:
    """The small markdown subset beats are written in, as Qt rich text."""
    out = html.escape(md, quote=False)
    out = re.sub(r"\[([^\]]+)\]\((node:[^)]+)\)",
                 rf"<a href='\2' style='color:{theme.series[0]};"
                 rf" text-decoration:none'>\1</a>", out)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    return re.sub(r"(?<![\w])_(.+?)_(?![\w])", r"<i>\1</i>", out)


def _unlink(text: str) -> str:
    """`[label]` left behind by a dropped link becomes `**label**`."""
    return re.sub(r"\[([^\]]+)\](?!\()", r"**\1**", text)


def narrate_commentary(model: SemanticModel, engine: AnalyticalEngine,
                       journal: Journal, *, layout: str = TREE) -> str:
    """The whole running commentary, for reports and export. In-app chart
    links mean nothing outside the app, so they are flattened."""
    return Commentary(model, engine, journal).markdown(
        live=set(), heading="## Commentary", layout=layout)
