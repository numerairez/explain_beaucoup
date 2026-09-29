"""Tests for choosing a dataset, and for the scope it is opened under.

Both happen before a Context exists, so none of this needs Qt. What is worth
guaranteeing:

  * a folder of models is described without reading their data, and a model
    that cannot load is *reported*, never silently dropped;
  * a scope narrows the governed frame itself - so totals, shares and
    cardinality are all recomputed against the subset - and leaves the model it
    was derived from untouched;
  * a scope that would leave nothing analysable is refused here, with a reason,
    rather than surfacing later as a SemanticError.
"""

from __future__ import annotations

import textwrap

import numpy as np
import pandas as pd
import pytest

from explain_beaucoup.core.context import Context, TimeWindow
from explain_beaucoup.engine.engine import AnalyticalEngine
from explain_beaucoup.engine.explain import explain
from explain_beaucoup.semantic import catalog
from explain_beaucoup.semantic.catalog import (Scope, ScopeError, apply_scope,
                                               describe, discover,
                                               frame_under, members_of,
                                               preview)
from explain_beaucoup.semantic.loader import load_model_file

MODEL = textwrap.dedent("""
    dataset: shop
    title: Shop
    data:
      path: shop.csv
    time:
      column: month
      grain: month
      grains: [month, quarter]
    dimensions:
      region:  {label: Region,  column: region}
      store:   {label: Store,   column: store, cell: true}
      channel: {label: Channel, column: channel}
    hierarchies:
      geography: {label: Geography, levels: [region, store]}
    metrics:
      revenue: {label: Revenue, kind: additive, format: currency, column: revenue}
      units:   {label: Units,   kind: additive, format: number,   column: units}
    comparisons:
      - {name: mom, label: the previous month, periods: 1}
    defaults:
      metric: revenue
      grain: [region]
    """)

STORES = {"North": ["Aberdeen", "Dundee"], "South": ["Bath", "Hove"]}


@pytest.fixture(scope="module")
def shop_dir(tmp_path_factory):
    rng = np.random.default_rng(7)
    months = [f"2026-{m:02d}" for m in range(1, 13)]
    rows = []
    for month in months:
        for region, stores in STORES.items():
            for store in stores:
                for channel in ("Online", "Store"):
                    rows.append({"month": month, "region": region,
                                 "store": store, "channel": channel,
                                 "revenue": round(rng.uniform(800, 4000), 2),
                                 "units": int(rng.integers(20, 200))})
    d = tmp_path_factory.mktemp("shop")
    pd.DataFrame(rows).to_csv(d / "shop.csv", index=False)
    (d / "shop.yaml").write_text(MODEL)
    return d


@pytest.fixture(scope="module")
def shop(shop_dir):
    return load_model_file(shop_dir / "shop.yaml")


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------

def test_describe_reads_the_model_without_touching_the_data(shop_dir):
    entry = describe(shop_dir / "shop.yaml")
    assert entry.ok
    assert entry.dataset == "shop" and entry.title == "Shop"
    assert dict(entry.dimensions)["region"] == "Region"
    assert dict(entry.metrics)["revenue"] == "Revenue"
    assert entry.hierarchies == (("Geography", ("region", "store")),)
    assert entry.time_grain == "month" and entry.grains == ("month", "quarter")
    assert entry.data_path == (shop_dir / "shop.csv").resolve()
    assert "3 dimensions" in entry.summary() and "2 metrics" in entry.summary()


def test_a_broken_model_is_listed_with_its_reason(shop_dir, tmp_path):
    (tmp_path / "bad.yaml").write_text("dimensions: {a: {}}\n")   # no metrics
    entry = describe(tmp_path / "bad.yaml")
    assert not entry.ok and "metrics" in entry.problem
    # Unparseable YAML is a reason too, not a crash.
    (tmp_path / "worse.yml").write_text("dataset: [oops\n")
    assert "not valid YAML" in describe(tmp_path / "worse.yml").problem


def test_a_model_whose_data_is_missing_cannot_be_opened(tmp_path):
    (tmp_path / "m.yaml").write_text(MODEL)          # shop.csv is not here
    entry = describe(tmp_path / "m.yaml")
    assert entry.error == "" and entry.data_missing and not entry.ok
    assert "data file is missing" in entry.problem


def test_discover_lists_a_folder_usable_models_first(shop_dir, tmp_path):
    for name, text in (("zeta.yaml", MODEL), ("alpha.yaml", "metrics: {}\n")):
        (shop_dir / name).write_text(text)
    try:
        entries = discover(shop_dir)
        names = [e.path.name for e in entries]
        assert set(names) == {"shop.yaml", "zeta.yaml", "alpha.yaml"}
        # alpha.yaml sorts first alphabetically but cannot load, so it is last.
        assert names[-1] == "alpha.yaml"
        assert [e.ok for e in entries] == [True, True, False]
    finally:
        for name in ("zeta.yaml", "alpha.yaml"):
            (shop_dir / name).unlink()
    assert discover(shop_dir / "nope") == []


