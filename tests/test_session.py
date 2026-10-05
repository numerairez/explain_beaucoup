"""Tests for the headless session.

These pin down the interaction rules the Qt window used to decide on its own:
where a result lands, which chart kind it is read as, what the menu offers,
and what the journal records. Any front end driving a `Session` gets exactly
this behaviour.
"""

from __future__ import annotations

import pandas as pd
import pytest

from explain_beaucoup.core import journal as J
from explain_beaucoup.core.operations import (BREAKDOWN, CHANGE, EXCEPTIONS,
                                              TIMESERIES)
from explain_beaucoup.core.session import Landing, Session
from explain_beaucoup.data.generate import load_frame
from explain_beaucoup.semantic.sales_model import sales_model
from explain_beaucoup.semantic.specs import SemanticError
from explain_beaucoup.view.stack import stack


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    return load_frame()


@pytest.fixture
def s(frame) -> Session:
    return Session(frame, sales_model())


def actions(s: Session) -> list[str]:
    return [e.action for e in s.journal]


# -- landing -----------------------------------------------------------------

def test_lands_on_the_models_first_grain(s):
    assert s.node.uid == "n1"
    assert s.node.kind == BREAKDOWN
    assert s.node.context.grain == ("region",)
    assert s.node.title == "Revenue by Region"
    assert actions(s) == [J.START]


def test_two_sessions_do_not_share_a_uid_sequence(frame):
    a, b = Session(frame, sales_model()), Session(frame, sales_model())
    a.activate(a.node.uid, "Luzon")
    assert b.node.uid == "n1"
    assert set(a.graph.nodes) == {"n1", "n2"}


# -- the stack ---------------------------------------------------------------

def test_a_drill_opens_below_and_keeps_the_mother_on_top(s):
    root = s.node.uid
    landed = s.activate(root, "Luzon")
    assert isinstance(landed, Landing) and landed.below and not landed.reused
    assert s.node.uid == root                       # mother keeps the top slot
    assert s.selected(root) == "Luzon"              # drilled-from bar marked
    child = landed.node
    assert child.parent == root
    assert child.context.filters == (("region", "Luzon"),)
    assert child.context.grain == ("province",)
    assert "Opened below" in landed.message


def test_the_same_drill_twice_reuses_the_chart(s):
    first = s.activate(s.node.uid, "Luzon")
    again = s.activate(s.node.uid, "Luzon")
    assert again.reused and again.node.uid == first.node.uid
    assert len(s.graph.child_nodes(s.node.uid)) == 1
    assert "Already open" in again.message


def test_in_place_verbs_replace_the_top_chart(s):
    landed = s.set_comparison("mom")
    assert not landed.below
    assert s.node is landed.node
    assert s.node.context.comparison == "mom"


def test_a_re_framing_verb_keeps_the_lens(s):
    s.raise_node(s.lens("Change").node.uid)
    assert s.node.kind == CHANGE
    s.set_metric("margin")
    assert s.node.kind == CHANGE                    # still read as a change


def change_on_top(s: Session, period: str = "yoy") -> None:
    s.run("compare", {"period": period})
    s.raise_node(s.run("change_contribution", {}).node.uid)
    assert s.node.kind == CHANGE


@pytest.mark.parametrize("verb, params", [
    ("decompose", {"dimension": "customer_segment"}),
    ("decompose", {"dimension": "channel"}),
    ("drill_down", {"dimension": "province"}),
])
def test_splitting_a_movement_keeps_reading_it_as_change(s, verb, params):
    """Breaking Luzon's +1.6M down shows how the 1.6M divides - not this
    period and the reference side by side."""
    change_on_top(s)
    luzon = s.result().row("Luzon")
    landed = s.run(verb, params, member="Luzon")
    assert landed.node.kind == CHANGE
    res = s.result(landed.node)
    assert res.delta_total == pytest.approx(luzon.delta)
    assert sum(r.delta for r in res.rows) == pytest.approx(luzon.delta)
    assert "Luzon" in landed.node.title


