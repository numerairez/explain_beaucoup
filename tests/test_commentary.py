"""The running commentary: a deterministic telling of what the analyst did.

Driven through the journal alone - no Qt - which is the same seam the window
records into.
"""

from __future__ import annotations

import pytest

from explain_beaucoup.core import journal as J
from explain_beaucoup.core.context import Context, TimeWindow
from explain_beaucoup.core.graph import InvestigationGraph
from explain_beaucoup.core.journal import Journal
from explain_beaucoup.core.operations import (BREAKDOWN, Operation, apply,
                                            result_kind)
from explain_beaucoup.data.generate import load_frame
from explain_beaucoup.engine.commentary import (PHRASES, Commentary,
                                              narrate_commentary,
                                              narrate_entry)
from explain_beaucoup.engine.engine import AnalyticalEngine
from explain_beaucoup.semantic.sales_model import sales_model
from explain_beaucoup.view import format as fmt

AUG = "2026-08"


@pytest.fixture(scope="module")
def model():
    m = sales_model()
    m.profile(load_frame())
    return m


@pytest.fixture(scope="module")
def engine(model):
    return AnalyticalEngine(load_frame(), model)


def _session(model):
    """A small investigation, recorded the way the window records it."""
    graph, journal = InvestigationGraph(), Journal()
    root_ctx = Context.new("sales", "revenue", TimeWindow.single(AUG),
                           grain=("region",))
    root = graph.add(root_ctx, title="Revenue by Region")
    journal.record(J.START, context=root_ctx, kind=BREAKDOWN, uid=root.uid,
                   title=root.title)

    def run(verb, node, *, branch=False, **params):
        op = Operation.of(verb, "test", **params)
        ctx = apply(model, node.context, op)
        kind = result_kind(model, ctx, op)
        new = (graph.branch_from(node.uid, ctx, kind=kind, op=op) if branch
               else graph.add(ctx, kind=kind, op=op, parent=node.uid))
        journal.record(J.OPERATION, verb=verb, params=params, context=ctx,
                       kind=kind, uid=new.uid, title=new.title,
                       prior=node.context, prior_kind=node.kind, branch=branch)
        return new

    cmp_ = run("compare", root, period="mom")
    luzon = run("drill_down", cmp_, dimension="province", member="Luzon")
    run("change_contribution", luzon)
    run("decompose", cmp_, branch=True, dimension="channel", member="Luzon")
    run("trend", root, periods=12, member="Luzon")
    run("exceptions", root)
    journal.record(J.PIN, context=luzon.context, kind=luzon.kind,
                   uid=luzon.uid, title=luzon.title, detail="Luzon moved")
    journal.record(J.BLOCKED, verb="change_contribution",
                   context=root_ctx, kind=BREAKDOWN, uid=root.uid,
                   detail="needs a dimensional grain.")
    return graph, journal


def test_every_move_gets_exactly_one_beat(engine, model):
    _, journal = _session(model)
    beats = Commentary(model, engine, journal).beats()
    assert [b.seq for b in beats] == [e.seq for e in journal]
    assert all(b.text.strip() for b in beats)


def test_commentary_is_deterministic(engine, model):
    _, j1 = _session(model)
    _, j2 = _session(model)
    fresh = AnalyticalEngine(load_frame(), model)
    a = narrate_commentary(model, engine, j1)
    b = narrate_commentary(model, fresh, j2)
    assert a == b


def test_beats_cite_engine_numbers(engine, model):
    _, journal = _session(model)
    start = journal.entries[0]
    res = engine.execute(start.context, BREAKDOWN)
    metric = model.metric("revenue")
    beat = narrate_entry(model, engine, start)
    assert fmt.value(metric, res.total, short=True) in beat.text
    assert res.rows[0].label in beat.text


