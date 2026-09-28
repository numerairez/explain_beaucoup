"""Deterministic narration of an investigation.

The plan's explanation contract is:

    AI request -> typed analytical operation -> deterministic result object
                                             -> narrative

This module implements the last arrow *without* an LLM: the numbers, the node
ids and the operations are all already decided, so the narrative is a
rendering problem. An AI sidecar would replace `narrate_*` only - it would
still receive ResultHandles and Evidence, never raw data, and the citations
below are what make its output checkable.
"""

from __future__ import annotations

from ..core.graph import InvestigationGraph, Node
from ..semantic.specs import SemanticModel
from ..view import format as fmt
from .engine import AnalyticalEngine, comparison_label
from .explain import ExplanationResult


def narrate_explanation(model: SemanticModel, exp: ExplanationResult,
                        limit: int = 4) -> str:
    metric = model.metric(exp.context.metric)
    scope = exp.context.scope_label() if exp.context.filters else "the whole book"
    lines: list[str] = []

    head = f"{metric.label} for {scope} in {exp.context.time.label} is " \
           f"{fmt.value(metric, exp.total)}"
    if exp.delta is not None and exp.prior_total:
        direction = "up" if exp.delta > 0 else "down"
        head += (f", {direction} {fmt.signed(metric, exp.delta)} "
                 f"({exp.delta_pct:+.1%}) against the "
                 f"{comparison_label(model, exp.context) or 'prior period'}")
    lines.append(head + ".")

    if exp.best_dimension:
        lines.append(
            f"The movement is least evenly spread across "
            f"**{model.label_of(exp.best_dimension)}**, which makes it the "
            f"strongest explanatory breakdown of the ones tested "
            f"({', '.join(model.label_of(d) for d in exp.dimensions_tested)}).")

    for i, ev in enumerate(exp.top(limit), 1):
        lines.append(f"{i}. **{ev.headline}.** {ev.detail}")

    offsets = [e for e in exp.evidence if e.kind == "offset"]
    if offsets:
        o = offsets[0]
        lines.append(
            f"Note the offset: {o.member} is moving against the aggregate, so "
            f"the headline number understates what is happening underneath it.")

    lines.append("")
    lines.append(f"_Computed from context `{exp.context.id}` over "
                 f"{len(exp.provenance)} governed queries. Every figure above "
                 f"comes from the analytical engine, not from prose._")
    return "\n\n".join(lines)


def narrate_investigation(model: SemanticModel, engine: AnalyticalEngine,
                          graph: InvestigationGraph,
                          node: Node | None = None) -> str:
    """Turn the path through the investigation graph into a report."""
    node = node or graph.current_node
    if node is None:
        return "_Nothing investigated yet._"
    path = graph.path_to(node.uid)

    lines = ["# Investigation", ""]
    for i, n in enumerate(path):
        metric = model.metric(n.context.metric)
        try:
            res = engine.execute(n.context, n.kind)
            total = fmt.value(metric, res.total)
            delta = (f", {fmt.signed(metric, res.delta_total)} vs "
                     f"{comparison_label(model, n.context) or 'prior'}"
                     if res.delta_total is not None else "")
        except Exception:                                    # pragma: no cover
            total, delta = "-", ""
        step = n.op.verb.replace("_", " ") if n.op else "start"
        lines.append(f"**{i+1}. {step}** - {n.title}")
        lines.append(f"   {metric.label}: {total}{delta}  "
                     f"`{n.uid}` / `{n.context.id}`")
        lines.append("")

    pins = graph.pins
    if pins:
        lines.append("## Pinned findings")
        lines.append("")
        for p in pins:
            note = f" - {p.note}" if p.note else ""
            lines.append(f"- **{p.title}**{note}  `{p.uid}`")
        lines.append("")

    branches = {b: ns for b, ns in graph.branches.items() if b != 0}
    if branches:
        lines.append("## Competing hypotheses")
        lines.append("")
        for b, ns in sorted(branches.items()):
            lines.append(f"- Branch {b}: " + "; ".join(n.title for n in ns))
        lines.append("")

    lines.append("---")
    lines.append(f"_{len(graph.nodes)} analytical nodes, "
                 f"{len(branches) + 1} path(s), {len(pins)} pin(s). "
                 f"Numbers computed by the analytical engine; "
                 f"contexts are reproducible by id._")
    return "\n".join(lines)


def suggested_questions(model: SemanticModel, node: Node) -> list[tuple[str, str, dict]]:
    """Context-sensitive next questions, generated from the semantic model.

    Returns (question, verb, params) so the UI can execute the very operation
    the question describes - the text and the action never drift apart.
    """
    ctx = node.context
    metric = model.metric(ctx.metric)
    out: list[tuple[str, str, dict]] = []
    current = ctx.grain[0] if ctx.grain else None
    on_time = current == model.time_column

    if not on_time:
        out.append((f"What makes up this {metric.label.lower()}?", "explain", {}))
        child = model.child_dimension(current) if current else None
        if child:
            out.append((f"What sits below {model.label_of(current)}?",
                        "drill_down", {"dimension": child}))
        if ctx.comparison:
            out.append(("Which children explain the movement?",
                        "change_contribution", {}))
        elif model.comparisons:
            first = model.comparisons[0]
            out.append((f"How does this compare with "
                        f"{first.label_for(ctx.time.grain)}?",
                        "compare", {"period": first.name}))
        out.append(("Is anything unusual against its own history?",
                    "exceptions", {}))
        alts = model.alternative_dimensions(ctx, exclude=(child,) if child else ())
        if alts:
            out.append((f"Which alternative dimension best explains this - "
                        f"{model.label_of(alts[0])}?",
                        "decompose", {"dimension": alts[0]}))
    n = 12 if ctx.time.grain in ("day", "week", "month") else 8
    out.append((f"How has this changed over the last {n} {ctx.time.grain}s?",
                "trend", {"periods": n}))
    if not on_time:
        out.append(("What is underneath this aggregate?", "distribution", {}))
    others = [m for m in model.metrics if m != ctx.metric]
    if others:
        out.append((f"What else is true of this population - "
                    f"{model.metric(others[0]).label}?",
                    "switch_metric", {"metric": others[0]}))
    return out
