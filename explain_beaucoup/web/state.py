"""A session, read out as plain data for the web client.

The Qt panels turn the same objects into widgets; this module turns them into
JSON. Nothing here decides anything analytical - the session already has.
Colours are sent as roles ("pos", "neg", ...), not hex, so the client keeps
one set of design tokens.
"""

from __future__ import annotations

from typing import Any

from ..core.graph import Node
from ..core.session import LENSES, Session
from ..engine.commentary import TREE
from ..engine.explain import Evidence, ExplanationResult
from ..engine.narrate import narrate_investigation, suggested_questions
from ..core import timegrain as tg
from ..semantic.specs import SemanticError
from ..view import format as fmt
from ..view.stack import stack

KIND_LABEL = {
    "change_contribution": "Change driver",
    "offset": "Offset",
    "anomaly": "Anomaly",
    "new_member": "New",
    "missing_member": "Missing",
    "concentration": "Concentration",
    "contribution": "Contribution",
    "metric_relationship": "Metric link",
}
# How many evidence cards the panel shows; `open_evidence` indexes into these.
EVIDENCE_SHOWN = 9


def snapshot(session: Session, *, theme: str = "light",
             layout: str = TREE, scroll_to: str | None = None,
             title: str = "", scope_label: str = "") -> dict[str, Any]:
    """Everything the workspace shows, in one payload."""
    node = session.node
    model = session.model
    res = session.result(node)
    return {
        "title": title or model.title or model.dataset.replace("_", " ").title(),
        "scope_label": scope_label,
        "theme": theme,
        "current": node.uid,
        "selected": session.selected(),
        "toolbar": toolbar(session),
        "stack": stack(session, theme, scroll_to=scroll_to),
        "crumbs": crumbs(session),
        "tree": [tree_item(session, r) for r in session.graph.roots],
        "pins": pins(session),
        "context": context(session, node, res),
        "questions": [{"text": t, "verb": v, "params": p}
                      for t, v, p in suggested_questions(model, node)],
        "explanation": (explanation(session, session.last_explanation)
                        if session.last_explanation else None),
        "commentary": commentary(session, layout),
        "report": narrate_investigation(model, session.engine, session.graph,
                                        node),
        "status": session.status(node),
    }


# -- toolbar -----------------------------------------------------------------

def toolbar(session: Session) -> dict[str, Any]:
    model = session.model
    node = session.node
    ctx = node.context
    grain = ctx.time.grain
    return {
        "metrics": [{"value": n, "label": m.label}
                    for n, m in model.metrics.items()],
        "grains": [{"value": g, "label": g.title()} for g in model.grains],
        "periods": [{"value": k, "label": tg.label_of(k, grain)}
                    for k in session.periods(grain)],
        "comparisons": [{"value": None, "label": "None"}] + [
            {"value": c.name, "label": c.name.upper(),
             "hint": c.label_for(grain)} for c in model.comparisons],
        "lenses": [{"value": lens, "label": lens} for lens in LENSES],
        "metric": ctx.metric,
        "grain": grain,
        "period": ctx.time.end,
        "comparison": ctx.comparison,
        "lens": session.lens_of(node),
        "can_up": bool(ctx.grain),
        "can_back": bool(node.parent),
        "pinned": node.pinned,
        "branch_options": [{"value": d, "label": model.label_of(d)}
                           for d in session.branch_options()],
    }


# -- navigation --------------------------------------------------------------

def crumbs(session: Session) -> dict[str, Any]:
    g = session.graph
    uid = session.node.uid
    item = lambda n: {"uid": n.uid, "text": session.crumb(n),  # noqa: E731
                      "pinned": n.pinned, "title": n.title}
    return {"path": [item(n) for n in g.path_to(uid)],
            "siblings": [item(n) for n in g.siblings(uid)]}


def tree_item(session: Session, uid: str) -> dict[str, Any]:
    node = session.graph.get(uid)
    return {
        "uid": uid,
        "text": session.crumb(node),
        "verb": node.op.verb.replace("_", " ") if node.op else "start",
        "title": node.title,
        "kind": node.kind,
        "pinned": node.pinned,
        "branch": node.branch,
        "current": uid == session.graph.current,
        "children": [tree_item(session, c) for c in node.children],
    }