def test_each_verb_reads_as_its_own_move(engine, model):
    _, journal = _session(model)
    text = [b.text for b in Commentary(model, engine, journal).beats()]
    assert text[0].startswith("Opened on")
    assert text[1].startswith("Set the reference to")
    assert "against" in text[1]                       # the movement is stated
    assert text[2].startswith("Drilled into **Luzon**")
    assert text[3].startswith("Asked what moved **Luzon**")
    assert "competing hypothesis" in text[4]
    assert text[5].startswith("Traced **Luzon** over")
    assert "own history" in text[6]
    assert "Luzon moved" in text[7]
    assert text[8].startswith("Tried to find what moved it - blocked")


def test_a_drill_is_answered_with_its_drivers_and_offsets(engine, model):
    _, journal = _session(model)
    entry = journal.entries[4]                  # Luzon by Channel, vs MOM
    beat = narrate_entry(model, engine, entry)
    res = engine.execute(entry.context, BREAKDOWN)
    assert res.delta_total < 0
    moved = sorted((r for r in res.rows if r.delta), key=lambda r: r.delta)
    biggest_fall, biggest_rise = moved[0], moved[-1]
    assert beat.question.startswith("Does Channel explain")
    answer = beat.answer
    assert answer.index(biggest_fall.label) < answer.index("decline")
    assert biggest_rise.label in answer.split("decline")[1]
    assert "pulled the other way" in answer


def test_drivers_move_with_the_total_and_offsets_against_it(engine, model):
    from explain_beaucoup.engine.commentary import _Say
    _, journal = _session(model)
    for entry in journal.entries[1:5]:
        mv = _Say(model, engine, entry).movers()
        assert mv is not None and mv.drivers
        assert all(d * mv.delta > 0 for _, d in mv.drivers)
        assert all(d * mv.delta < 0 for _, d in mv.offsets)
        ranked = [abs(d) for _, d in mv.drivers]
        assert ranked == sorted(ranked, reverse=True)


def test_a_drill_without_a_comparison_still_names_drivers(engine, model):
    j = Journal()
    root = Context.new("sales", "revenue", TimeWindow.single(AUG),
                       grain=("region",))
    op = Operation.of("drill_down", dimension="province", member="Luzon")
    c = apply(model, root, op)
    j.record(J.OPERATION, verb="drill_down", params=op.param_map, context=c,
             kind=BREAKDOWN, uid="n1", title="Luzon", prior=root)
    beat = Commentary(model, engine, j).beats()[0]
    assert "of the decline" in beat.answer
    assert "No comparison is set on the chart" in beat.answer


def test_ratio_drivers_are_never_given_a_share_of_the_move(engine, model):
    j = Journal()
    c = Context.new("sales", "margin_pct", TimeWindow.single(AUG),
                    grain=("region",), comparison="mom")
    j.record(J.OPERATION, verb="decompose", params={"dimension": "region"},
             context=c, kind=BREAKDOWN, uid="n1", title="Margin")
    answer = Commentary(model, engine, j).beats()[0].answer
    assert "% of the" not in answer
    assert "the increase" in answer or "the decline" in answer


def test_answers_indent_under_two_digit_items(engine, model):
    _, journal = _session(model)
    for uid in ("n0", "n00", "n0"):          # distinct moves, not repeats
        journal.record(J.RETURN, context=journal.entries[0].context,
                       kind=BREAKDOWN, uid=uid, title="Revenue by Region")
    md = Commentary(model, engine, journal).markdown()
    assert "\n12. " in md
    assert "\n    Revenue is" in md.split("\n12. ")[1]


def _clicked(model, prior, prior_kind, verb, **params):
    j = Journal()
    op = Operation.of(verb, **params)
    c = apply(model, prior, op)
    j.record(J.OPERATION, verb=verb, params=params, context=c,
             kind=result_kind(model, c, op), uid="n1", title="t",
             prior=prior, prior_kind=prior_kind)
    return j


