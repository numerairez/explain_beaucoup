"""Canned dashboards: saved starting points for an investigation.

A tile is not a chart. It is a short list of verbs, replayed from the model's
root through `apply()` every time the dashboard is read - so a tile the model
would refuse fails `check` rather than at render time, the investigation it
opens already carries the lineage that got it there, and nothing on it is
frozen to a period: the root is always the latest one, so the dashboard rolls
forward with the data.

Dashboards live beside their model, in `<model>.dashboard.yaml`:

    title: Sales overview
    tiles:
      - title: Revenue this month
        view: kpi
        steps: [drill_up, {compare: {period: mom}}]
      - title: What moved revenue
        size: wide
        steps: [{compare: {period: mom}}, change_contribution]

A step is a verb name, or a one-key mapping of a verb to its params. `view`
is optional: `kpi` draws the headline number alone, any result family
(`breakdown`, `change_contribution`, ...) overrides the one the steps imply.

"Dig deeper" is just `Session(root=tile.context)`. Everything after that is
the ordinary investigation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from ..engine.commentary import NOTABLE_Z
from ..engine.engine import ANALYSES, AnalyticalEngine, normal_kind
from ..engine.explain import Evidence, ExplanationResult, explain
from ..semantic.catalog import DASHBOARD_SUFFIX as SUFFIX
from ..semantic.specs import SemanticError, SemanticModel
from .context import Context, LineageStep, TimeWindow
from .operations import (BREAKDOWN, EXCEPTIONS, VERBS, Operation,
                         OperationError, aggregate_view, apply, on_time)

KPI = "kpi"
SIZES = ("normal", "wide")


class DashboardError(Exception):
    """A dashboard file, or a tile, that cannot be used - with the reason."""


@dataclass(frozen=True)
class Step:
    verb: str
    params: tuple[tuple[str, Any], ...] = ()

    @property
    def param_map(self) -> dict[str, Any]:
        return dict(self.params)

    def to_yaml(self) -> Any:
        return {self.verb: self.param_map} if self.params else self.verb


@dataclass(frozen=True)
class Tile:
    id: str
    title: str
    steps: tuple[Step, ...] = ()
    view: str | None = None          # "kpi", a result family, or None
    size: str = "normal"

    def to_yaml(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "title": self.title}
        if self.view:
            out["view"] = self.view
        if self.size != "normal":
            out["size"] = self.size
        out["steps"] = [s.to_yaml() for s in self.steps]
        return out


@dataclass
class Dashboard:
    title: str = ""
    tiles: list[Tile] = field(default_factory=list)

    def tile(self, tile_id: str) -> Tile:
        for t in self.tiles:
            if t.id == tile_id:
                return t
        raise KeyError(tile_id)

    def to_yaml(self) -> dict[str, Any]:
        return {"title": self.title,
                "tiles": [t.to_yaml() for t in self.tiles]}


# --------------------------------------------------------------------------
# files
# --------------------------------------------------------------------------

def path_for(model_path: str | Path) -> Path:
    """The dashboard file that belongs to a model file."""
    p = Path(model_path)
    return p.with_name(p.stem + SUFFIX)


def is_dashboard_file(path: Path) -> bool:
    return path.name.endswith(SUFFIX)


def load(path: str | Path) -> Dashboard | None:
    """The dashboard in `path`, or None when there is no such file."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        doc = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as exc:
        raise DashboardError(f"{p.name} is not valid YAML: {exc}") from None
    return parse(doc, where=p.name)


def save(dashboard: Dashboard, path: str | Path) -> None:
    Path(path).write_text(
        "# A canned dashboard: each tile is a list of verbs replayed from the\n"
        "# model's root. See explain_beaucoup/core/dashboard.py.\n"
        + yaml.safe_dump(dashboard.to_yaml(), sort_keys=False,
                         allow_unicode=True))


def parse(doc: Any, *, where: str = "dashboard") -> Dashboard:
    if not isinstance(doc, dict):
        raise DashboardError(f"{where}: must be a YAML mapping at the top level")
    raw_tiles = doc.get("tiles") or []
    if not isinstance(raw_tiles, list):
        raise DashboardError(f"{where}: tiles must be a list")
    tiles: list[Tile] = []
    taken: set[str] = set()
    for i, raw in enumerate(raw_tiles):
        at = f"{where}: tiles[{i}]"
        if not isinstance(raw, dict):
            raise DashboardError(f"{at} must be a mapping")
        title = str(raw.get("title") or f"Tile {i + 1}")
        size = str(raw.get("size") or "normal")
        if size not in SIZES:
            raise DashboardError(f"{at}.size: '{size}' is not one of "
                                 f"{', '.join(SIZES)}")
        view = raw.get("view")
        if view is not None and view != KPI and view not in ANALYSES:
            raise DashboardError(
                f"{at}.view: '{view}' is not '{KPI}' or a result family "
                f"({', '.join(ANALYSES)})")
        tile_id = unique_id(str(raw.get("id") or slug(title)), taken)
        tiles.append(Tile(tile_id, title, parse_steps(raw.get("steps"), at),
                          view, size))
    return Dashboard(str(doc.get("title") or ""), tiles)


