"""The "Explain this" engine.

Computes candidate explanations *deterministically* and ranks them. Every
piece of evidence carries the numbers behind it and a Context you can click
into - so an explanation is always a place you can go, never a claim you have
to trust.

Signals implemented (design plan section 5):
  contribution, change contribution, concentration, offsetting contributors,
  deviation from baseline, new / missing contributors, and a price-volume
  metric relationship where the semantic model allows it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from dataclasses import replace as dataclasses_replace
from typing import Any

from ..core import timegrain as tg
from ..core.context import Context, LineageStep, TimeWindow
from ..core.operations import CHANGE, TIMESERIES, Operation, apply
from ..semantic.specs import SemanticError, SemanticModel
from .engine import AnalyticalEngine, ResultHandle, comparison_label

# Relative weights per signal family - tuned so that "what moved" outranks
# "what is big", which is what users actually ask when something surprises them.
WEIGHT = {
    "change_contribution": 1.00,
    "offset": 0.92,
    "anomaly": 0.80,
    "new_member": 0.70,
    "missing_member": 0.70,
    "concentration": 0.55,
    "contribution": 0.50,
    "metric_relationship": 0.75,
}


@dataclass(frozen=True)
class Evidence:
    kind: str
    dimension: str | None
    member: str | None
    headline: str
    detail: str
    score: float
    magnitude: float
    direction: int                      # +1 up, -1 down, 0 neutral
    numbers: tuple[tuple[str, Any], ...] = ()
    target: Context | None = field(default=None, compare=False)
    target_kind: str = "breakdown"

    @property
    def number_map(self) -> dict[str, Any]:
        return dict(self.numbers)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "dimension": self.dimension,
                "member": self.member, "headline": self.headline,
                "detail": self.detail, "score": round(self.score, 4),
                "numbers": dict(self.numbers),
                "target_context_id": self.target.id if self.target else None}


@dataclass(frozen=True)
class ExplanationResult:
    context: Context
    metric_label: str
    total: float | None
    prior_total: float | None
    delta: float | None
    delta_pct: float | None
    comparison: str | None
    evidence: tuple[Evidence, ...]
    best_dimension: str | None
    dimensions_tested: tuple[str, ...]
    provenance: tuple[str, ...]

    def top(self, n: int = 8) -> tuple[Evidence, ...]:
        return self.evidence[:n]

    def to_dict(self) -> dict[str, Any]:
        return {"context_id": self.context.id, "total": self.total,
                "delta": self.delta, "best_dimension": self.best_dimension,
                "dimensions_tested": list(self.dimensions_tested),
                "evidence": [e.to_dict() for e in self.evidence]}


def explain(engine: AnalyticalEngine, model: SemanticModel, ctx: Context, *,
            candidate_dimensions: list[str] | None = None,
            max_dimensions: int = 4) -> ExplanationResult:
    """Rank deterministic evidence for the value described by `ctx`."""
    if ctx.comparison:
        base = ctx
    else:
        # Explaining a value means explaining its movement, so fall back to the
        # model's first declared comparison rather than a hardcoded period.
        default = next((c for c in model.comparisons
                        if model.is_valid(dataclasses_replace(ctx, comparison=c.name))[0]),
                       None)
        base = ctx if default is None else ctx.evolve(
            LineageStep("compare",
                        f"Compare with {default.label_for(ctx.time.grain)} "
                        f"(for explanation)", (("period", default.name),)),
            comparison=default.name)

    # Explain the *aggregate* described by the context, not the current split.
    scope_ctx = base if not base.grain else base.evolve(
        LineageStep("explain", "Explain scope total", ()), grain=())
    scope_res = engine.execute(scope_ctx)
    total = scope_res.total
    prior = scope_res.prior_total
    delta = scope_res.delta_total
    dpct = (delta / prior) if (delta is not None and prior) else None

    if candidate_dimensions:
        dims = list(candidate_dimensions)
    else:
        # The split already on screen is the most relevant explanation of all -
        # "why did the total move?" is usually answered by the bars in front of
        # you - so it leads, followed by the alternatives the model allows.
        dims = list(base.grain) + model.alternative_dimensions(base)
    # A dimension that can only yield one member inside this scope explains
    # nothing. Decomposing by it returns a single row whose delta *is* the
    # parent's, so every signal it produces restates the scope back at you -
    # "Luzon accounts for 100% of the decrease" - and its lopsidedness is a
    # perfect 1.0, which would always win "strongest breakdown". That happens
    # whenever a bar is explained: the member is pinned by a filter while the
    # chart's own grain is still on screen. `alternative_dimensions` already
    # rules both cases out; the grain in front of it has to clear the same bar.
    seen: set[str] = set()
    dims = [d for d in dims
            if d != model.time_column
            and d not in scope_ctx.filter_map
            and model.member_counts.get(d, 2) > 1
            and not (d in seen or seen.add(d))
            ][:max_dimensions]

    evidence: list[Evidence] = []
    provenance: list[str] = [scope_res.provenance]
    lopsidedness: dict[str, float] = {}

    for dim in dims:
        try:
            dim_ctx = apply(model, scope_ctx,
                            Operation.of("decompose", "explain-engine", dimension=dim))
            res = engine.execute(dim_ctx)
        except SemanticError:
            continue
        provenance.append(res.provenance)
        evidence.extend(_change_signals(engine, model, res, dim, delta))
        evidence.extend(_contribution_signals(model, res, dim))
        evidence.extend(_membership_signals(engine, model, res, dim))
        evidence.extend(_anomaly_signals(engine, model, dim_ctx, dim))
        lopsidedness[dim] = _lopsidedness(res)

    evidence.extend(_metric_relationship(engine, model, scope_ctx, delta))

    evidence.sort(key=lambda e: -e.score)
    best = max(lopsidedness, key=lambda d: lopsidedness[d]) if lopsidedness else None

    return ExplanationResult(
        context=base, metric_label=model.metric(base.metric).label,
        total=total, prior_total=prior, delta=delta, delta_pct=dpct,
        comparison=base.comparison, evidence=tuple(evidence),
        best_dimension=best, dimensions_tested=tuple(dims),
        provenance=tuple(provenance))


# --------------------------------------------------------------------------
# Signals
# --------------------------------------------------------------------------

def _change_signals(engine: AnalyticalEngine, model: SemanticModel,
                    res: ResultHandle, dim: str,
                    parent_delta: float | None) -> list[Evidence]:
    """Change contribution and offsetting contributors."""
    if not parent_delta:
        return []
    out: list[Evidence] = []
    parent_dir = 1 if parent_delta > 0 else -1
    label = model.label_of(dim)
    cmp_label = comparison_label(model, res.context) or "the prior period"

    movers = [r for r in res.rows if r.delta is not None]
    if not movers:
        return []
    gross = sum(abs(r.delta) for r in movers) or 1.0

    for r in sorted(movers, key=lambda r: -abs(r.delta or 0.0))[:6]:
        d = r.delta or 0.0
        if not d:
            continue
        share_of_change = d / parent_delta          # can exceed 1 or go negative
        share_of_gross = abs(d) / gross
        same_way = (1 if d > 0 else -1) == parent_dir
        target, target_kind = _delta_view(engine, model, res.context, dim, r.key)

        if same_way and share_of_change >= 0.12:
            out.append(Evidence(
                kind="change_contribution", dimension=dim, member=r.key,
                headline=f"{r.key} accounts for {share_of_change:.0%} of the "
                         f"{'increase' if parent_dir > 0 else 'decrease'}",
                detail=f"{label} {r.key} moved {_sig(d)} vs the {cmp_label}"
                       f"{_pct_tail(r.delta_pct)}.",
                score=WEIGHT["change_contribution"] * min(abs(share_of_change), 2.0) / 2.0
                      + 0.25 * share_of_gross,
                magnitude=abs(d), direction=1 if d > 0 else -1,
                numbers=(("delta", d), ("share_of_change", share_of_change),
                         ("value", r.value), ("prior", r.prior)),
                target=target, target_kind=target_kind))
        elif not same_way and abs(d) >= 0.15 * abs(parent_delta):
            out.append(Evidence(
                kind="offset", dimension=dim, member=r.key,
                headline=f"{r.key} moved the other way ({_sig(d)}), masking the "
                         f"{'rise' if parent_dir > 0 else 'fall'}",
                detail=f"Without {r.key}, the {'increase' if parent_dir > 0 else 'decrease'} "
                       f"would be {_sig(parent_delta - d)} instead of {_sig(parent_delta)}.",
                score=WEIGHT["offset"] * min(abs(d) / abs(parent_delta), 2.0) / 2.0 + 0.2,
                magnitude=abs(d), direction=1 if d > 0 else -1,
                numbers=(("delta", d), ("parent_delta", parent_delta),
                         ("net_without", parent_delta - d)),
                target=target, target_kind=target_kind))
    return out


def _contribution_signals(model: SemanticModel, res: ResultHandle,
                          dim: str) -> list[Evidence]:
    """Absolute contribution and concentration."""
    if res.metric.is_ratio:
        return []
    vals = [(r.key, r.value or 0.0) for r in res.rows if r.value is not None]
    if not vals or not res.total:
        return []
    vals.sort(key=lambda kv: -kv[1])
    out: list[Evidence] = []
    label = model.label_of(dim)

    top_key, top_val = vals[0]
    top_share = top_val / res.total
    out.append(Evidence(
        kind="contribution", dimension=dim, member=top_key,
        headline=f"{top_key} is the largest {label.lower()} at {top_share:.0%} of the total",
        detail=f"{_num(top_val)} of {_num(res.total)}.",
        score=WEIGHT["contribution"] * top_share,
        magnitude=top_val, direction=0,
        numbers=(("value", top_val), ("share", top_share), ("total", res.total)),
        target=r_context(model, res.context, dim, top_key)))

    # Concentration: how few members make up 80% of the value.
    cum, n80 = 0.0, 0
    for _, v in vals:
        cum += v
        n80 += 1
        if cum >= 0.8 * res.total:
            break
    if len(vals) >= 4 and n80 <= max(2, len(vals) // 4):
        hhi = sum((v / res.total) ** 2 for _, v in vals)
        out.append(Evidence(
            kind="concentration", dimension=dim, member=None,
            headline=f"Highly concentrated: {n80} of {len(vals)} "
                     f"{label.lower()} members make up 80% of the value",
            detail=f"Herfindahl index {hhi:.2f} - the total is driven by a few members, "
                   f"so member-level moves dominate the aggregate.",
            score=WEIGHT["concentration"] * (1 - n80 / len(vals)),
            magnitude=hhi, direction=0,
            numbers=(("members_to_80pct", n80), ("members", len(vals)), ("hhi", hhi)),
            target=res.context))
    return out


def _membership_signals(engine: AnalyticalEngine, model: SemanticModel,
                        res: ResultHandle, dim: str) -> list[Evidence]:
    """New and missing contributors - structural change in the population."""
    out: list[Evidence] = []
    label = model.label_of(dim)
    scale = abs(res.total or 0.0) or 1.0
    for r in res.rows:
        extra = dict(r.extra)
        if extra.get("missing") and r.prior:
            out.append(Evidence(
                kind="missing_member", dimension=dim, member=r.key,
                headline=f"{r.key} disappeared from this population",
                detail=f"It contributed {_num(r.prior)} in the prior period and "
                       f"nothing now.",
                score=WEIGHT["missing_member"] * min(abs(r.prior) / scale * 4, 1.0),
                magnitude=abs(r.prior), direction=-1,
                numbers=(("prior", r.prior),),
                target=member_trend(engine, model, res.context, dim, r.key),
                target_kind=TIMESERIES))
        elif r.value and not r.prior and res.context.comparison:
            out.append(Evidence(
                kind="new_member", dimension=dim, member=r.key,
                headline=f"{r.key} is new in this population",
                detail=f"It contributes {_num(r.value)} with no prior-period base.",
                score=WEIGHT["new_member"] * min(abs(r.value) / scale * 4, 1.0),
                magnitude=abs(r.value), direction=1,
                numbers=(("value", r.value),),
                target=member_trend(engine, model, res.context, dim, r.key),
                target_kind=TIMESERIES))
    return out


def _anomaly_signals(engine: AnalyticalEngine, model: SemanticModel,
                     dim_ctx: Context, dim: str) -> list[Evidence]:
    """Deviation from each member's own trailing baseline."""
    from ..core.operations import EXCEPTIONS
    res = engine.execute(dim_ctx, EXCEPTIONS)
    out: list[Evidence] = []
    label = model.label_of(dim)
    for r in res.rows[:3]:
        z = dict(r.extra).get("z", 0.0)
        if abs(z) < 2.0:
            continue
        base = dict(r.extra).get("baseline")
        out.append(Evidence(
            kind="anomaly", dimension=dim, member=r.key,
            headline=f"{r.key} is {abs(z):.1f} sd {'above' if z > 0 else 'below'} "
                     f"its own 12-month baseline",
            detail=f"{_num(r.value)} against a baseline of {_num(base)}.",
            score=WEIGHT["anomaly"] * min(abs(z) / 4.0, 1.0),
            magnitude=abs(z), direction=1 if z > 0 else -1,
            numbers=(("z", z), ("value", r.value), ("baseline", base)),
            target=member_trend(engine, model, dim_ctx, dim, r.key),
            target_kind=TIMESERIES))
    return out


