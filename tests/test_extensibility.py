"""The framework's extension points, and the two-axis grain.

Every layer that used to be a closed `if/elif` is now a registry - verbs,
result kinds, explain signals, views - and the data store is a `Backend`.
These tests pin down that each one can be extended from outside, that the
backends agree, and that a matrix behaves like any other node.
"""

from __future__ import annotations

import dataclasses

import pandas as pd
import pytest

from explain_beaucoup.core.context import (Context, TimeWindow, cell_key,
                                           split_cell)
from explain_beaucoup.core.operations import (BREAKDOWN, CHANGE, DISTRIBUTION,
                                              EXCEPTIONS, MATRIX, TIMESERIES,
                                              VERBS, Operation, Verb, apply,
                                              register_verb)
from explain_beaucoup.core.session import Session
from explain_beaucoup.data.generate import load_frame
from explain_beaucoup.data.source import DataSource
from explain_beaucoup.engine.backend import PandasBackend
from explain_beaucoup.engine.commentary import PHRASES, Told, register_phrase
from explain_beaucoup.engine.engine import ANALYSES, AnalyticalEngine
from explain_beaucoup.engine.explain import (SIGNALS, WEIGHT, Evidence,
                                             explain, register_signal)
from explain_beaucoup.semantic.sales_model import sales_model
from explain_beaucoup.semantic.specs import (DEFAULT_COMPARISONS, Capability,
                                             SemanticError)
from explain_beaucoup.view.stack import stack
from explain_beaucoup.view.theme import LIGHT
from explain_beaucoup.view.vega import VIEWS, register_view, spec_for


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    return load_frame()


@pytest.fixture
def s(frame) -> Session:
    return Session(frame, sales_model())


def _model(frame):
    m = sales_model()
    m.profile(frame)
    return m


# -- regressions ---------------------------------------------------------------

def test_change_contribution_does_not_assume_a_comparison_named_mom(frame):
    model = dataclasses.replace(sales_model(), comparisons=DEFAULT_COMPARISONS)
    s = Session(frame, model)
    s.run("change_contribution", {})
    assert s.graph.nodes[s.graph.current].kind == BREAKDOWN  # mother on top
    child = s.graph.child_nodes(s.node.uid)[-1]
    assert child.kind == CHANGE
    assert child.context.comparison == "prev"


def test_change_contribution_is_not_offered_without_a_valid_comparison(frame):
    model = dataclasses.replace(sales_model(), comparisons=())
    s = Session(frame, model)
    verbs = [i.verb for g in s.menu().groups for i in g]
    assert "change_contribution" not in verbs
    with pytest.raises(SemanticError):
        s.run("change_contribution", {})


def test_member_trend_label_follows_the_grain(s):
    s.set_time_grain("quarter")
    labels = [i.label for g in s.menu(key="Luzon").groups for i in g
              if i.verb == "trend"]
    assert labels == ["Trend Luzon over 8 quarters"]


# -- backends ------------------------------------------------------------------

@pytest.fixture(scope="module")
def engines(frame):
    pytest.importorskip("duckdb", reason="the DuckDB backend is optional")
    from explain_beaucoup.engine.backend import DuckDBBackend
    model = _model(frame)
    source = DataSource.build(frame, model.time_column,
                              native_grain=model.time_grain, name="sales")
    return (AnalyticalEngine(PandasBackend(source), model),
            AnalyticalEngine(DuckDBBackend.from_source(source), model))


def _ctx(**kw):
    base = dict(dataset="sales", metric="revenue",
                time=TimeWindow.single("2026-08"), grain=("region",))
    base.update(kw)
    filters = base.pop("filters", None)
    return Context.new(base.pop("dataset"), base.pop("metric"),
                       base.pop("time"), filters=filters, **base)


PARITY = [
    (BREAKDOWN, _ctx()),
    (BREAKDOWN, _ctx(comparison="mom", metric="margin_pct")),
    (BREAKDOWN, _ctx(grain=())),
    (CHANGE, _ctx(grain=("product",), comparison="yoy")),
    (TIMESERIES, _ctx(grain=("month",), time=TimeWindow("2025-09", "2026-08"),
                      comparison="yoy")),
    (TIMESERIES, _ctx(grain=("month",), time=TimeWindow("2025-Q1", "2026-Q2",
                                                        "quarter"))),
    (EXCEPTIONS, _ctx(grain=("province",), filters={"region": "Luzon"})),
    (DISTRIBUTION, _ctx(metric="avg_ticket")),
    (MATRIX, _ctx(grain=("region", "channel"), comparison="mom")),
    (MATRIX, _ctx(grain=("region", "month"),
                  time=TimeWindow("2025-09", "2026-08"), comparison="yoy")),
]


