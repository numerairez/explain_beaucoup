"""Tests for using the framework with someone else's data.

Every test here builds a dataset that shares nothing with the bundled demo -
different columns, different currency, daily dates in a non-ISO layout - and
drives it through the same set-up path a new team would follow:

    init (scaffold) -> edit -> check -> load -> analyse
"""

from __future__ import annotations

import textwrap

import numpy as np
import pandas as pd
import pytest
import yaml

from explain_beaucoup.core import timegrain as tg
from explain_beaucoup.core.context import Context, TimeWindow
from explain_beaucoup.core.operations import (BREAKDOWN, TIMESERIES, Operation,
                                            apply)
from explain_beaucoup.data.source import (DataSource, DataSourceError,
                                        profile_frame, read_table)
from explain_beaucoup.engine.engine import AnalyticalEngine
from explain_beaucoup.engine.explain import explain
from explain_beaucoup.semantic.loader import (ModelError, build, check,
                                            load_model_file, scaffold)
from explain_beaucoup.semantic.specs import SemanticError

# --------------------------------------------------------------------------
# a dataset with nothing in common with the demo
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def clinic_frame() -> pd.DataFrame:
    rng = np.random.default_rng(11)
    days = pd.date_range("2026-01-01", "2026-06-30", freq="D")
    sites = {"North": ["Leeds", "York"], "South": ["Bristol", "Exeter"]}
    specialties = ["Cardiology", "Dermatology", "Orthopaedics"]
    rows = []
    for d in days:
        for area, clinics in sites.items():
            for clinic in clinics:
                for spec in specialties:
                    base = 40 * (1.4 if area == "North" else 1.0)
                    if d >= pd.Timestamp("2026-06-01") and spec == "Dermatology":
                        base *= 0.35            # a planted collapse
                    booked = max(int(base * rng.lognormal(0, 0.2)), 1)
                    attended = max(int(booked * rng.uniform(0.7, 0.95)), 0)
                    rows.append({
                        "appt_date": d.strftime("%d/%m/%Y"),   # non-ISO
                        "area": area, "clinic": clinic, "specialty": spec,
                        "booked": booked, "attended": attended,
                        "cost_gbp": round(booked * rng.uniform(70, 130), 2),
                    })
    return pd.DataFrame(rows)


CLINIC_MODEL = textwrap.dedent("""
    dataset: clinic
    title: Clinic Activity
    data:
      path: clinic.csv
    time:
      column: appt_date
      grain: day
      format: "%d/%m/%Y"
      grains: [day, week, month]
    dimensions:
      area:      {label: Area,      column: area}
      clinic:    {label: Clinic,    column: clinic, cell: true}
      specialty: {label: Specialty, column: specialty, cell: true}
    hierarchies:
      geography: {label: Geography, levels: [area, clinic]}
    metrics:
      booked:   {label: Booked,   kind: additive, format: number, column: booked}
      attended: {label: Attended, kind: additive, format: number, column: attended}
      cost:     {label: Cost,     kind: additive, format: currency, symbol: "£",
                 column: cost_gbp}
      attendance_rate:
        label: Attendance Rate
        kind: ratio
        format: percent
        numerator: attended
        denominator: booked
    comparisons:
      - {name: prev, label: "the previous {grain}", periods: 1}
    defaults:
      metric: booked
      grain: [specialty]
    """)


@pytest.fixture(scope="module")
def clinic(tmp_path_factory, clinic_frame):
    d = tmp_path_factory.mktemp("clinic")
    clinic_frame.to_csv(d / "clinic.csv", index=False)
    (d / "model.yaml").write_text(CLINIC_MODEL)
    return load_model_file(d / "model.yaml")


# -- time grains ------------------------------------------------------------

@pytest.mark.parametrize("grain,key,nxt", [
    ("day", "2026-08-14", "2026-08-15"),
    ("week", "2026-W33", "2026-W34"),
    ("month", "2026-08", "2026-09"),
    ("quarter", "2026-Q3", "2026-Q4"),
    ("year", "2026", "2027"),
])
def test_period_keys_round_trip_at_every_grain(grain, key, nxt):
    assert tg.add(key, grain, 1) == nxt
    assert tg.add(nxt, grain, -1) == key
    assert tg.key_of(tg.anchor(key, grain), grain) == key
    assert tg.diff(nxt, key, grain) == 1
    lo, hi = tg.bounds(key, grain)
    assert lo <= tg.anchor(key, grain) <= hi