def test_every_way_of_splitting_a_change_chart_keeps_it(s):
    change_on_top(s)
    top = s.node.uid
    assert s.activate(top, "Luzon").node.kind == CHANGE      # double-click
    assert s.branch("channel").node.kind == CHANGE
    assert s.set_time_grain("quarter").node.kind == CHANGE
    s.goto(top)
    inner = s.run("drill_down", {"dimension": "province"}, member="Luzon")
    s.raise_node(inner.node.uid)
    assert s.run("drill_up", {}).node.kind == CHANGE


def test_change_falls_back_where_it_cannot_be_read(s):
    change_on_top(s)
    top = s.node.uid
    # Pinned to one member, the bar would own 100% of its own movement.
    assert s.run("focus", {"dimension": "region"},
                 member="Luzon").node.kind == BREAKDOWN
    s.goto(top)
    # Nothing to move against.
    assert s.run("clear_comparison", {}).node.kind == BREAKDOWN
    s.goto(top)
    # The total has no children to attribute the movement to.
    assert s.run("drill_up", {}).node.kind == BREAKDOWN


def test_a_change_chart_menu_says_what_a_split_shows(s):
    change_on_top(s)
    menu = s.menu(s.node.uid, "Luzon")
    items = [it for g in menu.groups for it in g]
    labels = [it.label for it in items]
    assert "Break down Luzon's change by Customer Segment" in labels
    assert "Break down Luzon's change by Province" in labels
    # Already read as change: the separate change item would duplicate the
    # drill, so it is not offered.
    assert not any(it.verb == "change_contribution" for it in items)


def test_crossing_a_member_crosses_it_along_its_hierarchy(s):
    """Laguna crossed with Channel is Laguna's cities by channel - not the
    whole chart it was clicked on."""
    change_on_top(s, "mom")
    prov = s.run("drill_down", {"dimension": "province"}, member="Luzon")
    s.raise_node(prov.node.uid)
    menu = s.menu(s.node.uid, "Laguna")
    item = next(it for g in menu.groups for it in g
                if it.verb == "cross" and it.params["dimension"] == "channel")
    assert item.label == "Cross Laguna by City × Channel"
    landed = s.invoke(s.node.uid, item)
    ctx = landed.node.context
    assert ctx.grain == ("city", "channel")
    assert ctx.filter_map == {"region": "Luzon", "province": "Laguna"}
    assert "Laguna" in landed.node.title


def test_a_leaf_or_a_period_crosses_the_whole_chart(s):
    city = s.run("drill_down", {"dimension": "province"}, member="Luzon")
    s.raise_node(city.node.uid)
    city = s.run("drill_down", {"dimension": "city"}, member="Laguna")
    s.raise_node(city.node.uid)
    leaf = s.result().rows[0].key
    items = [it for g in s.menu(s.node.uid, leaf).groups for it in g
             if it.verb == "cross"]
    assert items and all(it.label.startswith("Cross this chart with")
                         and it.member is None for it in items)
    with pytest.raises(SemanticError, match="no level below City"):
        s.run("cross", {"dimension": "channel"}, member=leaf)


def test_the_level_below_is_not_offered_as_a_cross(s):
    crosses = [it.params["dimension"] for g in s.menu(s.node.uid).groups
               for it in g if it.verb == "cross"]
    assert "province" not in crosses        # Region's own child level


def test_too_little_history_for_exceptions_is_said_not_drawn(s):
    s.run("set_grain", {"grain": "year"})
    with pytest.raises(SemanticError, match="at least 4 earlier years"):
        s.run("exceptions", {})


def test_a_carried_lens_that_cannot_be_read_falls_back_and_says_why(s):
    s.raise_node(s.run("exceptions", {}).node.uid)
    landed = s.set_time_grain("year")
    assert landed.node.kind == BREAKDOWN
    assert "Shown as a breakdown" in landed.message
    assert actions(s)[-1] != J.BLOCKED          # the move itself was fine


def test_a_distribution_point_is_not_a_member(s):
    s.raise_node(s.run("distribution", {}).node.uid)
    point = s.result().rows[0].key
    menu = s.menu(s.node.uid, point)
    assert menu.heading.startswith(point)
    assert all(it.member is None for g in menu.groups for it in g)
    assert s.activate(s.node.uid, point) is None
    for verb, params in [("decompose", {"dimension": "channel"}),
                         ("drill_down", {"dimension": "province"})]:
        s.goto(s.graph.current)
        landed = s.run(verb, params, member=point)
        assert not s.result(landed.node).is_empty