@pytest.mark.parametrize("kind,ctx", PARITY)
def test_duckdb_and_pandas_agree(engines, kind, ctx):
    pd_res, dk_res = (e.execute(ctx, kind) for e in engines)
    assert pd_res.fact_rows == dk_res.fact_rows
    assert pd_res.total == pytest.approx(dk_res.total, nan_ok=True)
    a = {r.key: (r.value, r.prior, r.delta) for r in pd_res.rows}
    b = {r.key: (r.value, r.prior, r.delta) for r in dk_res.rows}
    assert a.keys() == b.keys()
    for k in a:
        assert a[k] == pytest.approx(b[k], nan_ok=True), k


def test_duckdb_provenance_is_the_query_it_ran(engines):
    _, dk = engines
    res = dk.execute(_ctx(filters={"region": "Luzon"}, grain=("province",)))
    assert res.provenance.startswith('SELECT "province"')
    assert "\"region\"='Luzon'" in res.provenance
    assert 'GROUP BY "province"' in res.provenance


def test_a_session_runs_on_a_sql_backend(frame):
    pytest.importorskip("duckdb", reason="the DuckDB backend is optional")
    from explain_beaucoup.engine.backend import DuckDBBackend
    model = sales_model()
    source = DataSource.build(frame, model.time_column,
                              native_grain=model.time_grain, name="sales")
    s = Session(DuckDBBackend.from_source(source), model)
    assert model.member_counts["region"] == 3        # profiled through SQL
    s.activate(s.node.uid, "Luzon")
    s.run("cross", {"dimension": "month", "periods": 6})
    exp = s.explain()
    assert exp.evidence


# -- the two-axis grain -----------------------------------------------------------

def test_cell_keys_round_trip():
    assert split_cell(cell_key("Luzon", "2026-08")) == ("Luzon", "2026-08")


def test_cross_with_a_dimension_makes_a_matrix(s):
    landed = s.run("cross", {"dimension": "channel"})
    node = landed.node
    assert node.kind == MATRIX
    assert node.context.grain == ("region", "channel")
    res = s.result(node)
    assert res.axes == ("region", "channel")
    # Cells add up to the scope total on an additive metric.
    assert sum(r.value for r in res.rows) == pytest.approx(res.total)
    assert node.title == "Revenue by Region × Channel"


def test_cross_with_time_puts_time_on_the_columns(s):
    node = s.run("cross", {"dimension": "month", "periods": 6}).node
    assert node.context.grain == ("region", "month")
    assert node.context.time.length == 6
    res = s.result(node)
    cols = {dict(r.extra)["col"] for r in res.rows}
    assert len(cols) == 6


def test_a_matrix_comparison_shifts_the_time_column(s, frame):
    s.run("cross", {"dimension": "month", "periods": 3})
    s.raise_node(s.graph.child_nodes(s.node.uid)[-1].uid)
    s.set_comparison("yoy")
    res = s.result()
    luzon_aug = res.row(cell_key("Luzon", "2026-08"))
    last_year = frame[(frame.region == "Luzon") & (frame.month.astype(str)
                                                   .str.startswith("2025-08"))]
    assert luzon_aug.prior == pytest.approx(last_year.revenue.sum())


def test_drilling_a_cell_pins_both_axes(s):
    s.run("cross", {"dimension": "channel"})
    s.raise_node(s.graph.child_nodes(s.node.uid)[-1].uid)
    cell = s.result().rows[0].key
    row, col = split_cell(cell)
    landed = s.activate(s.node.uid, cell)
    ctx = landed.node.context
    assert ctx.filter_map == {"region": row, "channel": col}
    assert ctx.grain == ("province",)


def test_drilling_a_matrix_without_a_cell_keeps_its_columns(s):
    s.run("cross", {"dimension": "channel"})
    s.raise_node(s.graph.child_nodes(s.node.uid)[-1].uid)
    landed = s.run("drill_down", {"dimension": "province"})
    assert landed.node.context.grain == ("province", "channel")
    assert landed.node.kind == MATRIX


