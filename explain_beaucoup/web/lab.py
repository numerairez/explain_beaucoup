"""The lab behind the "How it works" page.

It walks one gesture through the framework stage by stage - Operation, verb,
validation, result kind, engine, backend query, view, placement - and reports
what each stage did. Nothing here is a mock: it calls the same functions the
session calls, against the session's own model and engine. It never touches
the session's graph or journal, so a developer can try anything - including
moves the model refuses - without disturbing the investigation.

Contexts travel as plain dicts (`Context.to_dict` / `from_dict`). The page
holds them as values, which is the point it is making: going back is just
holding on to the old one.
"""

from __future__ import annotations

import inspect
import json
import time as _time
from typing import Any

from ..core.context import Context
from ..core.operations import (VERBS, Operation, OperationError,
                               result_kind)
from ..core.session import Session, landing_kind, lands
from ..engine import explain as explain_mod
from ..engine.backend import PERIOD
from ..engine.engine import ANALYSES, normal_kind, title_for
from ..semantic.specs import SemanticError
from ..view import vega
from ..view.theme import THEMES

ROWS_SHOWN = 12


def _ctx_out(ctx: Context) -> dict[str, Any]:
    d = ctx.to_dict()
    d["id"] = ctx.id
    d["label"] = str(ctx)
    # Exactly what the fingerprint hashes: the analytical state, no lineage.
    d["identity"] = json.dumps(ctx.to_dict(with_lineage=False), sort_keys=True)
    return d


def _doc(obj: Any) -> str:
    """The object's own docstring - not one inherited from a base class."""
    own = obj.__dict__.get("__doc__") if isinstance(obj, type) else obj.__doc__
    return inspect.cleandoc(own) if own else ""


def _diff(before: Context, after: Context) -> list[dict[str, Any]]:
    a, b = before.to_dict(with_lineage=False), after.to_dict(with_lineage=False)
    return [{"field": k, "before": a[k], "after": b[k]}
            for k in a if a[k] != b[k]]


def _capabilities(session: Session, ctx: Context) -> list[dict[str, Any]]:
    return [{"verb": c.verb, "label": c.label, "params": c.param_map,
             "group": c.group, "hint": c.hint,
             "member_scoped": bool(VERBS.get(c.verb)
                                   and VERBS[c.verb].member_scoped),
             "lands": lands(c.verb)}
            for c in session.model.capabilities(ctx)]


def _reading(session: Session, ctx: Context, kind: str, theme: str,
             selected: str | None) -> dict[str, Any]:
    """Run a node through the engine and the view registry."""
    engine, model = session.engine, session.model
    kind = normal_kind(model, ctx, kind)
    hit = f"{ctx.fingerprint}:{kind}" in engine._cache
    t0 = _time.perf_counter()
    res = engine.execute(ctx, kind)
    ms = round((_time.perf_counter() - t0) * 1000, 2)
    metric = model.metric(ctx.metric)
    q = engine.query(ctx, ctx.time, metric, res.axes)
    view = vega.VIEWS.get(res.kind) or vega.VIEWS["breakdown"]
    spec = vega.spec_for(model, res, THEMES[theme], selected=selected)
    return {
        "kind": res.kind,
        "analysis": {"kind": res.kind, "title": title_for(model, ctx, res.kind),
                     "registered": res.kind in ANALYSES},
        "cache": {"hit": hit, "key": f"{ctx.fingerprint}:{kind}",
                  "size": engine.cache_size(), "ms": ms},
        "query": {
            "backend": type(engine.backend).__name__,
            "components": list(q.components),
            "filters": [list(f) for f in q.filters],
            "window": q.window.to_dict(),
            "group_by": ["period" if g == PERIOD else g for g in q.group_by],
            "ratio": list(q.ratio) if q.ratio else None,
            "sql": res.provenance,
        },
        "result": {
            "total": res.total, "prior_total": res.prior_total,
            "delta_total": res.delta_total, "fact_rows": res.fact_rows,
            "row_count": len(res.rows), "notes": list(res.notes),
            "axes": list(res.axes),
            "rows": [{"key": r.key, "label": r.label, "value": r.value,
                      "prior": r.prior, "delta": r.delta, "share": r.share}
                     for r in res.rows[:ROWS_SHOWN]],
            "value_txt": vega.fmt.value(metric, res.total, short=True),
        },
        "view": {"renderer": view.renderer, "builder": view.build.__name__,
                 "spec": spec},
        "marks": [r.key for r in res.rows if r.value is not None][:40],
    }