def test_explaining_a_trend_explains_a_point(s):
    s.raise_node(s.run("trend", {"periods": 12}).node.uid)
    latest = s.node.context.time.end
    assert s.explain().context.time.end == latest
    first = s.node.context.time.start
    exp = s.explain(member=first)
    assert (exp.context.time.start, exp.context.time.end) == (first, first)


def test_a_trend_with_a_comparison_breaks_down_its_latest_period(s):
    s.raise_node(s.run("trend", {"periods": 12}).node.uid)
    s.set_comparison("mom")
    landed = s.run("decompose", {"dimension": "channel"})
    ctx = landed.node.context
    assert ctx.time.length == 1 and ctx.comparison == "mom"


@pytest.mark.parametrize("setup", ["trend", "total"])
@pytest.mark.parametrize("lens, kind", [("Change", CHANGE),
                                        ("Exception", EXCEPTIONS)])
def test_change_and_exception_lenses_split_what_has_no_split(s, setup, lens,
                                                             kind):
    if setup == "trend":
        s.raise_node(s.run("trend", {"periods": 12}).node.uid)
    else:
        s.run("drill_up", {})
    landed = s.lens(lens)
    assert landed.node.kind == kind
    assert len(landed.node.context.grain) == 1
    assert landed.node.context.time.length == 1


def test_a_trend_is_a_timeseries_and_keeps_its_length_on_a_new_period(s):
    trend = s.lens("Trend").node
    assert trend.kind == TIMESERIES
    s.raise_node(trend.uid)
    length = s.node.context.time.length
    periods = s.periods(s.node.context.time.grain)
    s.set_period(periods[-3])
    assert s.node.context.time.end == periods[-3]
    assert s.node.context.time.length == length


def test_raising_a_child_is_a_return_and_drills_open_under_it(s):
    root = s.node.uid
    child = s.activate(root, "Luzon").node
    raised = s.raise_node(child.uid)
    assert s.node.uid == child.uid and raised.node.uid == child.uid
    assert actions(s)[-1] == J.RETURN
    grandchild = s.activate(child.uid, "Metro Manila").node
    assert grandchild.parent == child.uid
    assert s.back().node.uid == root


def test_acting_on_a_lower_chart_raises_it_first(s):
    root = s.node.uid
    child = s.activate(root, "Visayas").node
    s.run_from(child.uid, "compare", {"period": "mom"})
    assert s.node.uid != root
    assert s.node.context.filters == (("region", "Visayas"),)
    assert s.node.context.comparison == "mom"


def test_close_takes_the_subtree_but_never_the_top_chart(s):
    root = s.node.uid
    child = s.activate(root, "Luzon").node
    s.raise_node(child.uid)
    s.activate(child.uid, "Metro Manila")
    assert s.close(child.uid) is None               # it is on top
    s.back()
    msg = s.close(child.uid)
    assert msg == "Closed: Revenue by Province and 1 view below it"
    assert set(s.graph.nodes) == {root}
    assert actions(s)[-1] == J.CLOSE


# -- refusals ----------------------------------------------------------------

def test_a_refusal_raises_and_is_recorded(s):
    s.run("drill_up")                               # region -> scope total
    before = dict(s.graph.nodes)
    with pytest.raises(SemanticError):
        s.run("drill_up")
    assert s.graph.nodes == before
    last = s.journal.entries[-1]
    assert last.action == J.BLOCKED and last.verb == "drill_up"
    assert last.detail


def test_a_leaf_member_reads_the_whole_view_through_a_hierarchy_lens(s):
    s.raise_node(s.activate(s.node.uid, "Luzon").node.uid)
    s.raise_node(s.activate(s.node.uid, "Metro Manila").node.uid)
    leaf = s.node
    assert s.model.child_dimension(leaf.context.grain[0]) is None
    s.select(leaf.uid, s.result(leaf).rows[0].key)
    landed = s.lens("Exception")
    assert landed.node.kind == EXCEPTIONS
    assert landed.node.context.filters == leaf.context.filters


# -- the grammar, advertised -------------------------------------------------

