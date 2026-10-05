"""Canned dashboards: tiles are verbs replayed from the root, and digging into
one opens an ordinary investigation rooted where the tile left off."""

from __future__ import annotations

from pathlib import Path

import pytest

from explain_beaucoup.core import dashboard as dash
from explain_beaucoup.core.dashboard import DashboardError, Tile
from explain_beaucoup.core.operations import (CHANGE, EXCEPTIONS, TIMESERIES,
                                              BREAKDOWN)
from explain_beaucoup.core.session import Session, root_context
from explain_beaucoup.engine.engine import AnalyticalEngine
from explain_beaucoup.semantic.catalog import discover
from explain_beaucoup.semantic.loader import load_model_file
from explain_beaucoup.semantic.specs import SemanticError

MODELS = Path(__file__).resolve().parent.parent / "models"


@pytest.fixture(scope="module")
def loaded():
    return load_model_file(MODELS / "sales.yaml")


@pytest.fixture(scope="module")
def engine(loaded):
    return AnalyticalEngine(loaded.source, loaded.model)


@pytest.fixture(scope="module")
def root(loaded, engine):
    return root_context(loaded.model, engine)[0]


def tile(*steps, view=None) -> Tile:
    return Tile("t", "T", dash.parse_steps(list(steps), "test"), view)


# -- the file ---------------------------------------------------------------

def test_steps_are_verb_names_or_one_verb_mapped_to_params():
    steps = dash.parse_steps(["drill_up", {"compare": {"period": "mom"}}],
                             "x")
    assert [s.verb for s in steps] == ["drill_up", "compare"]
    assert steps[1].param_map == {"period": "mom"}


@pytest.mark.parametrize("raw, said", [
    ({"tiles": [{"steps": ["levitate"]}]}, "unknown verb 'levitate'"),
    ({"tiles": [{"steps": [{"a": 1, "b": 2}]}]}, "a step is a verb name"),
    ({"tiles": [{"view": "pie"}]}, "'pie' is not 'kpi'"),
    ({"tiles": [{"size": "huge"}]}, "'huge' is not one of"),
])
def test_a_bad_dashboard_is_refused_by_name(raw, said):
    with pytest.raises(DashboardError, match=said):
        dash.parse(raw)


def test_tile_ids_are_unique_slugs():
    d = dash.parse({"tiles": [{"title": "Revenue!"}, {"title": "Revenue"}]})
    assert [t.id for t in d.tiles] == ["revenue", "revenue-2"]


def test_a_dashboard_round_trips_through_yaml(tmp_path):
    d = dash.load(MODELS / "sales.dashboard.yaml")
    assert d is not None and d.tiles
    dash.save(d, tmp_path / "x.dashboard.yaml")
    assert dash.load(tmp_path / "x.dashboard.yaml") == d


def test_the_catalogue_skips_dashboards_and_counts_their_tiles():
    entries = discover(MODELS)
    assert all(not e.path.name.endswith(".dashboard.yaml") for e in entries)
    sales = next(e for e in entries if e.dataset == "sales")
    assert sales.tiles == len(dash.load(MODELS / "sales.dashboard.yaml").tiles)


# -- replay -----------------------------------------------------------------

def test_every_demo_tile_replays_and_checks(loaded, engine, root):
    d = dash.load(dash.path_for(loaded.path))
    issues = dash.check(loaded.model, engine, d, root)
    assert not [i for i in issues if i[0] == "error"], issues


def test_replay_lands_on_the_kind_the_session_would(loaded, engine, root):
    m = loaded.model
    r = dash.resolve(m, engine, tile({"trend": {"periods": 12}}), root)
    assert r.kind == TIMESERIES and r.context.time.length == 12
    r = dash.resolve(m, engine, tile({"compare": {"period": "mom"}},
                                     "change_contribution"), root)
    assert r.kind == CHANGE
    # A re-framing keeps the lens it was applied to, as in the stack.
    r = dash.resolve(m, engine, tile("exceptions",
                                     {"switch_metric": {"metric": "margin"}}),
                     root)
    assert r.kind == EXCEPTIONS and r.context.metric == "margin"
    # `view` overrides the family the steps imply.
    r = dash.resolve(m, engine, tile("exceptions", view="breakdown"), root)
    assert r.kind == BREAKDOWN