def test_a_clicked_member_states_where_it_stood(engine, model):
    root = Context.new("sales", "revenue", TimeWindow.single(AUG),
                       grain=("region",), comparison="mom")
    j = _clicked(model, root, BREAKDOWN, "distribution", member="Visayas")
    beat = Commentary(model, engine, j).beats()[0]
    res = engine.execute(root, BREAKDOWN)
    row = res.row("Visayas")
    metric = model.metric("revenue")
    rank = [r.key for r in res.rows].index("Visayas") + 1
    assert beat.selected.startswith("**Visayas** stood at "
                                    + fmt.value(metric, row.value, short=True))
    assert f"{row.share:.0%} of Revenue" in beat.selected
    assert f"of {len(res.rows)} by Region" in beat.selected
    assert ("down " if row.delta < 0 else "up ") in beat.selected
    assert "Visayas" in beat.question               # the question is scoped


def test_a_clicked_period_is_named_as_a_period(engine, model):
    root = Context.new("sales", "revenue", TimeWindow.single(AUG),
                       grain=("region",))
    trend = apply(model, root, Operation.of("trend", periods=12,
                                            member="Luzon"))
    j = _clicked(model, trend, "timeseries", "decompose",
                 dimension="channel", member="2026-06")
    beat = Commentary(model, engine, j).beats()[0]
    assert "2026-06" not in beat.text
    assert beat.selected.startswith("**Jun 2026** stood at")
    assert "for Luzon" in beat.selected


def test_a_member_clicked_twice_in_a_row_is_described_once(engine, model):
    root = Context.new("sales", "revenue", TimeWindow.single(AUG),
                       grain=("region",))
    j = _clicked(model, root, BREAKDOWN, "decompose",
                 dimension="channel", member="Luzon")
    op = Operation.of("trend", periods=12, member="Luzon")
    c = apply(model, root, op)
    j.record(J.OPERATION, verb="trend", params=op.param_map, context=c,
             kind="timeseries", uid="n2", title="t", prior=root,
             prior_kind=BREAKDOWN)
    first, second = Commentary(model, engine, j).beats()
    assert first.selected and not second.selected


def _tree_session(model):
    """root -> compare -> Luzon drill, plus a pin and a return on the way."""
    j = Journal()
    root = Context.new("sales", "revenue", TimeWindow.single(AUG),
                       grain=("region",))
    j.record(J.START, context=root, kind=BREAKDOWN, uid="r", title="Root")
    cmp_ = apply(model, root, Operation.of("compare", period="mom"))
    j.record(J.OPERATION, verb="compare", params={"period": "mom"},
             context=cmp_, kind=BREAKDOWN, uid="c", parent="r", title="Cmp",
             prior=root, prior_kind=BREAKDOWN)
    p = {"dimension": "province", "member": "Luzon"}
    luzon = apply(model, cmp_, Operation.of("drill_down", **p))
    j.record(J.OPERATION, verb="drill_down", params=p, context=luzon,
             kind=BREAKDOWN, uid="l", parent="c", title="Luzon",
             prior=cmp_, prior_kind=BREAKDOWN)
    j.record(J.PIN, context=cmp_, kind=BREAKDOWN, uid="c", title="Cmp")
    j.record(J.RETURN, context=root, kind=BREAKDOWN, uid="r", title="Root")
    return j


def test_the_tree_layout_nests_moves_like_the_map(engine, model):
    from explain_beaucoup.engine.commentary import TREE
    lines = Commentary(model, engine, _tree_session(model)).outline(TREE)
    got = [(ln.beat.seq, ln.depth, ln.head) for ln in lines]
    # The pin sits under the chart it was made on, after the drill made
    # from that chart earlier; the return sits under the root, last.
    assert got == [(1, 0, True), (2, 1, True), (3, 2, True), (4, 2, False),
                   (5, 1, False)]


def test_the_timeline_layout_is_the_journal_in_order(engine, model):
    from explain_beaucoup.engine.commentary import TIMELINE
    lines = Commentary(model, engine, _tree_session(model)).outline(TIMELINE)
    assert [ln.beat.seq for ln in lines] == [1, 2, 3, 4, 5]
    assert {ln.depth for ln in lines} == {0}