def test_the_menu_speaks_about_the_mark(s):
    menu = s.menu(s.node.uid, "Visayas")
    labels = [i.label for g in menu.groups for i in g]
    assert menu.heading.startswith("Visayas — ")
    assert "Drill into Visayas by Province" in labels
    assert "Break down Visayas's change by Province" in labels
    assert "Focus on Visayas (keep this grain)" in labels
    assert "Raise this chart to the top" not in labels
    assert not menu.note
    assert s.selected() == "Visayas"


def test_a_menu_from_below_can_raise_and_close(s):
    child = s.activate(s.node.uid, "Luzon").node
    menu = s.menu(child.uid, None)
    by_action = {i.action: i for g in menu.groups for i in g}
    assert menu.note
    assert {"raise", "close", "branch", "pin"} <= set(by_action)
    s.invoke(child.uid, by_action["raise"])
    assert s.node.uid == child.uid


def test_a_menu_item_runs_on_its_own_panel(s):
    root = s.node.uid
    child = s.activate(root, "Luzon").node
    item = next(i for g in s.menu(child.uid, "Metro Manila").groups for i in g
                if i.verb == "drill_down")
    landed = s.invoke(child.uid, item)
    assert landed.node.parent == child.uid
    assert ("province", "Metro Manila") in landed.node.context.filters


def test_metrics_nest_in_a_submenu(s):
    items = [i for g in s.menu().groups for i in g if i.submenu]
    assert items and all(i.submenu == "Switch metric" for i in items)
    assert not any(i.label.startswith("Switch metric") for i in items)


# -- explain, evidence, pins -------------------------------------------------

def test_explain_a_member_and_open_its_evidence(s):
    s.set_comparison("mom")
    exp = s.explain(member="Luzon")
    assert ("region", "Luzon") in exp.context.filters
    assert s.last_explanation is exp
    assert actions(s)[-1] == J.EXPLAIN
    ev = next(e for e in exp.evidence if e.target is not None)
    landed = s.open_evidence(ev)
    assert landed.message.startswith("Opened evidence")
    assert actions(s)[-1] == J.EVIDENCE


def test_explain_through_run(s):
    assert s.run("explain").evidence


def test_pin_toggles_and_writes_a_note(s):
    assert s.toggle_pin() == "Pinned for comparison and the report"
    assert s.node.pinned and s.node.note.startswith("Revenue ")
    assert s.toggle_pin() == "Unpinned"
    assert not s.node.pinned
    assert actions(s)[-2:] == [J.PIN, J.UNPIN]


def test_branch_opens_a_new_path(s):
    dim = s.branch_options()[0]
    landed = s.branch(dim)
    assert landed.node.branch != s.node.branch


# -- the payload a front end renders ----------------------------------------

def test_the_stack_payload(s):
    root = s.node.uid
    s.activate(root, "Luzon")
    s.activate(root, "Visayas")
    payload = stack(s, "dark", scroll_to=root)
    roles = [p["role"] for p in payload["panels"]]
    assert roles == ["focus", "child", "child"]
    assert payload["theme"] == "dark"
    focus, kid = payload["panels"][0], payload["panels"][1]
    assert focus["active"] and not kid["active"]
    assert kid["title"] == "Luzon — Revenue by Province"
    assert {a["action"] for a in kid["actions"]} == {"raise", "close"}
    assert "title" not in focus["spec"]


def test_listeners_hear_every_move(s):
    heard = []
    s.journal_listeners.append(heard.append)
    s.activate(s.node.uid, "Luzon")
    s.toggle_pin()
    assert [e.action for e in heard] == [J.OPERATION, J.PIN]


# -- saying where a move lands -----------------------------------------------

def test_a_replacement_says_so_and_how_to_get_back(s):
    landed = s.set_comparison("mom")
    assert landed.message.startswith(
        "Replaced the top chart: Revenue by Region — vs MOM")
    assert "↑ Parent goes back to Revenue by Region" in landed.message


def test_every_menu_verb_says_where_it_lands(s):
    items = {i.verb: i.lands for g in s.menu(s.node.uid, "Luzon").groups
             for i in g if i.action == "run"}
    assert items["explain"] == ""
    assert items["drill_down"] == "below"
    assert items["focus"] == "below"
    assert items["compare"] == "replace"
    assert items["switch_metric"] == "replace"