def test_a_refused_step_is_named(loaded, engine, root):
    with pytest.raises(SemanticError, match=r"step 2 \(drill_up\)"):
        dash.resolve(loaded.model, engine, tile("drill_up", "drill_up"), root)


def test_tiles_roll_forward_with_the_data(loaded, engine, root):
    r = dash.resolve(loaded.model, engine, tile(), root)
    assert r.context.time.end == engine.latest(loaded.model.time_grain)


# -- reading ----------------------------------------------------------------

def test_a_tile_reads_its_headline_why_and_exceptions(loaded, engine, root):
    m = loaded.model
    r = dash.resolve(m, engine, tile({"drill_down": {"dimension": "province"}},
                                     "exceptions"), root)
    reading = dash.read(m, engine, r)
    assert reading.headline is not None and reading.headline.delta is not None
    # The tile's own split leads the "why".
    assert reading.why is not None and reading.why.dimension == "province"
    assert reading.exceptions is not None


def test_a_trend_tile_headlines_its_latest_period(loaded, engine, root):
    r = dash.resolve(loaded.model, engine,
                     tile("drill_up", {"trend": {"periods": 12}}), root)
    reading = dash.read(loaded.model, engine, r)
    assert reading.headline is not None
    assert reading.headline.context.time.length == 1
    assert reading.exceptions is None


# -- digging in, and keeping what was found ---------------------------------

def test_a_session_can_start_from_a_tile(loaded, engine, root):
    r = dash.resolve(loaded.model, engine,
                     tile({"compare": {"period": "mom"}},
                          "change_contribution"), root)
    s = Session(loaded.source, loaded.model, root=r.context, kind=r.kind,
                title="What moved")
    assert s.node.title == "What moved" and s.node.kind == CHANGE
    assert [st.verb for st in s.node.context.lineage] == [
        "compare", "change_contribution"]
    landed = s.run("drill_down", {"dimension": "province"}, member="Luzon")
    assert landed.node.parent == s.graph.roots[0]


def test_a_node_becomes_a_tile_that_replays_to_it(loaded, engine, root):
    s = Session(loaded.source, loaded.model)
    s.run("set_grain", {"grain": "quarter"})
    s.run("decompose", {"dimension": "channel"}, member="Luzon")
    node = s.graph.get(s.graph.current)
    child = s.graph.child_nodes(node.uid)[0]
    t = dash.tile_from(loaded.model, engine, root, child.context, child.kind,
                       "x", set())
    # The data's last period is never saved; it is found again on replay.
    assert all("end" not in s.param_map for s in t.steps)
    assert dash.resolve(loaded.model, engine, t, root).context.id == \
        child.context.id


def test_a_view_opened_from_evidence_is_rebuilt_from_its_state(loaded,
                                                               engine, root):
    """Explain evidence lands on views whose lineage is not the grammar
    ("Explain scope total"). Such a view is rebuilt from what it is - metric,
    split, filters, window, comparison - and proven by replaying it."""
    s = Session(loaded.source, loaded.model)
    exp = s.explain()
    targets = [e for e in exp.evidence if e.target is not None
               and any(st.verb not in dash.VERBS for st in e.target.lineage)]
    assert targets
    for ev in targets:
        with pytest.raises(DashboardError):
            dash.steps_from(ev.target)
        t = dash.tile_from(loaded.model, engine, root, ev.target,
                           ev.target_kind, "x", set())
        r = dash.resolve(loaded.model, engine, t, root)
        assert r.context.id == ev.target.id


# -- the web API --------------------------------------------------------------

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from explain_beaucoup.web.server import create_app  # noqa: E402


@pytest.fixture
def models(tmp_path) -> Path:
    """A writable copy of the models folder - adding a tile writes a file."""
    data = (MODELS.parent / "explain_beaucoup" / "data").resolve()
    for p in MODELS.glob("*.yaml"):
        text = p.read_text().replace("../explain_beaucoup/data", str(data))
        (tmp_path / p.name).write_text(text)
    return tmp_path