def test_tree_markdown_indents_answers_under_their_item(engine, model):
    md = Commentary(model, engine, _tree_session(model)).markdown(
        layout="tree")
    assert "\n    - **#3** Drilled into **Luzon**" in md
    assert "\n      _What is driving Revenue" in md


def test_the_panel_html_escapes_labels_and_keeps_links(engine, model):
    from explain_beaucoup.view.theme import LIGHT
    html = Commentary(model, engine, _tree_session(model)).html(LIGHT)
    assert "href='node:l'" in html
    assert "<b>Luzon</b>" in html
    assert "**" not in html
    assert "name='s5'" in html                 # the newest beat is anchored


def test_closed_charts_are_no_longer_linked(engine, model):
    graph, journal = _session(model)
    commentary = Commentary(model, engine, journal)
    luzon = journal.entries[2].uid
    assert f"(node:{luzon})" in commentary.markdown(live=set(graph.nodes))
    graph.remove(luzon)
    md = commentary.markdown(live=set(graph.nodes))
    assert f"(node:{luzon})" not in md
    assert "Province" in md                    # the beat itself survives


def test_export_carries_no_in_app_links(engine, model):
    _, journal = _session(model)
    assert "node:" not in narrate_commentary(model, engine, journal)


def test_every_grammar_verb_has_a_phrase(model):
    from explain_beaucoup.core import operations
    import inspect
    src = inspect.getsource(operations.apply)
    for verb in ("drill_down", "decompose", "drill_up", "focus", "compare",
                 "trend", "switch_metric", "change_contribution",
                 "exceptions", "distribution", "set_time", "set_grain",
                 "clear_comparison"):
        assert f'"{verb}"' in src
        assert verb in PHRASES


# -- regressions found by driving the app ------------------------------------

def _op(model, journal, prior, verb, uid, prior_kind=BREAKDOWN, parent=None,
        **params):
    op = Operation.of(verb, **params)
    c = apply(model, prior, op)
    journal.record(J.OPERATION, verb=verb, params=params, context=c,
                   kind=result_kind(model, c, op), uid=uid, parent=parent,
                   title=uid, prior=prior, prior_kind=prior_kind)
    return c


def test_a_complete_latest_month_is_not_flagged_partial(engine):
    """Monthly rows are stamped on the 1st; August is still complete."""
    c = Context.new("sales", "revenue", TimeWindow.single(AUG).trailing(3),
                    grain=("month",))
    res = engine.execute(c, "timeseries")
    assert not any(dict(r.extra).get("partial") for r in res.rows)


def test_comparison_labels_follow_the_views_grain(engine, model):
    c = Context.new("sales", "revenue", TimeWindow.single("2026-Q2", "quarter"),
                    grain=("region",), comparison="yoy")
    j = Journal()
    j.record(J.OPERATION, verb="compare", params={"period": "yoy"}, context=c,
             kind=BREAKDOWN, uid="n1", title="t")
    text = Commentary(model, engine, j).beats()[0].text
    assert "the same quarter last year" in text
    assert "month" not in text


def test_an_incomplete_period_is_not_given_drivers(engine, model):
    c = Context.new("sales", "revenue", TimeWindow.single("2026-Q3", "quarter"),
                    grain=("region",), comparison="yoy")
    j = Journal()
    j.record(J.OPERATION, verb="compare", params={"period": "yoy"}, context=c,
             kind=BREAKDOWN, uid="n1", title="t")
    answer = Commentary(model, engine, j).beats()[0].answer
    assert "not like for like" in answer
    assert "drive" not in answer and "fell:" not in answer


def test_a_reference_before_the_data_is_not_a_movement(engine, model):
    c = Context.new("sales", "revenue", TimeWindow.single("2024-09"),
                    grain=("region",), comparison="mom")
    j = Journal()
    j.record(J.OPERATION, verb="compare", params={"period": "mom"}, context=c,
             kind=BREAKDOWN, uid="n1", title="t")
    answer = Commentary(model, engine, j).beats()[0].answer
    assert "nothing to measure against" in answer
    assert " up " not in answer