def test_drill_up_out_of_a_matrix_drops_the_columns(s):
    s.run("cross", {"dimension": "month", "periods": 6})
    s.raise_node(s.graph.child_nodes(s.node.uid)[-1].uid)
    landed = s.run("drill_up", {})
    assert landed.node.context.grain == ("region",)
    assert landed.node.context.time.length == 1


def test_a_matrix_cannot_be_crossed_again_or_have_one_axis_twice(s, frame):
    model = s.model
    two = _ctx(grain=("region", "channel"))
    with pytest.raises(SemanticError):
        apply(model, two, Operation.of("cross", dimension="product"))
    with pytest.raises(SemanticError):
        model.validate(_ctx(grain=("region", "region")))
    assert "cross" not in [c.verb for c in model.capabilities(two)]


def test_readings_of_a_matrix_use_one_axis(s):
    s.run("cross", {"dimension": "month", "periods": 6})
    s.raise_node(s.graph.child_nodes(s.node.uid)[-1].uid)
    landed = s.run("change_contribution", {})
    assert landed.node.kind == CHANGE
    assert landed.node.context.grain == ("region",)
    assert landed.node.context.time.length == 1


def test_explain_a_matrix_cell(s):
    s.run("cross", {"dimension": "channel"})
    s.raise_node(s.graph.child_nodes(s.node.uid)[-1].uid)
    cell = s.result().rows[0].key
    exp = s.explain(member=cell)
    row, col = split_cell(cell)
    assert exp.context.filter_map == {"region": row, "channel": col}


def test_a_matrix_renders_as_a_heatmap_with_cell_keys(s):
    node = s.run("cross", {"dimension": "channel"}).node
    res = s.result(node)
    spec = spec_for(s.model, res, LIGHT, selected=res.rows[0].key)
    cells = spec["layer"][0]
    assert cells["mark"]["type"] == "rect"
    assert all(" × " in d["key"] for d in spec["data"]["values"])
    assert sum(d["selected"] for d in spec["data"]["values"]) == 1
    panels = stack(s)["panels"]
    assert {p["renderer"] for p in panels} == {"vega-lite"}


def test_the_commentary_tells_a_cross(s):
    s.run("cross", {"dimension": "channel"})
    beat = s.commentary.beats()[-1]
    assert "Crossed" in beat.move


# -- registries --------------------------------------------------------------------

@pytest.fixture
def plugin_verb():
    @register_verb
    class Top3(Verb):
        """Keep the grain, read only the top three members."""
        name = "top3"
        placement = "replace"
        preserves_kind = True

        def apply(self, model, ctx, p, op):
            return ctx.evolve(self.step("Top three", p, op))

        def advertise(self, model, ctx):
            return [Capability(self.name, "Top three", (), "Composition")]

    register_phrase("top3", lambda say: Told("Kept the top three."))
    yield VERBS["top3"]
    VERBS.pop("top3")
    PHRASES.pop("top3")


def test_a_registered_verb_is_advertised_run_and_narrated(s, plugin_verb):
    labels = [i.label for g in s.menu().groups for i in g]
    assert "Top three" in labels
    item = next(i for g in s.menu().groups for i in g if i.verb == "top3")
    assert item.lands == "replace"
    s.run("top3", {})
    assert s.commentary.beats()[-1].move == "Kept the top three."


def test_a_registered_signal_joins_the_explanation(frame):
    model = _model(frame)
    engine = AnalyticalEngine(frame, model)

    def seasonality(p):
        return [Evidence("seasonality", None, None, "A seasonal dip",
                         "", WEIGHT["seasonality"], 1.0, -1)]

    register_signal("seasonality", seasonality, per="scope", weight=2.0)
    try:
        exp = explain(engine, model, _ctx(comparison="mom"))
        assert exp.evidence[0].kind == "seasonality"
    finally:
        SIGNALS.pop("seasonality")
        WEIGHT.pop("seasonality")


def test_a_registered_analysis_and_view(frame):
    from explain_beaucoup.engine.engine import breakdown, register_analysis
    model = _model(frame)
    engine = AnalyticalEngine(frame, model)

    @register_analysis("pareto", title=lambda m, c: "Pareto")
    def pareto(engine, ctx):
        return dataclasses.replace(breakdown(engine, ctx), kind="pareto")

    register_view("pareto", lambda *a: {"mesh": []}, renderer="three")
    try:
        res = engine.execute(_ctx(), "pareto")
        assert res.kind == "pareto"
        assert spec_for(model, res, LIGHT) == {"mesh": []}
    finally:
        ANALYSES.pop("pareto")
        VIEWS.pop("pareto")
