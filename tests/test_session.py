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