def test_a_member_the_grammar_dropped_is_not_narrated(engine, model):
    total = Context.new("sales", "revenue", TimeWindow.single(AUG))
    j = Journal()
    _op(model, j, total, "decompose", "n1", dimension="region",
        member="Mindanao")
    beat = Commentary(model, engine, j).beats()[0]
    assert "Mindanao" not in beat.move and not beat.selected


def test_raising_the_chart_just_opened_is_not_a_beat(engine, model):
    j = Journal()
    root = Context.new("sales", "revenue", TimeWindow.single(AUG),
                       grain=("region",))
    j.record(J.START, context=root, kind=BREAKDOWN, uid="r", title="Root")
    c = _op(model, j, root, "drill_down", "d", parent="r",
            dimension="province", member="Luzon")
    j.record(J.RETURN, context=c, kind=BREAKDOWN, uid="d", title="d")
    beats = Commentary(model, engine, j).beats()
    assert [b.number for b in beats] == [1, 2]
    assert all(b.action != J.RETURN for b in beats)


def test_the_same_reading_twice_points_back(engine, model):
    j = Journal()
    root = Context.new("sales", "revenue", TimeWindow.single(AUG),
                       grain=("region",))
    j.record(J.START, context=root, kind=BREAKDOWN, uid="r", title="Root")
    c = _op(model, j, root, "drill_down", "d", parent="r",
            dimension="province", member="Luzon")
    _op(model, j, c, "change_contribution", "x", parent="d")
    third = Commentary(model, engine, j).beats()[2]
    assert third.answer.endswith("Same reading as #2.")


def test_a_lone_member_is_not_given_a_share(engine, model):
    j = Journal()
    root = Context.new("sales", "revenue", TimeWindow.single(AUG),
                       grain=("province",), filters={"region": "Mindanao"},
                       comparison="mom")
    _op(model, j, root, "drill_down", "d", dimension="city",
        member="Davao del Sur")
    beat = Commentary(model, engine, j).beats()[0]
    assert "is the only City here" in beat.answer
    assert "100%" not in beat.answer


def test_a_trend_is_judged_on_complete_periods(engine, model):
    j = Journal()
    root = Context.new("sales", "revenue",
                       TimeWindow.single("2026-Q3", "quarter"),
                       grain=("region",))
    _op(model, j, root, "trend", "t", periods=8)
    answer = Commentary(model, engine, j).beats()[0].answer
    assert "Q3 2026 is still incomplete" in answer
    assert "low ₱122" not in answer


def test_plain_text_has_no_markup_and_keeps_the_tree(engine, model):
    text = Commentary(model, engine, _tree_session(model)).plain()
    assert "**" not in text and "](" not in text and "_What" not in text
    assert "\n        #3 Drilled into Luzon by Province." in text


def test_json_nests_like_the_map_and_round_trips(engine, model):
    import json
    data = json.loads(Commentary(model, engine, _tree_session(model)).json())
    (root,) = data["commentary"]
    assert root["type"] == "chart" and root["step"] == 1
    cmp_ = root["children"][0]
    assert [c["step"] for c in cmp_["children"]] == [3, 4]
    assert cmp_["children"][0]["type"] == "chart"          # the drill
    assert cmp_["children"][1] == {**cmp_["children"][1], "type": "note",
                                   "action": "pin"}
    assert "children" not in cmp_["children"][1]
    assert root["children"][1]["action"] == "return"
    drill = cmp_["children"][0]
    assert drill["context"]["filters"] == {"region": "Luzon"}
    assert "**" not in json.dumps(data)
    steps = []

    def walk(items):
        for it in items:
            steps.append(it["step"])
            walk(it.get("children", []))
    walk(data["commentary"])
    assert sorted(steps) == [1, 2, 3, 4, 5]                # nothing lost


# -- the same action is told once -------------------------------------------

