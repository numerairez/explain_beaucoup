"""Tests for the analytical framework.

These cover the guarantees the design plan actually makes: deterministic
numbers, semantic safety, immutable contexts and a preserved investigation
graph. The UI is deliberately not needed for any of them - that separation is
the whole point of the architecture.
"""

from __future__ import annotations

import dataclasses

import pandas as pd
import pytest

from explain_beaucoup.core.context import (Context, LineageStep, TimeWindow,
                                         month_add, month_diff)
from explain_beaucoup.core.graph import InvestigationGraph
from explain_beaucoup.core.operations import (BREAKDOWN, CHANGE, DISTRIBUTION,
                                            EXCEPTIONS, TIMESERIES, Operation,
                                            apply, result_kind)
from explain_beaucoup.data.generate import load_frame
from explain_beaucoup.engine.engine import AnalyticalEngine, _delta
from explain_beaucoup.engine.explain import explain
from explain_beaucoup.engine.narrate import (narrate_explanation,
                                           narrate_investigation,
                                           suggested_questions)
from explain_beaucoup.semantic.sales_model import sales_model
from explain_beaucoup.semantic.specs import SemanticError
from explain_beaucoup.view.theme import DARK, LIGHT
from explain_beaucoup.view.vega import (COMPACT_ROWS, COMPACT_STEP,
                                        spec_for, titles_for)

AUG = "2026-08"
JUL = "2026-07"


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    return load_frame()


@pytest.fixture(scope="module")
def model(frame):
    m = sales_model()
    m.profile(frame)
    return m


@pytest.fixture(scope="module")
def engine(frame, model):
    return AnalyticalEngine(frame, model)


def ctx(metric="revenue", grain=("region",), filters=None, comparison=None,
        time=None):
    return Context.new("sales", metric, time or TimeWindow.single(AUG),
                       filters=filters or {}, grain=grain, comparison=comparison)


# -- time -------------------------------------------------------------------

def test_month_arithmetic_wraps_years():
    assert month_add("2026-11", 3) == "2027-02"
    assert month_add("2026-01", -1) == "2025-12"
    assert month_diff("2026-08", "2025-08") == 12
    assert TimeWindow("2026-01", "2026-03").months == ["2026-01", "2026-02", "2026-03"]
    assert TimeWindow.single(AUG).trailing(12).start == "2025-09"


# -- context ----------------------------------------------------------------

def test_context_is_immutable_and_operations_return_new_nodes(model):
    c1 = ctx()
    c2 = c1.with_filter("region", "Luzon", LineageStep("focus", "Focus"))
    assert c1.filters == ()                    # unchanged
    assert c2.filter_map == {"region": "Luzon"}
    assert c1.id != c2.id


def test_fingerprint_ignores_lineage_so_the_cache_can_be_shared(model):
    """Two contexts that mean the same thing must hash the same even if they
    were reached by different paths."""
    base = ctx(filters={"region": "Luzon"}, grain=("province",))
    viaA = base.evolve(LineageStep("drill_down", "one way"))
    viaB = base.evolve(LineageStep("decompose", "another way"))
    assert viaA.lineage != viaB.lineage
    assert viaA.fingerprint == viaB.fingerprint == base.fingerprint


def test_lineage_accumulates(model):
    c = ctx()
    c = apply(model, c, Operation.of("drill_down", dimension="province",
                                     member="Luzon"))
    c = apply(model, c, Operation.of("compare", period="mom"))
    assert [s.verb for s in c.lineage] == ["drill_down", "compare"]
    assert c.comparison == "mom"


# -- semantic safety --------------------------------------------------------

def test_structural_drill_must_follow_the_hierarchy(model):
    with pytest.raises(SemanticError, match="structural child"):
        apply(model, ctx(), Operation.of("drill_down", dimension="city"))


def test_change_contribution_needs_a_dimensional_grain(model):
    with pytest.raises(SemanticError, match="dimensional grain"):
        apply(model, ctx(grain=()), Operation.of("change_contribution"))