@pytest.fixture
def client(models) -> TestClient:
    return TestClient(create_app(models))


def test_a_dataset_with_a_dashboard_opens_on_it(client):
    o = client.post("/api/boards", json={"entry": 0}).json()
    assert o["session"] is None
    b = client.get(f"/api/boards/{o['board']}").json()
    assert b["tiles"] and all("error" not in t for t in b["tiles"])
    kpi = next(t for t in b["tiles"] if t["view"] == "kpi")
    assert kpi["spec"] is None and kpi["headline"]["delta"]


def test_a_dataset_without_one_opens_straight_into_a_session(client, models):
    (models / "sales.dashboard.yaml").unlink()
    o = client.post("/api/boards", json={"entry": 0}).json()
    assert o["session"]
    snap = client.get(f"/api/sessions/{o['session']}").json()
    assert snap["board"] == {"id": o["board"], "tiles": 0, "tile": None,
                             "tile_title": None}


def test_digging_resumes_and_pins_flow_back(client):
    bid = client.post("/api/boards", json={"entry": 0}).json()["board"]
    tid = "revenue-by-channel"
    d = client.post(f"/api/boards/{bid}/tiles/{tid}/investigate",
                    json={"key": "Digital"}).json()
    sid = d["session"]
    snap = client.get(f"/api/sessions/{sid}").json()
    assert snap["selected"] == "Digital" and snap["board"]["tile"] == tid
    client.post(f"/api/sessions/{sid}/move", json={"type": "pin"})

    again = client.post(f"/api/boards/{bid}/tiles/{tid}/investigate",
                        json={}).json()
    assert again["session"] == sid                 # resumed, not restarted
    t = next(t for t in client.get(f"/api/boards/{bid}").json()["tiles"]
             if t["id"] == tid)
    assert len(t["investigation"]["pins"]) == 1


def test_a_chart_is_added_once_and_can_be_removed(client, models):
    bid = client.post("/api/boards", json={"entry": 0}).json()["board"]
    sid = client.post(f"/api/boards/{bid}/explore").json()["session"]
    client.post(f"/api/sessions/{sid}/move", json={
        "type": "run", "verb": "decompose",
        "params": {"dimension": "channel"}, "member": "Luzon"})
    snap = client.get(f"/api/sessions/{sid}").json()
    child = snap["stack"]["panels"][1]["uid"]

    r = client.post(f"/api/sessions/{sid}/dashboard", json={"uid": child})
    assert r.status_code == 200, r.text
    added = r.json()
    assert added["title"].startswith("Luzon — ")
    assert added["tile"] in (models / "sales.dashboard.yaml").read_text()

    dup = client.post(f"/api/sessions/{sid}/dashboard", json={"uid": child})
    assert dup.status_code == 422 and "Already on the dashboard" in \
        dup.json()["detail"]

    b = client.delete(f"/api/boards/{bid}/tiles/{added['tile']}").json()
    assert added["tile"] not in [t["id"] for t in b["tiles"]]


def test_a_kpi_tile_digs_into_a_total_that_reads_as_one(loaded, engine, root):
    """The scope total's one bar is labelled with the scope, and its menu
    is the whole view's - never `__total__` scoped as if it were a member."""
    r = dash.resolve(loaded.model, engine, tile("drill_up"), root)
    s = Session(loaded.source, loaded.model, root=r.context, kind=r.kind)
    uid = s.graph.current
    (row,) = s.result().rows
    assert row.label == "Total"
    menu = s.menu(uid, row.key)
    labels = [it.label for g in menu.groups for it in g]
    assert menu.heading.startswith("Total — ")
    assert not any("__total__" in t for t in labels + [menu.heading])
    assert all(it.member is None for g in menu.groups for it in g)
    assert s.mark_summary(uid, row.key).startswith("Total: ")

    landed = s.run("focus", {"dimension": "region", "member": "Luzon"})
    assert s.result(landed.node).rows[0].label == "Luzon"