def pins(session: Session) -> list[dict[str, Any]]:
    out = []
    for node in session.graph.pins:
        metric = session.model.metric(node.context.metric)
        try:
            val = fmt.value(metric, session.result(node).total, short=True)
        except SemanticError:
            val = "-"
        out.append({"uid": node.uid, "title": node.title,
                    "metric": metric.label, "value": val, "note": node.note})
    return out


# -- side panels ---------------------------------------------------------------

def context(session: Session, node: Node, res: Any) -> dict[str, Any]:
    """The Context object itself, on screen."""
    model = session.model
    ctx = node.context
    metric = model.metric(ctx.metric)
    rows = [
        ("dataset", ctx.dataset),
        ("metric", f"{metric.label}  ({metric.kind})"),
        ("aggregation", metric.aggregation),
        ("scope", "; ".join(f"{model.label_of(d)} = {m}"
                            for d, m in ctx.filters) or "—"),
        ("grain", " / ".join(model.label_of(g) for g in ctx.grain) or "total"),
        ("time", ctx.time.label),
        ("comparison", ctx.comparison.upper() if ctx.comparison else "—"),
        ("context id", ctx.id),
        ("node id", node.uid),
        ("result id", res.id),
        ("fact rows", f"{res.fact_rows:,}"),
        ("computed in", f"{res.computed_ms:.1f} ms"),
    ]
    return {
        "rows": [{"key": k, "value": v} for k, v in rows],
        "provenance": res.provenance,
        "notes": list(res.notes),
        "lineage": [{"verb": s.verb, "summary": s.summary, "source": s.source}
                    for s in ctx.lineage],
    }


def _role(ev: Evidence) -> str:
    if ev.kind == "offset":
        return "offset"
    if ev.kind in ("anomaly", "missing_member", "change_contribution"):
        return "neg" if ev.direction < 0 else "pos"
    if ev.kind == "metric_relationship":
        return "link"
    return "muted"


def explanation(session: Session, exp: ExplanationResult) -> dict[str, Any]:
    model = session.model
    metric = model.metric(exp.context.metric)
    delta = None
    if exp.delta is not None:
        good = (exp.delta >= 0) == metric.higher_is_better
        delta = {"text": fmt.signed(metric, exp.delta),
                 "pct": exp.delta_pct, "up": exp.delta >= 0,
                 "tone": "good" if good else "critical",
                 "comparison": (exp.comparison or "").upper()}
    return {
        "context_id": exp.context.id,
        "total": fmt.value(metric, exp.total),
        "delta": delta,
        "where": (f"{metric.label} · {exp.context.scope_label()} · "
                  f"{exp.context.time.label}"),
        "best_dimension": (model.label_of(exp.best_dimension)
                           if exp.best_dimension else None),
        "evidence": [{"index": i, "kind": ev.kind,
                      "label": KIND_LABEL.get(ev.kind, ev.kind.title()),
                      "role": _role(ev), "score": ev.score,
                      "headline": ev.headline, "detail": ev.detail,
                      "member": ev.member, "openable": ev.target is not None}
                     for i, ev in enumerate(exp.top(EVIDENCE_SHOWN))],
        "queries": len(exp.provenance),
    }


def commentary(session: Session, layout: str = TREE) -> dict[str, Any]:
    """The commentary as lines. Text stays in the beats' small markdown
    subset - `[label](node:uid)`, `**bold**`, `_italic_` - for the client to
    render; links to closed charts are already flattened."""
    from ..engine.commentary import _live

    c = session.commentary
    live = set(session.graph.nodes)
    lines = []
    for line in c.outline(layout, include_hidden=True):
        b = line.beat
        lines.append({
            "seq": b.seq, "number": b.number, "depth": line.depth,
            "head": line.head, "hidden": b.hidden, "uid": b.uid,
            "move": _live(b, b.move, live),
            "selected": _live(b, b.selected, live) if b.selected else "",
            "question": b.question,
            "answer": _live(b, b.answer, live),
        })
    newest = max((ln["seq"] for ln in lines), default=None)
    return {"layout": layout, "lines": lines, "newest": newest,
            "removed": len(c.hidden)}