@pytest.mark.parametrize("grain,a,b", [
    ("week", "2025-W52", "2026-W01"), ("quarter", "2026-Q4", "2027-Q1"),
    ("month", "2026-12", "2027-01"), ("day", "2026-12-31", "2027-01-01"),
])
def test_period_keys_sort_across_year_boundaries(grain, a, b):
    """The engine filters windows with a string comparison, so keys must sort
    chronologically as text - including over a year end."""
    assert a < b
    assert tg.add(a, grain, 1) == b


def test_iso_years_with_53_weeks_are_handled():
    """2026 is a 53-week ISO year - a classic off-by-one in period maths."""
    assert tg.add("2026-W52", "week", 1) == "2026-W53"
    assert tg.add("2026-W53", "week", 1) == "2027-W01"
    assert tg.diff("2027-W01", "2026-W52", "week") == 2


def test_windows_re_express_at_a_coarser_grain():
    w = TimeWindow("2026-08-03", "2026-08-21", "day")
    assert w.at_grain("month").label == "Aug 2026"
    assert w.at_grain("quarter").label == "Q3 2026"
    assert w.length == 19


# -- reading a team's file --------------------------------------------------

def test_non_iso_dates_are_parsed(clinic):
    assert clinic.source.native_grain == "day"
    assert clinic.source.span("day")[0] == "2026-01-01"
    assert clinic.source.periods("month")[:2] == ["2026-01", "2026-02"]


def test_month_strings_are_accepted_as_a_time_column():
    frame = pd.DataFrame({"period": ["2026-01", "2026-02"], "x": [1, 2]})
    src = DataSource.build(frame, "period")
    assert src.native_grain == "month"
    assert src.periods("month") == ["2026-01", "2026-02"]


def test_bare_years_are_accepted():
    frame = pd.DataFrame({"yr": [2024, 2025, 2026], "x": [1, 2, 3]})
    src = DataSource.build(frame, "yr")
    assert src.native_grain == "year"
    assert src.periods("year") == ["2024", "2025", "2026"]


def test_a_missing_time_column_says_so(clinic_frame):
    with pytest.raises(DataSourceError, match="not in the data"):
        DataSource.build(clinic_frame, "when")


def test_unreadable_files_are_reported(tmp_path):
    with pytest.raises(DataSourceError, match="No such data file"):
        read_table(tmp_path / "nope.csv")
    (tmp_path / "x.weird").write_text("a,b")
    with pytest.raises(DataSourceError, match="Don't know how to read"):
        read_table(tmp_path / "x.weird")


def test_only_coarser_grains_than_the_data_are_offered(clinic):
    assert clinic.source.grains() == ["day", "week", "month", "quarter", "year"]
    monthly = DataSource.build(
        pd.DataFrame({"m": ["2026-01", "2026-02"], "x": [1, 2]}), "m")
    assert "day" not in monthly.grains()


# -- the model file ---------------------------------------------------------

def test_scaffold_produces_a_loadable_model(tmp_path, clinic_frame):
    clinic_frame.to_csv(tmp_path / "clinic.csv", index=False)
    text = scaffold(clinic_frame, "clinic.csv", dataset="clinic")
    (tmp_path / "m.yaml").write_text(text)
    loaded = load_model_file(tmp_path / "m.yaml")
    assert loaded.model.dataset == "clinic"
    assert "booked" in loaded.model.metrics
    assert "specialty" in loaded.model.dimensions
    # Drill paths are never guessed.
    assert loaded.model.hierarchies == {}


def test_profiling_separates_dimensions_from_measures(clinic_frame):
    roles = {p.name: p.role for p in profile_frame(clinic_frame)}
    assert roles["appt_date"] == "time"
    assert roles["specialty"] == "dimension"
    assert roles["cost_gbp"] == "measure"


@pytest.mark.parametrize("bad,message", [
    ("metrics: {x: {kind: ratio, format: number}}", "numerator"),
    ("metrics: {x: {kind: additive, format: number}}", "column"),
    ("metrics: {x: {kind: nonsense, column: a, format: number}}", "kind"),
    ("metrics: {x: {kind: additive, column: a, format: fancy}}", "format"),
    ("hierarchies: {h: {levels: [nope]}}", "not a declared dimension"),
    ("defaults: {metric: ghost}", "not a declared metric"),
    ("relationships: [{metric: ghost, volume: a, rate: b}]", "not a declared metric"),
])
def test_model_errors_name_the_offending_key(bad, message):
    doc = yaml.safe_load(
        "dataset: t\ntime: {column: d}\n"
        "dimensions: {a: {column: a}}\n"
        "metrics: {ok: {kind: additive, column: v, format: number}}\n")
    doc.update(yaml.safe_load(bad) or {})
    with pytest.raises(ModelError, match=message):
        build(doc, tmp_path_none := __import__("pathlib").Path("."),
              load_data=False)