def parse_steps(raw: Any, where: str) -> tuple[Step, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise DashboardError(f"{where}.steps must be a list")
    out: list[Step] = []
    for j, s in enumerate(raw):
        at = f"{where}.steps[{j}]"
        if isinstance(s, str):
            verb, params = s, {}
        elif isinstance(s, dict) and len(s) == 1:
            verb, params = next(iter(s.items()))
            params = params or {}
            if not isinstance(params, dict):
                raise DashboardError(f"{at}: the params of '{verb}' must be "
                                     f"a mapping")
        else:
            raise DashboardError(f"{at}: a step is a verb name, or one verb "
                                 f"mapped to its params")
        if verb not in VERBS:
            raise DashboardError(f"{at}: unknown verb '{verb}'")
        out.append(Step(str(verb), tuple(sorted(params.items()))))
    return tuple(out)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "tile"


def unique_id(base: str, taken: set[str]) -> str:
    out, n = base, 2
    while out in taken:
        out, n = f"{base}-{n}", n + 1
    taken.add(out)
    return out


# --------------------------------------------------------------------------
# replay
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Resolved:
    """A tile replayed against today's data: the Context it starts an
    investigation from, and the result family it is read as."""
    tile: Tile
    context: Context
    kind: str


def resolve(model: SemanticModel, engine: AnalyticalEngine, tile: Tile,
            root: Context) -> Resolved:
    """Replay a tile's steps from `root`, exactly as the session would run
    them. Raises SemanticError, naming the step, if the model refuses one."""
    from .session import landing_kind
    ctx, kind = root, BREAKDOWN
    for i, step in enumerate(tile.steps):
        p = step.param_map
        if step.verb == "set_grain" and "end" not in p:
            # As in `Session.run`: only the data knows where it stops.
            p["end"] = engine.latest(p.get("grain", ctx.time.grain))
        op = Operation.of(step.verb, "dashboard", **p)
        try:
            new = apply(model, ctx, op)
        except (SemanticError, OperationError) as exc:
            raise SemanticError(f"step {i + 1} ({step.verb}): {exc}") from exc
        ctx, kind = new, landing_kind(model, kind, new, op)
    if tile.view and tile.view != KPI:
        kind = tile.view
    return Resolved(tile, ctx, normal_kind(model, ctx, kind))


def steps_from(ctx: Context) -> tuple[Step, ...]:
    """A node's lineage, as steps a dashboard can replay.

    Refuses lineage that is not made of registered verbs - a view opened from
    explain evidence, say - by name, rather than saving a tile that would
    replay to something else.
    """
    out: list[Step] = []
    for s in ctx.lineage:
        if s.verb not in VERBS:
            raise DashboardError(
                f"'{s.summary}' was not a move a dashboard can replay. Add "
                f"the chart you reached it from instead.")
        params = tuple((k, v) for k, v in s.params
                       if not (s.verb == "set_grain" and k == "end"))
        out.append(Step(s.verb, params))
    return tuple(out)


def tile_from(model: SemanticModel, engine: AnalyticalEngine, root: Context,
              ctx: Context, kind: str, title: str, taken: set[str]) -> Tile:
    """A tile that replays to `ctx`, read as `kind`. Proven by replaying it.

    The chart's own lineage is tried first, since it is how the analyst got
    there. A view opened from explain evidence has steps the grammar does
    not replay, so it is rebuilt from what it *is* instead - see
    `steps_to`."""
    tile_id = unique_id(slug(title), taken)
    tried: list[str] = []
    for build in (steps_from, lambda c: steps_to(model, engine, root, c)):
        try:
            tile = Tile(tile_id, title, build(ctx))
            replayed = resolve(model, engine, tile, root)
        except (DashboardError, SemanticError) as exc:
            tried.append(str(exc))
            continue
        if replayed.context.id != ctx.id:
            tried.append("it replays to a different view")
            continue
        return replace(tile, view=kind) if replayed.kind != kind else tile
    raise DashboardError(
        "This chart cannot be rebuilt from the model's grammar, so it cannot "
        f"be kept on a dashboard ({tried[-1]}).")


def steps_to(model: SemanticModel, engine: AnalyticalEngine, root: Context,
             ctx: Context) -> tuple[Step, ...]:
    """Steps from `root` to `ctx` built from its state rather than its
    history: metric, time grain, split, filters, window, comparison.

    A window that ends on the latest period is written relatively, so it
    rolls forward like any other tile; one that ends earlier is pinned with
    `set_time`, because that is what the chart shows."""
    steps: list[Step] = []

    def add(verb: str, **params: Any) -> None:
        steps.append(Step(verb, tuple(sorted(params.items()))))

    if ctx.metric != root.metric:
        add("switch_metric", metric=ctx.metric)
    grain = ctx.time.grain
    if grain != root.time.grain:
        add("set_grain", grain=grain)
    time_col = model.time_column
    dims = [g for g in ctx.grain if g != time_col]
    if not ctx.grain:
        for _ in range(len(root.grain) + len(model.hierarchies) + 1):
            add("drill_up")
            probe = resolve(model, engine, Tile("", "", tuple(steps)), root)
            if not probe.context.grain:
                break
    elif dims and dims[0] not in root.grain:
        add("decompose", dimension=dims[0])
    for dim, member in ctx.filters:
        add("focus", dimension=dim, member=member)
    if time_col in ctx.grain:
        if dims:
            add("cross", dimension=time_col, periods=ctx.time.length)
        else:
            add("trend", periods=ctx.time.length)
    elif len(dims) == 2:
        add("cross", dimension=dims[1])
    if ctx.time.end != engine.latest(grain) or (
            time_col not in ctx.grain and ctx.time.length != 1):
        add("set_time", start=ctx.time.start, end=ctx.time.end, grain=grain)
    if ctx.comparison:
        add("compare", period=ctx.comparison)
    return tuple(steps)


# --------------------------------------------------------------------------
# reading a tile
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class TileReading:
    """What a tile says at a glance, before anyone digs in."""
    resolved: Resolved
    headline: ExplanationResult | None     # total, change and evidence
    why: Evidence | None                   # the one line that says why
    exceptions: int | None                 # members past NOTABLE_Z, if read


def read(model: SemanticModel, engine: AnalyticalEngine,
         resolved: Resolved) -> TileReading:
    """The headline number, its change, the evidence for it, and how many of
    the tile's members are unusual against their own history.

    All of it is the ordinary explain engine and exceptions analysis, run on
    the tile's aggregate - nothing here computes a number of its own.
    """
    ctx = aggregate_view(model, resolved.context)
    if on_time(model, ctx):
        # A trend's headline is its latest period: comparing a whole window
        # with the period before it would overlap, and be refused.
        ctx = ctx.evolve(LineageStep("collapse", "Read the latest period", ()),
                         grain=(), time=TimeWindow.single(ctx.time.end,
                                                          ctx.time.grain))
    try:
        headline: ExplanationResult | None = explain(engine, model, ctx)
    except SemanticError:
        headline = None
    return TileReading(resolved, headline, _why(headline, ctx),
                       _exceptions(model, engine, ctx))


def _why(headline: ExplanationResult | None, ctx: Context) -> Evidence | None:
    """The tile's own split explains it best, so evidence on that dimension
    leads; otherwise the strongest evidence for the scope."""
    if headline is None or not headline.evidence:
        return None
    dim = ctx.grain[0] if ctx.grain else None
    return next((e for e in headline.evidence if e.dimension == dim),
                headline.evidence[0])


def _exceptions(model: SemanticModel, engine: AnalyticalEngine,
                ctx: Context) -> int | None:
    if len(ctx.grain) != 1 or on_time(model, ctx):
        return None
    try:
        res = engine.execute(apply(model, ctx, Operation.of(
            "exceptions", "dashboard")), EXCEPTIONS)
    except (SemanticError, OperationError):
        return None
    return sum(1 for r in res.rows
               if abs(dict(r.extra).get("z", 0.0)) >= NOTABLE_Z)


# --------------------------------------------------------------------------
# checking
# --------------------------------------------------------------------------

def check(model: SemanticModel, engine: AnalyticalEngine, dashboard: Dashboard,
          root: Context) -> list[tuple[str, str, str]]:
    """(level, where, message) for every tile: replayed and computed against
    the real data, so `explain-beaucoup check` catches a broken tile."""
    issues: list[tuple[str, str, str]] = []
    if not dashboard.tiles:
        issues.append(("warning", "dashboard", "no tiles"))
    for t in dashboard.tiles:
        where = f"dashboard.{t.id}"
        try:
            r = resolve(model, engine, t, root)
            engine.execute(r.context, r.kind)
        except SemanticError as exc:
            issues.append(("error", where, str(exc)))
            continue
        issues.append(("info", where, f"{r.context} as {r.kind}"))
    return issues