def _metric_relationship(engine: AnalyticalEngine, model: SemanticModel,
                         scope_ctx: Context, parent_delta: float | None) -> list[Evidence]:
    """Split a movement into a volume effect and a rate effect.

    Only offered when the semantic model *declares* that this metric is
    volume x rate - the engine never guesses that two columns are related.
    """
    if not parent_delta:
        return []
    rel = model.relationship_for(scope_ctx.metric, "volume_rate")
    if rel is None or not rel.volume or not rel.rate:
        return []
    import dataclasses

    vol = engine.execute(dataclasses.replace(scope_ctx, metric=rel.volume))
    base = engine.execute(scope_ctx)
    if None in (vol.total, vol.prior_total, base.total, base.prior_total):
        return []
    if not vol.total or not vol.prior_total or not base.prior_total:
        return []

    rate_now = base.total / vol.total
    rate_prev = base.prior_total / vol.prior_total
    d_vol = vol.total - vol.prior_total
    d_rate = rate_now - rate_prev
    # Standard decomposition: dV_metric = dVolume x rate_prev + volume_now x dRate
    volume_effect = d_vol * rate_prev
    rate_effect = vol.total * d_rate
    volume_led = abs(volume_effect) >= abs(rate_effect)
    dominant = rel.volume_noun if volume_led else rel.rate_noun
    vol_metric = model.metric(rel.volume)
    # Show the side of the split the card credits the move to, over time -
    # a single bar of either one cannot show an effect.
    driver = trend_context(engine, model, dataclasses_replace(
        scope_ctx, metric=rel.volume if volume_led else rel.rate))
    return [Evidence(
        kind="metric_relationship", dimension=None, member=None,
        headline=f"The move is mostly a {dominant} effect",
        detail=(f"{rel.volume_noun.capitalize()} effect {_sig(volume_effect)} "
                f"({d_vol:+,.0f} {vol_metric.unit or rel.volume_noun} x prior "
                f"{rel.rate_noun}), {rel.rate_noun} effect {_sig(rate_effect)} "
                f"({d_rate:+,.2f} per unit x current {rel.volume_noun})."),
        score=WEIGHT["metric_relationship"] *
              min(max(abs(volume_effect), abs(rate_effect)) / abs(parent_delta), 1.0),
        magnitude=max(abs(volume_effect), abs(rate_effect)),
        direction=1 if parent_delta > 0 else -1,
        numbers=(("volume_effect", volume_effect), ("rate_effect", rate_effect),
                 ("d_volume", d_vol), ("d_rate", d_rate)),
        target=driver, target_kind=TIMESERIES)]


