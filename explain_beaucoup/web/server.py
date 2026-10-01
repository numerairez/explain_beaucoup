"""The local API the React workspace talks to.

One process, one analyst. Sessions live in memory, keyed by id, so a reload
of the page picks the investigation back up for as long as the server runs.

Every move goes through `POST /api/sessions/{id}/move` and answers with the
whole workspace snapshot: the client never patches state of its own, it
renders what the session says - the same contract the Qt window keeps.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..core import timegrain as tg
from ..core.session import Landing, MenuItem, Session
from ..data.source import DataSourceError
from ..engine.commentary import TIMELINE, TREE
from ..engine.explain import ExplanationResult
from ..engine.narrate import narrate_investigation
from ..semantic import catalog
from ..semantic.catalog import CatalogEntry, Scope, ScopeError
from ..semantic.loader import LoadedModel, ModelError, load_model_file
from ..semantic.specs import SemanticError
from ..view.theme import LIGHT
from . import state

STATIC = Path(__file__).parent / "static"


class Workspace:
    """A session plus what the page needs to say about how it was opened."""

    def __init__(self, loaded: LoadedModel) -> None:
        self.session = Session(loaded.source, loaded.model)
        self.scope_label = loaded.scope_label
        self.title = loaded.model.title or ""


class ScopeIn(BaseModel):
    filters: dict[str, list[str]] = Field(default_factory=dict)
    start: str | None = None
    end: str | None = None
    # Dimensions whose member lists the picker wants, each read under every
    # *other* filter - that is what makes the lists cross-filter.
    dims: list[str] = Field(default_factory=list)

    def scope(self, *, excluding: str | None = None) -> Scope:
        return Scope.of({d: m for d, m in self.filters.items()
                         if d != excluding}, start=self.start, end=self.end)


class OpenIn(ScopeIn):
    entry: int


class Move(BaseModel):
    """One gesture. `type` picks the session method; the rest are its args."""

    type: Literal["run", "activate", "select", "goto", "raise", "close",
                  "back", "pin", "branch", "lens", "metric", "period",
                  "time_grain", "comparison", "explain", "evidence",
                  "invoke", "hide_beat", "restore_beat"]
    uid: str | None = None
    verb: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    member: str | None = None
    key: str | None = None
    value: str | None = None
    index: int | None = None
    item: dict[str, Any] | None = None
    source: str = "web"


def create_app(models_dir: Path, *, preload: LoadedModel | None = None) -> FastAPI:
    app = FastAPI(title="Explain Beaucoup", docs_url="/api/docs")
    workspaces: dict[str, Workspace] = {}
    loaded_cache: dict[Path, LoadedModel] = {}
    entries: list[CatalogEntry] = catalog.discover(models_dir)
    boot: dict[str, str | None] = {"session": None}

    if preload is not None:
        sid = uuid.uuid4().hex[:12]
        workspaces[sid] = Workspace(preload)
        boot["session"] = sid

    def workspace(sid: str) -> Workspace:
        ws = workspaces.get(sid)
        if ws is None:
            raise HTTPException(404, "That session has ended. Open a dataset "
                                     "again.")
        return ws

    def entry(i: int) -> CatalogEntry:
        if not 0 <= i < len(entries):
            raise HTTPException(404, "No such dataset")
        return entries[i]

    def load(e: CatalogEntry) -> LoadedModel:
        if e.path not in loaded_cache:
            if not e.ok:
                raise HTTPException(422, f"{e.path.name} — {e.problem}")
            try:
                loaded_cache[e.path] = load_model_file(e.path)
            except (ModelError, DataSourceError) as exc:
                raise HTTPException(422, f"{e.path.name} could not be "
                                         f"opened: {exc}") from exc
        return loaded_cache[e.path]

    def snap(sid: str, *, theme: str, layout: str,
             scroll_to: str | None = None) -> dict[str, Any]:
        ws = workspace(sid)
        out = state.snapshot(ws.session, theme=theme, layout=layout,
                             scroll_to=scroll_to, title=ws.title,
                             scope_label=ws.scope_label)
        out["session"] = sid
        return out

    # -- boot and catalogue ------------------------------------------------

    @app.get("/api/boot")
    def get_boot() -> dict[str, Any]:
        return {"session": boot["session"], "models_dir": str(models_dir)}

    @app.get("/api/catalog")
    def get_catalog() -> dict[str, Any]:
        return {
            "models_dir": str(models_dir),
            "entries": [{"index": i, "name": e.name, "path": str(e.path),
                         "summary": e.summary(), "ok": e.ok,
                         "problem": e.problem,
                         "metrics": [label for _, label in e.metrics],
                         "dimensions": [label for _, label in e.dimensions]}
                        for i, e in enumerate(entries)],
        }

    @app.get("/api/catalog/{i}")
    def get_entry(i: int) -> dict[str, Any]:
        e = entry(i)
        loaded = load(e)
        model, source = loaded.model, loaded.source
        grain = model.time_grain
        periods = source.periods(grain)
        return {
            "index": i,
            "title": model.title or model.dataset,
            "path": str(e.path),
            "rows": source.row_count,
            "grain": grain,
            "grains": list(model.grains),
            "periods": [{"value": k, "label": tg.label_of(k, grain)}
                        for k in periods],
            "metrics": [label for _, label in e.metrics],
            "dimensions": [label for _, label in e.dimensions],
            "hierarchies": [{"label": label, "levels": list(levels)}
                            for label, levels in e.hierarchies],
            # Worth filtering on: something to choose between in the data.
            "filterable": [{"value": n, "label": d.label}
                           for n, d in model.dimensions.items()
                           if model.member_counts.get(n, 0) > 1],
        }

    @app.post("/api/catalog/{i}/preview")
    def post_preview(i: int, body: ScopeIn) -> dict[str, Any]:
        loaded = load(entry(i))
        model = loaded.model
        p = catalog.preview(loaded, body.scope())
        members: dict[str, list[str]] = {}
        for dim in body.dims:
            if dim not in model.dimensions:
                continue
            frame = catalog.frame_under(loaded, body.scope(excluding=dim))
            members[dim] = catalog.members_of(frame, model.dim(dim).column)
        span = ([tg.label_of(p.span[0], model.time_grain),
                 tg.label_of(p.span[1], model.time_grain)]
                if p.span else None)
        return {"rows": p.rows, "total_rows": p.total_rows,
                "periods": p.periods, "grain": model.time_grain, "span": span,
                "breakdowns": len(p.breakdowns), "problems": list(p.problems),
                "notes": list(p.notes), "ok": p.ok, "members": members,
                "label": body.scope().describe(model)}

    @app.post("/api/sessions")
    def post_session(body: OpenIn) -> dict[str, str]:
        loaded = load(entry(body.entry))
        try:
            scoped = catalog.apply_scope(loaded, body.scope())
        except ScopeError as exc:
            raise HTTPException(422, str(exc)) from exc
        sid = uuid.uuid4().hex[:12]
        workspaces[sid] = Workspace(scoped)
        return {"session": sid}

    # -- the workspace -----------------------------------------------------

    @app.get("/api/sessions/{sid}")
    def get_session(sid: str, theme: str = "light",
                    layout: str = TREE) -> dict[str, Any]:
        return snap(sid, theme=_theme(theme), layout=_layout(layout))

    @app.post("/api/sessions/{sid}/menu")
    def post_menu(sid: str, body: Move) -> dict[str, Any]:
        s = workspace(sid).session
        uid = body.uid if body.uid in s.graph.nodes else s.graph.current
        menu = s.menu(uid, body.key)
        return {"uid": uid, "heading": menu.heading, "note": menu.note,
                "groups": [[asdict(it) for it in g] for g in menu.groups]}

    @app.post("/api/sessions/{sid}/move")
    def post_move(sid: str, body: Move, theme: str = "light",
                  layout: str = TREE) -> dict[str, Any]:
        s = workspace(sid).session
        message, tone, explained = "", "info", False
        try:
            out = _dispatch(s, body)
        except SemanticError as exc:
            out, message, tone = None, str(exc), "blocked"
        except KeyError as exc:
            raise HTTPException(404, f"No such chart: {exc}") from exc
        scroll_to = None
        if isinstance(out, Landing):
            message, scroll_to = out.message, out.node.uid
        elif isinstance(out, ExplanationResult):
            explained = True
            message = (f"Explained — {len(out.evidence)} pieces of evidence "
                       f"from {len(out.provenance)} governed queries")
        elif isinstance(out, str):
            message = out
        return {"message": message, "tone": tone, "explained": explained,
                "state": snap(sid, theme=_theme(theme), layout=_layout(layout),
                              scroll_to=scroll_to)}

    @app.get("/api/sessions/{sid}/commentary", response_class=PlainTextResponse)
    def get_commentary(sid: str, fmt: str = "markdown",
                       layout: str = TREE) -> str:
        c = workspace(sid).session.commentary
        lay = _layout(layout)
        if fmt == "json":
            return c.json()
        if fmt == "plain":
            return c.plain(layout=lay)
        if fmt == "html":
            return c.html(LIGHT, live=set(), layout=lay)
        return c.markdown(live=set(), layout=lay)

    @app.get("/api/sessions/{sid}/export", response_class=PlainTextResponse)
    def get_export(sid: str) -> str:
        s = workspace(sid).session
        return (narrate_investigation(s.model, s.engine, s.graph) + "\n\n"
                + s.commentary.markdown(live=set(), heading="## Commentary",
                                        layout=TREE) + "\n")

    # -- the built client --------------------------------------------------

    if (STATIC / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=STATIC / "assets"),
                  name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def client(path: str) -> FileResponse:
            f = STATIC / path
            if path and f.is_file() and STATIC in f.resolve().parents:
                return FileResponse(f)
            return FileResponse(STATIC / "index.html")

    return app


def _dispatch(s: Session, m: Move) -> Any:
    t = m.type
    if t == "run":
        return s.run_from(m.uid, m.verb, m.params, member=m.member,
                          source=m.source)
    if t == "activate":
        return s.activate(m.uid or s.graph.current or "", str(m.key))
    if t == "select":
        s.select(m.uid or s.graph.current or "", m.key)
        return s.mark_summary(m.uid or "", m.key) if m.key else None
    if t == "goto":
        return Landing(s.goto(str(m.uid)), True, False)
    if t == "raise":
        return s.raise_node(str(m.uid))
    if t == "close":
        return s.close(str(m.uid))
    if t == "back":
        return s.back() or "Already at the root of this path"
    if t == "pin":
        return s.toggle_pin(m.uid)
    if t == "branch":
        return s.branch(str(m.value))
    if t == "lens":
        return s.lens(str(m.value))
    if t == "metric":
        return s.set_metric(str(m.value))
    if t == "period":
        return s.set_period(str(m.value))
    if t == "time_grain":
        return s.set_time_grain(str(m.value))
    if t == "comparison":
        return s.set_comparison(m.value)
    if t == "explain":
        if m.uid and m.uid != s.graph.current:
            s.raise_node(m.uid)
        return s.explain(member=m.member)
    if t == "evidence":
        exp = s.last_explanation
        if exp is None or m.index is None:
            return None
        shown = exp.top(state.EVIDENCE_SHOWN)
        return s.open_evidence(shown[m.index]) if m.index < len(shown) else None
    if t == "invoke":
        return s.invoke(str(m.uid), MenuItem(**(m.item or {})))
    if t == "hide_beat":
        s.commentary.hide(int(m.index or 0))
        return None
    if t == "restore_beat":
        s.commentary.restore(m.index)
        return None
    raise HTTPException(400, f"Unknown move: {t}")       # pragma: no cover


def _theme(name: str) -> str:
    return "dark" if name == "dark" else "light"


def _layout(name: str) -> str:
    return TIMELINE if name == TIMELINE else TREE