def test_a_model_without_a_time_section_is_rejected():
    with pytest.raises(ModelError, match="time"):
        build({"dataset": "t", "dimensions": {"a": {"column": "a"}},
               "metrics": {"m": {"kind": "additive", "column": "v",
                                 "format": "number"}}},
              __import__("pathlib").Path("."), load_data=False)


# -- checking ---------------------------------------------------------------

def test_check_catches_columns_that_are_not_in_the_data(clinic_frame, tmp_path):
    doc = yaml.safe_load(CLINIC_MODEL)
    doc["dimensions"]["ward"] = {"label": "Ward", "column": "ward_name"}
    doc["metrics"]["staff"] = {"kind": "additive", "column": "staff_count",
                               "format": "number"}
    model, source = build(doc, tmp_path, frame=clinic_frame)
    issues = {i.where: i for i in check(model, source) if i.level == "error"}
    assert "dimensions.ward" in issues
    assert "metrics.staff" in issues
    assert "not in the data" in issues["dimensions.ward"].message


def test_check_catches_a_non_numeric_metric(clinic_frame, tmp_path):
    doc = yaml.safe_load(CLINIC_MODEL)
    doc["metrics"]["oops"] = {"kind": "additive", "column": "specialty",
                              "format": "number"}
    model, source = build(doc, tmp_path, frame=clinic_frame)
    errors = [i for i in check(model, source) if i.level == "error"]
    assert any("not numeric" in i.message for i in errors)


def test_check_warns_about_a_hierarchy_in_the_wrong_order(clinic_frame, tmp_path):
    doc = yaml.safe_load(CLINIC_MODEL)
    doc["hierarchies"]["geography"]["levels"] = ["clinic", "area"]  # fine->coarse
    model, source = build(doc, tmp_path, frame=clinic_frame)
    warnings = [i for i in check(model, source) if i.level == "warning"]
    assert any("wrong order" in i.message for i in warnings)


def test_a_clean_model_has_no_errors(clinic):
    assert not [i for i in check(clinic.model, clinic.source)
                if i.level == "error"]


# -- analysing someone else's data -----------------------------------------

def test_the_landing_view_comes_from_the_model(clinic):
    assert clinic.model.first_grain() == ("specialty",)
    assert clinic.model.default_metric == "booked"


def test_currency_formatting_follows_the_metric(clinic):
    from explain_beaucoup.view import format as fmt
    assert fmt.value(clinic.model.metric("cost"), 1234.0).startswith("£")
    assert fmt.value(clinic.model.metric("booked"), 1234.0) == "1,234"


def test_engine_aggregates_at_any_grain(clinic):
    engine = AnalyticalEngine(clinic.source, clinic.model)
    frame = clinic.source.frame
    for grain, key in [("day", "2026-06-15"), ("month", "2026-06"),
                       ("week", "2026-W25")]:
        ctx = Context.new("clinic", "booked", TimeWindow.single(key, grain),
                          grain=("specialty",))
        res = engine.execute(ctx)
        lo, hi = tg.bounds(key, grain)
        dates = pd.to_datetime(frame["appt_date"], format="%d/%m/%Y")
        truth = frame[(dates >= lo) & (dates <= hi)]["booked"].sum()
        assert res.total == pytest.approx(float(truth))


def test_ratio_metrics_hold_on_a_new_dataset(clinic):
    engine = AnalyticalEngine(clinic.source, clinic.model)
    ctx = Context.new("clinic", "attendance_rate",
                      TimeWindow.single("2026-06", "month"), grain=("area",))
    res = engine.execute(ctx)
    frame = clinic.source.frame
    dates = pd.to_datetime(frame["appt_date"], format="%d/%m/%Y")
    june = frame[dates.dt.month == 6]
    for row in res.rows:
        sub = june[june.area == row.key]
        assert row.value == pytest.approx(
            float(sub.attended.sum()) / float(sub.booked.sum()))
    assert all(r.share is None for r in res.rows)


def test_explain_finds_a_story_it_was_never_told_about(clinic):
    """The planted collapse is in Dermatology - nothing in the framework
    knows that word."""
    engine = AnalyticalEngine(clinic.source, clinic.model)
    ctx = Context.new("clinic", "booked", TimeWindow.single("2026-06", "month"),
                      grain=("specialty",), comparison="prev")
    exp = explain(engine, clinic.model, ctx)
    assert exp.delta < 0
    drivers = [e for e in exp.evidence if e.kind == "change_contribution"]
    assert drivers and drivers[0].member == "Dermatology"