def _lopsidedness(res: ResultHandle) -> float:
    """How much of the gross movement sits in a single member. The dimension
    that best *explains* a change is the one where the change is least evenly
    spread."""
    deltas = [abs(r.delta) for r in res.rows if r.delta is not None]
    if not deltas:
        return 0.0
    gross = sum(deltas)
    return (max(deltas) / gross) if gross else 0.0


# --------------------------------------------------------------------------

def r_context(model: SemanticModel, ctx: Context, dim: str,
              member: str) -> Context:
    """The node you land on when you click a piece of evidence.

    Landing on "revenue by product_category, filtered to Payments" would be a
    single bar - useless. So scope to the member and step the grain down to
    whatever sits below it.
    """
    import dataclasses
    scoped = ctx.with_filter(dim, member,
                             LineageStep("focus", f"Focus on {member}",
                                         (("dimension", dim), ("member", member)),
                                         "explain-evidence"))
    child = model.child_dimension(dim)
    if child:
        return dataclasses.replace(scoped, grain=(child,))
    alts = model.alternative_dimensions(scoped)
    return dataclasses.replace(scoped, grain=(alts[0],) if alts else ())


def _delta_view(engine: AnalyticalEngine, model: SemanticModel, ctx: Context,
                dim: str, member: str) -> tuple[Context, str]:
    """Where a change driver or an offset lands: the same movement, one level
    down.

    Both cards claim a *movement* - "Payments accounts for 62% of the
    decrease", "Lending moved the other way, masking the fall". A levels
    breakdown answers a different question and leaves the delta in the
    tooltips, so the landing node is read as change: deltas around a zero
    line, largest mover first. A member with nothing left to break down by
    has no change view to offer, so it falls back to its own trend.
    """
    target = r_context(model, ctx, dim, member)
    if target.grain and target.grain[0] != model.time_column:
        return target, CHANGE
    return member_trend(engine, model, ctx, dim, member), TIMESERIES