def test_change_within_a_member_breaks_it_down_one_level(engine, model):
    """"Break down Luzon's change" ranks Luzon's parts, not Luzon alone."""
    c = apply(model, ctx(), Operation.of("change_contribution", member="Luzon"))
    assert c.filter_map["region"] == "Luzon"
    assert c.grain == ("province",)                       # structural child
    res = engine.execute(c, CHANGE)
    assert len([r for r in res.rows if r.delta is not None]) > 1


def test_change_within_a_leaf_member_is_blocked(frame, model):
    """No level below it in a hierarchy: nothing to break the change down by."""
    member = str(sorted(frame[model.dim("channel").column].unique())[0])
    with pytest.raises(SemanticError, match="no level below"):
        apply(model, ctx(grain=("channel",)),
              Operation.of("change_contribution", member=member))


def test_exceptions_within_a_member_break_it_down(model):
    c = apply(model, ctx(), Operation.of("exceptions", member="Luzon"))
    assert c.grain == ("province",)


def test_exceptions_within_a_leaf_member_are_blocked(frame, model):
    member = str(sorted(frame[model.dim("channel").column].unique())[0])
    with pytest.raises(SemanticError, match="no level below"):
        apply(model, ctx(grain=("channel",)),
              Operation.of("exceptions", member=member))


def test_new_and_gone_members_count_toward_an_additive_change():
    """A member present on one side only moved from (or to) zero, so the
    children still sum to the parent; a ratio has nothing to move from."""
    assert _delta(None, 50.0, True) == -50.0              # gone
    assert _delta(20.0, None, True) == 20.0               # new
    assert _delta(20.0, None, False) is None              # ratio / no reference


def test_compact_change_panel_keeps_the_biggest_movers(engine, model):
    res = engine.execute(ctx(grain=("product",), comparison="yoy",
                             metric="volume"), CHANGE)
    movers = sorted((r for r in res.rows if r.delta), key=lambda r: -abs(r.delta))
    kept = {d["key"] for d in
            spec_for(model, res, LIGHT, compact=True)["data"]["values"]}
    assert kept == {r.key for r in movers[:COMPACT_ROWS]}


def test_change_tooltip_fields_exist_in_the_data(engine, model):
    res = engine.execute(ctx(comparison="mom"), CHANGE)
    spec = spec_for(model, res, LIGHT)
    fields = set(spec["data"]["values"][0])
    for layer in spec["layer"]:
        for tip in layer.get("encoding", {}).get("tooltip", []):
            assert tip["field"] in fields


def test_a_comparison_that_overlaps_its_own_window_is_blocked(model):
    """The general rule that replaces "YoY needs <= 12 months": a reference
    window must not overlap the window being analysed."""
    long_window = ctx(time=TimeWindow("2025-01", "2026-08"))
    with pytest.raises(SemanticError, match="overlaps"):
        apply(model, long_window, Operation.of("compare", period="yoy"))
    # ...but on a time series the comparison is per point, so it is allowed.
    trend = ctx(grain=("month",), time=TimeWindow("2025-01", "2026-08"))
    model.validate(dataclasses.replace(trend, comparison="yoy"))


def test_selecting_a_point_on_a_time_series_scopes_to_that_period(engine, model):
    """Clicking one point and breaking it down must narrow the window to that
    point. A period is not a filter - it re-keys `ctx.time` - so dropping it
    would break the whole plotted window down by the new dimension."""
    trend = apply(model, ctx(), Operation.of("trend", periods=12))
    assert trend.time.length == 12

    out = apply(model, trend,
                Operation.of("decompose", dimension="channel", member=AUG))
    assert out.time == TimeWindow.single(AUG)
    assert out.grain == ("channel",)
    assert model.time_column not in out.filter_map   # time is never a filter

    only_aug = apply(model, ctx(), Operation.of("decompose", dimension="channel"))
    assert engine.execute(out).total == engine.execute(only_aug).total

    # With no point selected the window is untouched.
    whole = apply(model, trend, Operation.of("decompose", dimension="channel"))
    assert whole.time == trend.time


def test_unknown_metric_and_dimension_are_rejected(model):
    with pytest.raises(SemanticError):
        model.validate(ctx(metric="ebitda"))
    with pytest.raises(SemanticError):
        model.validate(ctx(grain=("colour",)))