def _twice(model, journal, action, **fields):
    for _ in range(2):
        journal.record(action, **fields)


def test_the_same_drill_twice_is_told_once(engine, model):
    j = Journal()
    root = Context.new("sales", "revenue", TimeWindow.single(AUG),
                       grain=("region",))
    j.record(J.START, context=root, kind=BREAKDOWN, uid="r", title="Root")
    p = {"dimension": "province", "member": "Luzon"}
    c = apply(model, root, Operation.of("drill_down", **p))
    for i, source in enumerate(("dblclick", "menu")):
        j.record(J.OPERATION, verb="drill_down", params=p, source=source,
                 context=c, kind=BREAKDOWN, uid="d", parent="r", title="d",
                 prior=root, prior_kind=BREAKDOWN, reused=bool(i))
    assert [b.action for b in Commentary(model, engine, j).beats()] == [
        J.START, J.OPERATION]


def test_explain_evidence_and_blocked_repeats_are_told_once(engine, model):
    j = Journal()
    root = Context.new("sales", "revenue", TimeWindow.single(AUG),
                       grain=("region",))
    j.record(J.START, context=root, kind=BREAKDOWN, uid="r", title="Root")
    _twice(model, j, J.EXPLAIN, context=root, kind=BREAKDOWN, uid="r",
           title="Root")
    _twice(model, j, J.BLOCKED, verb="drill_up", context=root, kind=BREAKDOWN,
           uid="r", title="Root", detail="Already at the top.")
    assert len(Commentary(model, engine, j).beats()) == 3


def test_a_different_action_is_not_a_repeat(engine, model):
    j = Journal()
    root = Context.new("sales", "revenue", TimeWindow.single(AUG),
                       grain=("region",))
    j.record(J.START, context=root, kind=BREAKDOWN, uid="r", title="Root")
    for member, uid in (("Luzon", "a"), ("Visayas", "b")):
        p = {"dimension": "province", "member": member}
        j.record(J.OPERATION, verb="drill_down", params=p,
                 context=apply(model, root, Operation.of("drill_down", **p)),
                 kind=BREAKDOWN, uid=uid, parent="r", title=uid,
                 prior=root, prior_kind=BREAKDOWN)
    assert len(Commentary(model, engine, j).beats()) == 3


def test_toggles_and_returns_are_moves_not_repeats(engine, model):
    j = Journal()
    root = Context.new("sales", "revenue", TimeWindow.single(AUG),
                       grain=("region",))
    j.record(J.START, context=root, kind=BREAKDOWN, uid="r", title="Root")
    for action in (J.PIN, J.UNPIN, J.PIN):
        j.record(action, context=root, kind=BREAKDOWN, uid="r", title="Root")
    assert [b.action for b in Commentary(model, engine, j).beats()] == [
        J.START, J.PIN, J.UNPIN, J.PIN]


def test_a_repeat_that_moves_focus_is_told_as_a_return(engine, model):
    """Re-applying an in-place move lands back on the chart it made before:
    the window records the repeat and a return, and only the return shows."""
    j = Journal()
    root = Context.new("sales", "revenue", TimeWindow.single(AUG),
                       grain=("region",))
    j.record(J.START, context=root, kind=BREAKDOWN, uid="r", title="Root")
    c = apply(model, root, Operation.of("compare", period="mom"))
    op = dict(verb="compare", params={"period": "mom"}, context=c,
              kind=BREAKDOWN, uid="c", parent="r", title="Cmp", prior=root,
              prior_kind=BREAKDOWN)
    j.record(J.OPERATION, **op)
    j.record(J.RETURN, context=root, kind=BREAKDOWN, uid="r", title="Root")
    j.record(J.OPERATION, reused=True, **op)
    j.record(J.RETURN, context=c, kind=BREAKDOWN, uid="c", title="Cmp")
    actions = [b.action for b in Commentary(model, engine, j).beats()]
    assert actions == [J.START, J.OPERATION, J.RETURN, J.RETURN]