def trend_context(engine: AnalyticalEngine, model: SemanticModel,
                  ctx: Context, *, periods: int = 12) -> Context:
    """The same scope, read as a trend: the time axis exposed and the window
    wound back far enough to show where the current period came from.

    Every card whose claim is about *movement over time* - an anomaly against
    its baseline, a member arriving or disappearing, a rate effect - lands
    here, because a single-period chart cannot show movement at all.
    """
    import dataclasses
    grain = ctx.time.grain
    # Period keys sort as text at every grain, so clamping the wound-back
    # start to the first period with data is a plain max().
    first = tg.key_of(engine.source.timestamps.min(), grain)
    start = max(tg.add(ctx.time.start, grain, -periods), first)
    return dataclasses.replace(ctx, grain=(model.time_column,),
                               time=TimeWindow(start, ctx.time.end, grain))


def member_trend(engine: AnalyticalEngine, model: SemanticModel, ctx: Context,
                 dim: str, member: str, *, periods: int = 12) -> Context:
    """One member's own history - what an anomaly or a membership change is
    actually a claim about.

    Landing on a breakdown *of* that member hides the claim: for an anomaly
    the deviation is invisible without the baseline behind it, and for a
    member that has disappeared the current window is empty by definition.
    """
    scoped = ctx.with_filter(dim, member,
                             LineageStep("focus", f"Focus on {member}",
                                         (("dimension", dim), ("member", member)),
                                         "explain-evidence"))
    return trend_context(engine, model, scoped, periods=periods)


def _num(v: float | None) -> str:
    if v is None:
        return "-"
    a = abs(v)
    if a >= 1e9:
        return f"{v/1e9:,.2f}B"
    if a >= 1e6:
        return f"{v/1e6:,.2f}M"
    if a >= 1e3:
        return f"{v/1e3:,.1f}K"
    return f"{v:,.0f}"


def _sig(v: float | None) -> str:
    if v is None:
        return "-"
    return ("+" if v > 0 else "-") + _num(abs(v))


def _pct_tail(p: float | None) -> str:
    return f" ({p:+.1%})" if p is not None else ""