def test_degenerate_levels_are_not_advertised_but_do_not_hide_the_hierarchy(model):
    """country has one member: never offered, and never blocks region."""
    alts = model.alternative_dimensions(
        ctx(grain=("channel",), filters={"channel": "Digital"}))
    assert "country" not in alts
    assert "region" in alts


def test_capabilities_never_offer_a_duplicate_of_the_structural_drill(model):
    caps = model.capabilities(ctx())
    drills = [c.param_map["dimension"] for c in caps if c.verb == "drill_down"]
    decomps = [c.param_map["dimension"] for c in caps if c.verb == "decompose"]
    assert drills == ["province"]
    assert "province" not in decomps


# -- engine determinism -----------------------------------------------------

def test_breakdown_matches_the_raw_data(engine, frame, model):
    res = engine.execute(ctx())
    truth = frame[frame.month == AUG].groupby("region")["revenue"].sum()
    assert res.total == pytest.approx(float(truth.sum()))
    for row in res.rows:
        assert row.value == pytest.approx(float(truth[row.key]))
        assert row.share == pytest.approx(float(truth[row.key]) / float(truth.sum()))


def test_ratio_metrics_are_recomputed_from_components_never_summed(engine, frame):
    """The plan's margin_pct rule: aggregate the components, then divide."""
    res = engine.execute(ctx(metric="margin_pct", grain=("product_category",)))
    aug = frame[frame.month == AUG]
    for row in res.rows:
        sub = aug[aug.product_category == row.key]
        expected = float(sub["margin"].sum()) / float(sub["revenue"].sum())
        assert row.value == pytest.approx(expected)
    # And the total is NOT the mean of the parts.
    naive = sum(r.value for r in res.rows) / len(res.rows)
    correct = float(aug["margin"].sum()) / float(aug["revenue"].sum())
    assert res.total == pytest.approx(correct)
    assert res.total != pytest.approx(naive)
    # Shares are meaningless for a ratio and must not be invented.
    assert all(r.share is None for r in res.rows)


def test_comparison_pulls_the_right_prior_window(engine, frame):
    res = engine.execute(ctx(comparison="mom"))
    jul = frame[frame.month == JUL].groupby("region")["revenue"].sum()
    for row in res.rows:
        assert row.prior == pytest.approx(float(jul[row.key]))
        assert row.delta == pytest.approx(row.value - row.prior)


def test_timeseries_covers_every_month_in_the_window(engine):
    window = TimeWindow("2025-09", "2026-08")
    res = engine.execute(ctx(grain=("month",), time=window), TIMESERIES)
    assert [r.key for r in res.rows] == window.months
    assert res.kind == TIMESERIES


def test_results_are_cached_by_context_fingerprint(engine):
    e = AnalyticalEngine(engine.df, engine.model)
    c = ctx()
    first = e.execute(c)
    assert e.cache_size() == 1
    again = e.execute(c.evolve(LineageStep("noop", "different path")))
    assert e.cache_size() == 1          # same analytical state -> same entry
    assert again is first


def test_provenance_is_recorded(engine):
    res = engine.execute(ctx(filters={"region": "Luzon"}, grain=("province",)))
    assert "GROUP BY province" in res.provenance
    assert "region='Luzon'" in res.provenance
    assert res.fact_rows > 0


def test_exceptions_score_against_each_members_own_baseline(engine):
    res = engine.execute(ctx(grain=("province",)), EXCEPTIONS)
    zs = {r.key: dict(r.extra)["z"] for r in res.rows}
    assert zs                                  # something was scored
    # Cebu was generated to run hot from Jul 2026.
    assert zs["Cebu"] > 2.0
    assert abs(zs["Cebu"]) == max(abs(v) for v in zs.values())


# -- explain ----------------------------------------------------------------