def read(session: Session, ctx_dict: dict[str, Any], kind: str = "breakdown",
         theme: str = "light", selected: str | None = None) -> dict[str, Any]:
    """Load any Context into the lab: how it reads, and what it offers."""
    ctx = Context.from_dict(ctx_dict)
    return {"context": _ctx_out(ctx),
            "reading": _reading(session, ctx, kind, theme, selected),
            "capabilities": _capabilities(session, ctx)}


def start(session: Session, theme: str = "light") -> dict[str, Any]:
    """Where a session starts: the root Context and how it reads."""
    root = session.graph.nodes[session.graph.roots[0]].context
    return read(session, root.to_dict(), "breakdown", theme)


def step(session: Session, ctx_dict: dict[str, Any], verb: str,
         params: dict[str, Any], member: str | None, kind: str,
         theme: str = "light") -> dict[str, Any]:
    """One gesture, every stage. Stops at the stage that refuses it."""
    model = session.model
    before = Context.from_dict(ctx_dict)
    p = dict(params or {})
    v = VERBS.get(verb)
    stages: list[dict[str, Any]] = []
    out: dict[str, Any] = {"before": _ctx_out(before), "stages": stages,
                           "ok": False}

    if v is not None and member is not None and v.member_scoped:
        p["member"] = member
    op = Operation.of(verb, "lab", **p)
    stages.append({"stage": "operation", "ok": True,
                   "detail": {"verb": op.verb, "params": op.param_map,
                              "source": op.source,
                              "member_dropped": bool(
                                  member is not None and v is not None
                                  and not v.member_scoped)}})
    if v is None:
        stages.append({"stage": "verb", "ok": False,
                       "error": f"Unknown analytical verb '{verb}'"})
        return out
    stages.append({"stage": "verb", "ok": True, "detail": {
        "name": v.name, "class": type(v).__name__, "doc": _doc(type(v)),
        "member_scoped": v.member_scoped, "placement": v.placement,
        "preserves_kind": v.preserves_kind,
        "carries_change": v.carries_change,
        "hierarchy_scoped": v.hierarchy_scoped}})

    try:
        after = v.apply(model, before, op.param_map, op)
    except (SemanticError, OperationError) as exc:
        stages.append({"stage": "apply", "ok": False, "error": str(exc)})
        return out
    stages.append({"stage": "apply", "ok": True, "detail": {
        "diff": _diff(before, after),
        "lineage": after.lineage[-1].to_dict() if after.lineage else None,
        "same_id": after.id == before.id}})

    try:
        model.validate(after)
    except SemanticError as exc:
        stages.append({"stage": "validate", "ok": False, "error": str(exc)})
        return out
    stages.append({"stage": "validate", "ok": True, "detail": {}})

    k = landing_kind(model, kind, after, op)
    preserved = k != result_kind(model, after, op) and k == kind
    stages.append({"stage": "kind", "ok": True,
                   "detail": {"kind": k, "preserved": preserved}})

    selected = member if (member is not None and len(after.grain) ==
                          len(before.grain)) else None
    try:
        reading = _reading(session, after, k, theme, selected)
    except SemanticError as exc:
        stages.append({"stage": "engine", "ok": False, "error": str(exc)})
        return out
    stages.append({"stage": "engine", "ok": True, "detail": {
        "cache": reading["cache"], "analysis": reading["analysis"]}})
    stages.append({"stage": "backend", "ok": True,
                   "detail": reading["query"]})
    stages.append({"stage": "view", "ok": True, "detail": {
        "renderer": reading["view"]["renderer"],
        "builder": reading["view"]["builder"]}})
    stages.append({"stage": "place", "ok": True,
                   "detail": {"lands": lands(verb)}})
    out.update(ok=True, after=_ctx_out(after), reading=reading,
               capabilities=_capabilities(session, after))
    return out