# --------------------------------------------------------------------------
# scope
# --------------------------------------------------------------------------

def test_an_empty_scope_is_the_whole_table(shop):
    assert Scope().is_empty
    assert apply_scope(shop, Scope()) is shop
    p = preview(shop, Scope())
    assert p.rows == p.total_rows == shop.source.row_count
    assert p.ok and not p.notes


def test_scope_describes_itself_in_period_labels(shop):
    model = shop.model
    assert Scope.of({"region": ["North"]}).describe(model) == "North"
    assert (Scope.of({"store": ["Bath", "Hove", "Dundee", "Aberdeen"]})
            .describe(model)) == "4 of Store"
    assert Scope.of({}, start="2026-03", end="2026-05").describe(model) == \
        "Mar 2026 – May 2026"
    assert Scope.of({}, start="2026-03").describe(model) == "from Mar 2026"
    assert Scope.of({}, end="2026-05").describe(model) == "through May 2026"
    assert Scope.of({}, start="2026-03", end="2026-03").describe(model) == "Mar 2026"


def test_a_scope_narrows_the_governed_frame_and_re_profiles_it(shop):
    scope = Scope.of({"region": ["North"]}, start="2026-04", end="2026-09")
    scoped = apply_scope(shop, scope)

    assert scoped.source.row_count == 1 * 2 * 2 * 6      # region x store x channel x month
    assert scoped.source.span("month") == ("2026-04", "2026-09")
    # Cardinality is learned from the subset: two of the four stores are gone.
    assert scoped.model.member_counts["store"] == 2
    assert scoped.model.member_counts["region"] == 1
    assert scoped.scope_label == "North · Apr 2026 – Sep 2026"
    assert scoped.scope_label in scoped.model.title

    # The model and source it came from are untouched.
    assert shop.source.row_count == 2 * 2 * 2 * 12   # region x store x channel x month
    assert shop.model.member_counts["store"] == 4
    assert shop.model.title == "Shop"


def test_totals_are_totals_of_the_subset(shop):
    scope = Scope.of({"channel": ["Online"]})
    scoped = apply_scope(shop, scope)
    engine = AnalyticalEngine(scoped.source, scoped.model)
    ctx = Context.new(scoped.model.dataset, "revenue",
                      TimeWindow.single("2026-05", "month"), grain=("region",))
    res = engine.execute(ctx)

    raw = shop.frame
    expected = raw[(raw["month"] == "2026-05")
                   & (raw["channel"] == "Online")]["revenue"].sum()
    assert res.total == pytest.approx(expected)
    # Shares are shares of the scope, so they still add to one.
    assert sum(r.share for r in res.rows) == pytest.approx(1.0)
    # And the explain engine runs against the subset without special casing.
    assert explain(engine, scoped.model, ctx).evidence is not None


def test_a_collapsed_landing_grain_is_dropped_not_rendered_as_one_bar(shop):
    scoped = apply_scope(shop, Scope.of({"region": ["South"]}))
    assert shop.model.default_grain == ("region",)
    assert scoped.model.default_grain == ()
    # The next usable level down the hierarchy takes over.
    assert scoped.model.first_grain() == ("store",)


def test_a_scope_that_leaves_nothing_is_refused_with_a_reason(shop):
    with pytest.raises(ScopeError, match="selects no rows"):
        apply_scope(shop, Scope.of({"region": ["Atlantis"]}))
    assert preview(shop, Scope.of({"region": ["Atlantis"]})).problems

    # One store and one channel leaves no dimension to break anything down by.
    pinned = Scope.of({"region": ["North"], "store": ["Dundee"],
                       "channel": ["Store"]})
    assert not preview(shop, pinned).ok
    with pytest.raises(ScopeError, match="single value"):
        apply_scope(shop, pinned)


def test_preview_warns_about_history_too_short_to_trend(shop):
    p = preview(shop, Scope.of({}, start="2026-01", end="2026-02"))
    assert p.ok and p.periods == 2
    assert any("baselines" in n for n in p.notes)

    collapsed = preview(shop, Scope.of({"region": ["North"]}))
    assert collapsed.ok
    assert any("landing view" in n for n in collapsed.notes)


def test_members_cross_filter_so_a_picker_never_offers_an_empty_set(shop):
    assert members_of(shop.frame, "store") == ["Aberdeen", "Bath", "Dundee", "Hove"]
    north = frame_under(shop, Scope.of({"region": ["North"]}))
    assert members_of(north, "store") == ["Aberdeen", "Dundee"]
    assert members_of(shop.frame, "nope") == []


def test_open_model_is_load_plus_scope(shop_dir):
    loaded = catalog.open_model(shop_dir / "shop.yaml",
                               Scope.of({"region": ["South"]}))
    assert loaded.source.row_count == 2 * 2 * 12
    assert loaded.scope_label == "South"
    assert catalog.open_model(shop_dir / "shop.yaml").scope_label == ""