def test_explain_finds_the_planted_story(engine, model):
    exp = explain(engine, model, ctx(comparison="mom"))
    assert exp.delta < 0                                    # revenue fell
    kinds = {e.kind for e in exp.top(6)}
    assert "change_contribution" in kinds
    assert "offset" in kinds

    drivers = [e for e in exp.evidence if e.kind == "change_contribution"]
    assert drivers[0].member in {"Payments", "Digital"}     # the real cause
    offsets = [e for e in exp.evidence if e.kind == "offset"]
    assert offsets[0].member == "Lending"                   # the masking mover
    assert offsets[0].number_map["delta"] > 0


def test_explanations_outrank_size_with_movement(engine, model):
    exp = explain(engine, model, ctx(comparison="mom"))
    top_kind = exp.top(1)[0].kind
    assert top_kind in {"change_contribution", "offset"}


def test_every_piece_of_evidence_is_a_place_you_can_go(engine, model):
    exp = explain(engine, model, ctx(comparison="mom"))
    for ev in exp.top(8):
        if ev.target is None:
            continue
        model.validate(ev.target)                   # must be a legal node
        assert engine.execute(ev.target).rows       # and must resolve to data
        if ev.member and ev.dimension:
            # Landing must drill *into* the member, not filter to a single bar.
            assert ev.target.filter_map.get(ev.dimension) == ev.member
            assert ev.target.grain != (ev.dimension,)


def test_explain_without_an_explicit_comparison_defaults_to_mom(engine, model):
    exp = explain(engine, model, ctx())
    assert exp.comparison == "mom"
    assert exp.delta is not None


# -- graph ------------------------------------------------------------------

def test_branching_preserves_both_hypotheses(model):
    g = InvestigationGraph()
    c1 = ctx()
    root = g.add(c1, title="root")
    c2 = apply(model, c1, Operation.of("drill_down", dimension="province",
                                       member="Luzon"))
    n2 = g.add(c2, parent=root.uid)
    a = g.add(apply(model, c2, Operation.of("decompose", dimension="product")),
              parent=n2.uid)
    b = g.branch_from(n2.uid,
                      apply(model, c2, Operation.of("decompose", dimension="channel")))
    assert a.branch != b.branch
    assert {n.uid for n in g.siblings(a.uid)} == {b.uid}
    assert [n.uid for n in g.path_to(b.uid)] == [root.uid, n2.uid, b.uid]
    # Nothing was destroyed by the branch.
    assert g.get(a.uid).context.grain == ("product",)


def test_closing_a_node_takes_its_subtree_with_it(model):
    """Closing a chart in the stack must not strand the views it spawned."""
    g = InvestigationGraph()
    c1 = ctx()
    root = g.add(c1, title="root")
    c2 = apply(model, c1, Operation.of("drill_down", dimension="province",
                                       member="Luzon"))
    n2 = g.add(c2, parent=root.uid)
    n3 = g.add(apply(model, c2, Operation.of("decompose", dimension="product")),
               parent=n2.uid)
    keep = g.add(ctx(grain=("product",)), parent=root.uid)
    g.goto(n3.uid)

    gone = g.remove(n2.uid)

    assert set(gone) == {n2.uid, n3.uid}
    assert n2.uid not in g.nodes and n3.uid not in g.nodes
    assert [n.uid for n in g.child_nodes(root.uid)] == [keep.uid]
    # The focus falls back to the closed node's parent, never to a dead uid.
    assert g.current == root.uid


def test_child_nodes_are_the_charts_below(model):
    g = InvestigationGraph()
    root = g.add(ctx(), title="root")
    first = g.add(ctx(grain=("product",)), parent=root.uid)
    second = g.add(ctx(grain=("channel",)), parent=root.uid)
    assert [n.uid for n in g.child_nodes(root.uid)] == [first.uid, second.uid]
    assert g.child_nodes(first.uid) == []


def test_pins_survive_navigation(model):
    g = InvestigationGraph()
    root = g.add(ctx(), title="root")
    g.pin(root.uid, "keep me")
    g.add(ctx(grain=("product",)), parent=root.uid)
    assert [n.uid for n in g.pins] == [root.uid]
    assert g.get(root.uid).note == "keep me"


# -- views ------------------------------------------------------------------

