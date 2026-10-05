"""The dashboard a dataset opens on, read out for the web client.

A board is a model opened under a scope, its canned dashboard, and the
investigations dug out of its tiles - one per tile, resumed rather than
restarted. Every number on a tile comes from the same engine and explain
registry the workspace uses; this module only replays tiles and says what
they read.
"""

from __future__ import annotations

from typing import Any, Callable

from ..core import dashboard as dash
from ..core.dashboard import Dashboard, DashboardError, Tile
from ..core.session import Session, root_context
from ..engine.engine import AnalyticalEngine
from ..semantic.loader import LoadedModel
from ..semantic.specs import SemanticError
from ..view import format as fmt
from ..view.theme import THEMES
from ..view.vega import renderer_for, spec_for, titles_for
from .state import KIND_LABEL, _role


class Board:
    def __init__(self, bid: str, loaded: LoadedModel) -> None:
        self.id = bid
        self.loaded = loaded
        self.path = dash.path_for(loaded.path)
        self.engine = AnalyticalEngine(loaded.source, loaded.model)
        self.root, _ = root_context(loaded.model, self.engine)
        self.error = ""
        self.dashboard = self._read()
        # tile id -> session id; the investigation each tile has opened.
        self.sessions: dict[str, str] = {}
        # The session opened without a tile ("Explore freely").
        self.free: str | None = None

    @property
    def model(self):
        return self.loaded.model

    def _read(self) -> Dashboard:
        try:
            return dash.load(self.path) or Dashboard()
        except DashboardError as exc:
            self.error = str(exc)
            return Dashboard()

    def resolve(self, tile: Tile) -> dash.Resolved:
        return dash.resolve(self.model, self.engine, tile, self.root)

    def open_session(self, tile: Tile) -> Session:
        r = self.resolve(tile)
        return Session(self.loaded.source, self.model, root=r.context,
                       kind=r.kind, title=tile.title)

    # -- editing -----------------------------------------------------------
    # The file is re-read before every write, so two boards on the same
    # model (two scopes, say) never overwrite each other's tiles.

    def add(self, session: Session, uid: str, title: str = "") -> Tile:
        if self.error:
            raise DashboardError(f"Fix {self.path.name} first: {self.error}")
        node = session.graph.get(uid)
        board = self._read()
        for t in board.tiles:
            try:
                r = self.resolve(t)
            except SemanticError:
                continue
            if r.context.id == node.context.id and r.kind == node.kind:
                raise DashboardError(
                    f"Already on the dashboard as “{t.title}”.")
        if not title:
            # As the stack titles a child: the scope leads, since two tiles
            # of the same split differ by it.
            scope = node.context.scope_label() if node.context.filters else ""
            title = f"{scope} — {node.title}" if scope else node.title
        tile = dash.tile_from(self.model, self.engine, self.root, node.context,
                              node.kind, title, {t.id for t in board.tiles})
        if not board.title:
            board.title = self.model.title or self.model.dataset
        board.tiles.append(tile)
        dash.save(board, self.path)
        self.dashboard = board
        return tile

    def remove(self, tile_id: str) -> None:
        board = self._read()
        board.tiles = [t for t in board.tiles if t.id != tile_id]
        dash.save(board, self.path)
        self.dashboard = board
        self.sessions.pop(tile_id, None)


def payload(board: Board, theme: str,
            session_of: Callable[[str], Session | None]) -> dict[str, Any]:
    """Everything the dashboard page shows, in one payload."""
    model = board.model
    free = board.free if board.free and session_of(board.free) else None
    return {
        "board": board.id,
        "title": board.dashboard.title or model.title or model.dataset,
        "dataset": model.title or model.dataset,
        "scope_label": board.loaded.scope_label,
        "file": board.path.name,
        "error": board.error,
        "period": board.root.time.label,
        "free": free,
        "tiles": [tile(board, t, theme, session_of)
                  for t in board.dashboard.tiles],
    }


def tile(board: Board, t: Tile, theme: str,
         session_of: Callable[[str], Session | None]) -> dict[str, Any]:
    model, engine = board.model, board.engine
    out: dict[str, Any] = {"id": t.id, "title": t.title, "size": t.size,
                           "view": t.view, "steps": [s.verb for s in t.steps],
                           "investigation": investigation(
                               board.sessions.get(t.id), session_of)}
    try:
        r = board.resolve(t)
        res = engine.execute(r.context, r.kind)
        reading = dash.read(model, engine, r)
    except SemanticError as exc:
        # Said on the tile, by name: under this scope, or against today's
        # data, the model refuses one of its steps.
        out["error"] = str(exc)
        return out

    metric = model.metric(r.context.metric)
    h = reading.headline
    headline = None
    if h is not None:
        delta = None
        if h.delta is not None:
            good = (h.delta >= 0) == metric.higher_is_better
            delta = {"text": fmt.signed(metric, h.delta), "pct": h.delta_pct,
                     "up": h.delta >= 0, "tone": "good" if good else "critical",
                     "comparison": (h.comparison or "").upper()}
        headline = {"value": fmt.value(metric, h.total, short=True),
                    "metric": metric.label, "period": h.context.time.label,
                    "delta": delta}
    why = reading.why
    head, sub = titles_for(model, res)
    spec = None
    if t.view != dash.KPI:
        spec = spec_for(model, res, THEMES[theme], compact=True)
        spec.pop("title", None)
    out.update({
        "kind": r.kind,
        "context": str(r.context),
        "subtitle": sub or head,
        "headline": headline,
        "why": None if why is None else {
            "headline": why.headline, "label": KIND_LABEL.get(
                why.kind, why.kind.title()), "role": _role(why)},
        "exceptions": reading.exceptions,
        "renderer": renderer_for(res.kind),
        "spec": spec,
        "empty": res.is_empty,
    })
    return out


def investigation(sid: str | None,
                  session_of: Callable[[str], Session | None]
                  ) -> dict[str, Any] | None:
    """What digging into a tile has turned up so far: how far it went, and
    the findings pinned along the way - they flow back onto the tile."""
    s = session_of(sid) if sid else None
    if s is None:
        return None
    pins = []
    for n in s.graph.pins:
        metric = s.model.metric(n.context.metric)
        try:
            value = fmt.value(metric, s.result(n).total, short=True)
        except SemanticError:
            value = "-"
        pins.append({"uid": n.uid, "title": n.title, "note": n.note,
                     "value": value})
    root = s.graph.roots[0] if s.graph.roots else None
    return {"session": sid, "views": len(s.graph.nodes),
            "pins": pins,
            "at": s.node.title if s.graph.current != root else None}