def explain(session: Session, ctx_dict: dict[str, Any]) -> dict[str, Any]:
    ctx = Context.from_dict(ctx_dict)
    try:
        exp = explain_mod.explain(session.engine, session.model, ctx)
    except SemanticError as exc:
        return {"ok": False, "error": str(exc)}
    metric = session.model.metric(ctx.metric)
    return {
        "ok": True,
        "comparison": exp.comparison,
        "total": vega.fmt.value(metric, exp.total, short=True),
        "delta": (vega.fmt.signed(metric, exp.delta)
                  if exp.delta is not None else None),
        "delta_pct": exp.delta_pct,
        "dimensions_tested": [session.model.label_of(d)
                              for d in exp.dimensions_tested],
        "best_dimension": session.model.label_of(exp.best_dimension)
                          if exp.best_dimension else None,
        "queries": len(exp.provenance),
        "evidence": [{
            "kind": e.kind, "headline": e.headline, "detail": e.detail,
            "score": e.score, "weight": explain_mod.WEIGHT.get(e.kind),
            "dimension": session.model.label_of(e.dimension)
                         if e.dimension else None,
            "target": _ctx_out(e.target) if e.target is not None else None,
            "target_kind": e.target_kind,
        } for e in exp.evidence[:10]],
    }


def registry(session: Session) -> dict[str, Any]:
    """Every extension point, as currently populated."""
    model = session.model
    return {
        "verbs": [{"name": v.name, "class": type(v).__name__,
                   "module": type(v).__module__, "doc": _doc(type(v)),
                   "member_scoped": v.member_scoped, "placement": v.placement,
                   "preserves_kind": v.preserves_kind,
                   "carries_change": v.carries_change,
                   "hierarchy_scoped": v.hierarchy_scoped,
                   "advertises": type(v).advertise is not
                   type(v).__mro__[-2].advertise}
                  for v in VERBS.values()],
        "analyses": [{"kind": a.kind, "compute": a.compute.__name__,
                      "doc": _doc(a.compute).split("\n\n")[0]}
                     for a in ANALYSES.values()],
        "views": [{"kind": k, "renderer": v.renderer,
                   "builder": v.build.__name__}
                  for k, v in vega.VIEWS.items()],
        "signals": [{"name": s.name, "per": s.per,
                     "emits": [{"kind": k, "weight": explain_mod.WEIGHT.get(k)}
                               for k in s.emits]}
                    for s in explain_mod.SIGNALS.values()],
        "weights": explain_mod.WEIGHT,
        "backend": {"class": type(session.engine.backend).__name__,
                    "doc": _doc(type(session.engine.backend)).split("\n\n")[0],
                    "native_grain": session.engine.native_grain},
        "model": {
            "dataset": model.dataset, "title": model.title,
            "time_column": model.time_column, "grains": list(model.grains),
            "dimensions": [{"name": n, "label": d.label, "column": d.column,
                            "hierarchy": d.hierarchy,
                            "members": model.member_counts.get(n)}
                           for n, d in model.dimensions.items()],
            "hierarchies": [{"name": h.name, "label": h.label,
                             "levels": list(h.levels),
                             "labels": [model.label_of(l) for l in h.levels]}
                            for h in model.hierarchies.values()],
            "metrics": [{"name": n, "label": m.label, "kind": m.kind,
                         "rule": m.aggregation,
                         "invalid_grains": sorted(m.invalid_grains)}
                        for n, m in model.metrics.items()],
            "comparisons": [{"name": c.name,
                             "label": c.label_for(model.time_grain)}
                            for c in model.comparisons],
        },
    }
