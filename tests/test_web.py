"""Tests for the web API: the contract the React workspace renders from.

The interaction rules themselves are covered in test_session.py; these check
that the API reaches them and reads the result out faithfully.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from explain_beaucoup.web.server import create_app  # noqa: E402

MODELS = Path(__file__).resolve().parent.parent / "models"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app(MODELS))


@pytest.fixture
def sid(client) -> str:
    return client.post("/api/sessions", json={"entry": 0}).json()["session"]


def move(client, sid, **body):
    r = client.post(f"/api/sessions/{sid}/move", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_the_catalogue_lists_the_models(client):
    entries = client.get("/api/catalog").json()["entries"]
    assert entries and entries[0]["ok"]
    detail = client.get("/api/catalog/0").json()
    assert detail["rows"] > 0 and detail["periods"]
    assert {f["value"] for f in detail["filterable"]} >= {"region", "province"}


def test_member_lists_cross_filter(client):
    p = client.post("/api/catalog/0/preview", json={
        "filters": {"region": ["Luzon"]}, "dims": ["region", "province"]}).json()
    assert p["rows"] < p["total_rows"] and p["ok"]
    assert len(p["members"]["region"]) == 3          # its own filter excluded
    assert "Cebu" not in p["members"]["province"]    # narrowed by region
    assert p["label"] == "Luzon"


def test_an_empty_scope_is_refused_by_name(client):
    r = client.post("/api/sessions", json={
        "entry": 0, "filters": {"region": ["Luzon"], "province": ["Cebu"]}})
    assert r.status_code == 422
    assert "no rows" in r.json()["detail"]


def test_a_scoped_session_says_so(client):
    sid = client.post("/api/sessions", json={
        "entry": 0, "filters": {"region": ["Luzon"]}}).json()["session"]
    snap = client.get(f"/api/sessions/{sid}").json()
    assert snap["scope_label"] == "Luzon"


def test_the_snapshot_carries_the_whole_workspace(client, sid):
    snap = client.get(f"/api/sessions/{sid}?theme=dark").json()
    assert snap["current"] == "n1"
    assert snap["stack"]["theme"] == "dark"
    assert snap["stack"]["panels"][0]["role"] == "focus"
    assert snap["toolbar"]["metric"] == "revenue"
    assert snap["crumbs"]["path"][0]["text"] == "by Region"
    assert snap["commentary"]["lines"][0]["move"].startswith("Opened on")
    assert snap["explanation"] is None


def test_a_drill_opens_below(client, sid):
    r = move(client, sid, type="activate", uid="n1", key="Luzon")
    assert r["message"].startswith("Opened below")
    roles = [p["role"] for p in r["state"]["stack"]["panels"]]
    assert roles == ["focus", "child"]
    assert r["state"]["stack"]["scroll_to"] == "n2"


def test_the_menu_round_trips_through_invoke(client, sid):
    move(client, sid, type="activate", uid="n1", key="Luzon")
    menu = client.post(f"/api/sessions/{sid}/menu",
                       json={"type": "select", "uid": "n2", "key": "Cavite"}).json()
    assert menu["note"]                                # from a chart below
    item = next(i for g in menu["groups"] for i in g if i["verb"] == "drill_down")
    r = move(client, sid, type="invoke", uid="n2", item=item)
    assert r["state"]["current"] == "n2"               # raised first
    assert len(r["state"]["stack"]["panels"]) == 2


def test_explain_then_open_evidence(client, sid):
    r = move(client, sid, type="explain", member="Luzon")
    assert r["explained"]
    ev = r["state"]["explanation"]["evidence"]
    assert ev and ev[0]["role"] in {"pos", "neg", "offset", "link", "muted"}
    first = next(e for e in ev if e["openable"])
    r = move(client, sid, type="evidence", index=first["index"])
    assert r["message"].startswith("Opened evidence")


def test_a_refusal_comes_back_as_blocked(client, sid):
    move(client, sid, type="run", verb="drill_up")
    r = move(client, sid, type="run", verb="drill_up")
    assert r["tone"] == "blocked"
    assert r["state"]["commentary"]["lines"][-1]["move"].startswith("Tried to")


def test_commentary_can_be_edited_and_copied(client, sid):
    move(client, sid, type="activate", uid="n1", key="Luzon")
    r = move(client, sid, type="hide_beat", index=1)
    assert r["state"]["commentary"]["removed"] == 1
    text = client.get(f"/api/sessions/{sid}/commentary?fmt=plain").text
    assert "Opened on" not in text and "Drilled into" in text
    r = move(client, sid, type="restore_beat")
    assert r["state"]["commentary"]["removed"] == 0
    assert client.get(f"/api/sessions/{sid}/commentary?fmt=json").text.startswith("{")


def test_export_and_unknown_sessions(client, sid):
    assert client.get(f"/api/sessions/{sid}/export").text.startswith("# Investigation")
    assert client.get("/api/sessions/nope").status_code == 404


def test_next_questions_say_where_they_land(client, sid):
    qs = client.get(f"/api/sessions/{sid}").json()["questions"]
    lands = {q["verb"]: q["lands"] for q in qs}
    assert lands["compare"] == "replace" and lands["drill_down"] == "below"
    assert lands["explain"] == ""
    r = move(client, sid, type="run", verb="compare", params={"period": "mom"})
    assert r["message"].startswith("Replaced the top chart")
