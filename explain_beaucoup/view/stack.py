"""The chart stack as data: the focus on top, the views drilled out of it below.

This is the payload the chart surface renders - Vega-Lite specs plus each
panel's header and actions. It is plain JSON-able data, so the Qt window's web
view and any other front end draw the same stack from the same rules.
"""

from __future__ import annotations

from typing import Any

from ..core.graph import Node
from ..core.session import Session
from ..semantic.specs import SemanticError
from .theme import THEMES
from .vega import spec_for, titles_for


def stack(session: Session, theme: str = "light", *,
          scroll_to: str | None = None) -> dict[str, Any]:
    """The focused node on top, the views drilled out of it underneath."""
    focus = session.node
    panels = [panel(session, focus, session.result(focus), theme, role="focus")]
    for child in session.graph.child_nodes(focus.uid):
        try:
            child_res = session.result(child)
        except SemanticError:
            continue        # a child whose scope no longer resolves
        panels.append(panel(session, child, child_res, theme, role="child"))
    return {"theme": theme, "scroll_to": scroll_to, "panels": panels}


def panel(session: Session, node: Node, res: Any, theme: str, *,
          role: str) -> dict[str, Any]:
    graph = session.graph
    compact = role == "child"
    head, sub = titles_for(session.model, res)
    actions: list[dict[str, Any]] = []
    badge = ""
    if compact:
        actions.append({"action": "raise", "label": "Raise to top",
                        "primary": True,
                        "hint": "Put this chart on top, where drilling it "
                                "opens its own children below"})
        actions.append({"action": "close", "label": "✕",
                        "hint": "Close this chart and anything below it"})
        deeper = len(node.children)
        if deeper:
            badge = f"{deeper} deeper"
    else:
        if node.parent and node.parent in graph.nodes:
            actions.append({"action": "up", "label": "↑ Parent",
                            "hint": f"Back to {graph.get(node.parent).title}"})
        actions.append({"action": "explain", "label": "Explain",
                        "hint": "Why this view looks the way it does"})
        actions.append({"action": "pin",
                        "label": "Unpin" if node.pinned else "Pin"})
    spec = spec_for(session.model, res, THEMES[theme],
                    selected=session.selected(node.uid), compact=compact)
    # The panel header carries the headline, so the plot does not repeat it.
    spec.pop("title", None)
    scope = node.context.scope_label() if node.context.filters else ""
    return {
        "uid": node.uid,
        "context_id": node.context.id,
        "role": role,
        "active": node.uid == graph.current,
        # Two children of the same mother differ by scope, not by headline,
        # so the scope leads the title.
        "title": f"{scope} — {head}" if scope and compact else head,
        "subtitle": sub,
        "badge": badge,
        "actions": actions,
        "spec": spec,
    }