def test_grain_switching_is_an_operation_like_any_other(clinic):
    ctx = Context.new("clinic", "booked", TimeWindow.single("2026-06-30", "day"),
                      grain=("specialty",))
    weekly = apply(clinic.model, ctx, Operation.of("set_grain", grain="week"))
    assert weekly.time.grain == "week"
    assert weekly.time.end == "2026-W27"
    with pytest.raises(SemanticError, match="not available"):
        apply(clinic.model, ctx, Operation.of("set_grain", grain="year"))


def test_grain_switching_never_lands_past_the_end_of_the_data(clinic):
    """Re-keying a coarse window to a fine one can invent empty periods -
    a year window becomes twelve months, most with no data."""
    model, last = clinic.model, clinic.source.latest("month")
    yearly = Context.new("clinic", "booked", TimeWindow.single("2026", "year"),
                         grain=("specialty",))
    back = apply(model, yearly,
                 Operation.of("set_grain", grain="month", end=last))
    assert back.time.end == last == "2026-06"      # not December
    # On a time series the span is kept, still clamped to real data.
    trend = Context.new("clinic", "booked", TimeWindow.single("2026", "year"),
                        grain=("appt_date",))
    span = apply(model, trend,
                 Operation.of("set_grain", grain="month", end=last))
    assert (span.time.start, span.time.end) == ("2026-01", "2026-06")


def test_trend_uses_periods_of_the_contexts_own_grain(clinic):
    engine = AnalyticalEngine(clinic.source, clinic.model)
    ctx = Context.new("clinic", "booked", TimeWindow.single("2026-06", "month"),
                      grain=("specialty",))
    trended = apply(clinic.model, ctx, Operation.of("trend", periods=6))
    assert trended.time.length == 6
    assert trended.grain == ("appt_date",)
    res = engine.execute(trended, TIMESERIES)
    assert [r.key for r in res.rows] == ["2026-01", "2026-02", "2026-03",
                                         "2026-04", "2026-05", "2026-06"]


def test_partial_periods_at_the_edge_of_the_data_are_flagged(clinic):
    engine = AnalyticalEngine(clinic.source, clinic.model)
    ctx = Context.new("clinic", "booked",
                      TimeWindow.single("2026-W27", "week").trailing(4),
                      grain=("appt_date",))
    res = engine.execute(ctx, TIMESERIES)
    partial = [r.label for r in res.rows if dict(r.extra).get("partial")]
    assert partial                      # the data ends mid-week
    assert any("Partial" in n for n in res.notes)


def test_time_series_charts_are_grain_neutral(clinic):
    """A period key is not a date: "2026-W14" must not be pasted into a date
    string. Every point must carry a timestamp Vega can actually parse."""
    from explain_beaucoup.view.theme import LIGHT
    from explain_beaucoup.view.vega import spec_for
    engine = AnalyticalEngine(clinic.source, clinic.model)
    for grain, end, n in [("day", "2026-06-30", 10), ("week", "2026-W26", 4),
                          ("month", "2026-06", 6)]:
        ctx = Context.new("clinic", "booked",
                          TimeWindow.single(end, grain).trailing(n),
                          grain=("appt_date",))
        spec = spec_for(clinic.model, engine.execute(ctx, TIMESERIES), LIGHT)
        values = spec["data"]["values"]
        assert values, grain
        for v in values:
            assert pd.Timestamp(v["t"]) is not pd.NaT
        assert not any(pd.isna(pd.Timestamp(v["t"])) for v in values)


def test_distribution_cells_come_from_the_model(clinic):
    engine = AnalyticalEngine(clinic.source, clinic.model)
    from explain_beaucoup.core.operations import DISTRIBUTION
    ctx = Context.new("clinic", "cost", TimeWindow.single("2026-06", "month"),
                      grain=("specialty",))
    res = engine.execute(ctx, DISTRIBUTION)
    assert res.rows
    # cells were declared as clinic x specialty - 4 clinics x 3 specialties
    assert len(res.rows) == 12
    assert any("clinic" in n.lower() or "Clinic" in n for n in res.notes)


def test_the_menu_adapts_to_a_different_model(clinic):
    ctx = Context.new("clinic", "booked", TimeWindow.single("2026-06", "month"),
                      grain=("area",))
    caps = clinic.model.capabilities(ctx)
    labels = [c.label for c in caps]
    assert "Drill down to Clinic" in labels
    assert "Break down by Specialty" in labels
    assert any(l.startswith("View by") for l in labels)
    assert not any("Region" in l for l in labels)   # nothing from the demo