@pytest.mark.parametrize("kind,context", [
    (BREAKDOWN, ctx()),
    (BREAKDOWN, ctx(comparison="mom")),
    (CHANGE, ctx(grain=("product",), comparison="mom")),
    (TIMESERIES, ctx(grain=("month",), time=TimeWindow("2025-09", "2026-08"))),
    (EXCEPTIONS, ctx(grain=("province",))),
    (DISTRIBUTION, ctx(grain=("product",))),
])
@pytest.mark.parametrize("theme", [LIGHT, DARK])
def test_specs_build_for_every_kind_and_theme(engine, model, kind, context, theme):
    spec = spec_for(model, engine.execute(context, kind), theme)
    assert spec["$schema"].endswith("vega-lite/v6.json")
    assert spec["config"]["background"] == theme.surface
    assert spec["title"]["text"]
    assert "layer" in spec


def test_every_point_on_a_time_series_can_be_selected(engine, model):
    """A mark is only selectable if a right-click resolves a datum carrying its
    key. On a time series the line is not interactive, the points are size 0
    until hovered, and the crosshair is transparent - so without an explicit
    hit target the only selectable mark is whichever decoration happens to be
    painted (the partial-period ring on the last point), which is why a
    breakdown used to scope to the whole window for every other period."""
    res = engine.execute(ctx(grain=("month",),
                             time=TimeWindow("2025-09", "2026-08")), TIMESERIES)
    spec = spec_for(model, res, LIGHT)
    n = len([r for r in res.rows if r.value is not None])

    bands = [l for l in spec["layer"]
             if l.get("mark", {}).get("type") == "rule"
             and l["mark"].get("opacity") == 0]
    assert len(bands) == 1, "expected exactly one transparent hit layer"
    band = bands[0]

    # Full height: x only, so the rule spans the plot vertically.
    assert set(band["encoding"]) == {"x", "tooltip"}
    # Sized off Vega's own width signal - the chart is laid out width=container,
    # and n-1 because N points leave N-1 gaps between them.
    assert band["mark"]["strokeWidth"] == {"expr": f"max(6, width / {n - 1})"}
    # Hover hangs off the hit layer, so the crosshair follows the same geometry
    # the selection uses - and nothing re-introduces a `nearest` voronoi, whose
    # cells carry no key and would sit on top of these bands.
    assert [p["name"] for p in band["params"]] == ["hover"]
    assert "nearest" not in band["params"][0]["select"]
    for layer in spec["layer"]:
        for prm in layer.get("params", []):
            assert not prm["select"].get("nearest"), "a voronoi would mask the hit bands"


def test_a_two_series_chart_carries_a_legend(engine, model):
    spec = spec_for(model, engine.execute(ctx(comparison="mom")), LIGHT)
    colour = spec["layer"][0]["encoding"]["color"]
    assert colour["legend"]["orient"] == "top"
    assert colour["scale"]["range"] == [LIGHT.series[0], LIGHT.series[1]]


def test_a_single_series_chart_has_no_legend(engine, model):
    spec = spec_for(model, engine.execute(ctx()), LIGHT)
    assert "color" not in spec["layer"][0]["encoding"]


def test_no_chart_uses_a_second_value_axis(engine, model):
    """One axis, always - the cardinal charting rule."""
    for kind, c in [(BREAKDOWN, ctx()), (CHANGE, ctx(grain=("product",), comparison="mom")),
                    (TIMESERIES, ctx(grain=("month",), time=TimeWindow("2025-09", "2026-08")))]:
        spec = spec_for(model, engine.execute(c, kind), LIGHT)
        titles = {l["encoding"]["x"]["axis"].get("title")
                  for l in spec["layer"]
                  if "encoding" in l and "x" in l["encoding"]
                  and isinstance(l["encoding"]["x"], dict)
                  and "axis" in l["encoding"]["x"]}
        assert len(titles) == 1


def test_diverging_charts_use_the_two_poles_only(engine, model):
    spec = spec_for(model, engine.execute(ctx(grain=("product",), comparison="mom"),
                                          CHANGE), LIGHT)
    bars = [l for l in spec["layer"] if l.get("mark", {}).get("type") == "bar"][0]
    colour = bars["encoding"]["color"]
    assert colour["condition"]["value"] == LIGHT.pos
    assert colour["value"] == LIGHT.neg


# -- narration --------------------------------------------------------------

def test_a_compact_spec_fits_a_child_panel(engine, model):
    """Charts below the focus are glances: fewer categories, no axis titles,
    no in-plot title - the panel header carries it."""
    res = engine.execute(ctx(grain=("city",)), BREAKDOWN)
    full = spec_for(model, res, LIGHT)
    compact = spec_for(model, res, LIGHT, compact=True)

    assert _row_counts(full)[0] > COMPACT_ROWS
    assert _row_counts(compact)[0] == COMPACT_ROWS
    assert "title" in full and "title" not in compact
    assert all(step <= COMPACT_STEP for step in _band_steps(compact))
    assert all(step > COMPACT_STEP for step in _band_steps(full))
    assert _axis_titles(compact) == set()
    assert _axis_titles(full)


def test_a_compact_time_series_keeps_every_point(engine, model):
    """Dropping periods would misread the shape, so only the height shrinks."""
    context = ctx(grain=("month",), time=TimeWindow("2025-09", "2026-08"))
    res = engine.execute(context, TIMESERIES)
    full = spec_for(model, res, LIGHT)
    compact = spec_for(model, res, LIGHT, compact=True)
    assert _row_counts(compact) == _row_counts(full)
    assert compact["height"] < full["height"]


def test_titles_for_matches_the_chart_headline(engine, model):
    res = engine.execute(ctx(), BREAKDOWN)
    head, sub = titles_for(model, res)
    spec = spec_for(model, res, LIGHT)
    assert spec["title"]["text"] == head
    assert spec["title"]["subtitle"] == sub


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _row_counts(spec) -> list[int]:
    return [len(n["data"]["values"]) for n in _walk(spec)
            if isinstance(n.get("data"), dict)
            and isinstance(n["data"].get("values"), list)]


def _band_steps(spec) -> list[float]:
    return [n["height"]["step"] for n in _walk(spec)
            if isinstance(n.get("height"), dict) and "step" in n["height"]]


def _axis_titles(spec) -> set:
    return {n["axis"]["title"] for n in _walk(spec)
            if isinstance(n.get("axis"), dict)
            and n["axis"].get("title") is not None}


def test_narrative_cites_the_context_and_the_numbers(engine, model):
    exp = explain(engine, model, ctx(comparison="mom"))
    text = narrate_explanation(model, exp)
    assert exp.context.id in text
    assert "governed queries" in text
    assert "%" in text


def test_investigation_report_lists_nodes_pins_and_branches(engine, model):
    g = InvestigationGraph()
    root = g.add(ctx(), title="Revenue by Region")
    c2 = apply(model, root.context,
               Operation.of("drill_down", dimension="province", member="Luzon"))
    n2 = g.add(c2, op=Operation.of("drill_down", dimension="province"),
               parent=root.uid)
    g.branch_from(n2.uid, apply(model, c2, Operation.of("decompose",
                                                       dimension="channel")))
    g.pin(n2.uid, "Luzon is the mover")
    text = narrate_investigation(model, engine, g, n2)
    assert "Pinned findings" in text
    assert "Competing hypotheses" in text
    assert "Luzon is the mover" in text


def test_suggested_questions_map_to_runnable_operations(model):
    g = InvestigationGraph()
    node = g.add(ctx())
    for _text, verb, params in suggested_questions(model, node):
        if verb == "explain":
            continue
        apply(model, node.context, Operation.of(verb, **params))


def test_result_kind_follows_the_operation(model):
    c = ctx()
    assert result_kind(model, c, None) == BREAKDOWN
    assert result_kind(model, c, Operation.of("exceptions")) == EXCEPTIONS
    assert result_kind(model, ctx(grain=("month",)), None) == TIMESERIES


def test_change_capability_names_the_dimension_it_breaks_down_by(model):
    labels = [c.label for c in model.capabilities(ctx(comparison="mom"))
              if c.verb == "change_contribution"]
    assert labels == ["Break down change by Region"]
