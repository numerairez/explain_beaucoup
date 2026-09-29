"""The dataset catalogue, and the scope a dataset is opened under.

Two things live here, and both happen *before* the workspace exists:

* **Discovery** - the models sitting in a folder, described without reading a
  single row of their data, so a picker can list them cheaply and say why an
  unusable one is unusable.
* **Scope** - member restrictions and a period range applied to the governed
  frame *itself*, before any Context is built.

Scope is deliberately not the `focus` verb. `focus` pins one member of one
dimension inside a session, and `drill_up` walks back out of it. A scope
narrows the population the whole session is about: every total, share, baseline
and explanation is computed against it, and no verb in the grammar can escape
it. That is why it is chosen once, up front - and why the model's title carries
it afterwards, so a scoped total is never mistaken for the whole table.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml

from ..core import timegrain as tg
from ..data.source import DataSource
from .loader import LoadedModel, ModelError, build, load_model_file
from .specs import SemanticModel

MODEL_SUFFIXES = (".yaml", ".yml")


class ScopeError(Exception):
    """A scope that would leave nothing analysable."""


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CatalogEntry:
    """One model file, described from the YAML alone.

    Everything here is known without opening the data, which is what lets a
    folder of models be listed instantly. `row_count` and the member lists
    only exist once a dataset is actually selected.
    """

    path: Path
    dataset: str = ""
    title: str = ""
    dimensions: tuple[tuple[str, str], ...] = ()     # (name, label)
    metrics: tuple[tuple[str, str], ...] = ()
    hierarchies: tuple[tuple[str, tuple[str, ...]], ...] = ()
    time_column: str = ""
    time_grain: str = ""
    grains: tuple[str, ...] = ()
    data_path: Path | None = None
    error: str = ""

    @property
    def name(self) -> str:
        return self.title or self.dataset or self.path.stem

    @property
    def data_missing(self) -> bool:
        return self.data_path is not None and not self.data_path.exists()

    @property
    def ok(self) -> bool:
        """Whether this model can be opened at all."""
        return not self.error and not self.data_missing

    @property
    def problem(self) -> str:
        if self.error:
            return self.error
        if self.data_missing:
            return f"its data file is missing: {self.data_path}"
        return ""

    def summary(self) -> str:
        if self.problem:
            return self.problem
        return (f"{len(self.dimensions)} dimensions · {len(self.metrics)} "
                f"metrics · {self.time_grain}-grain")


def describe(path: str | Path) -> CatalogEntry:
    """Read one model file's shape. Never raises - an unusable model is an
    entry carrying the reason, because a picker has to show it and say why."""
    p = Path(path)
    try:
        doc = yaml.safe_load(p.read_text())
    except OSError as exc:
        return CatalogEntry(path=p, error=f"cannot be read: {exc.strerror}")
    except yaml.YAMLError as exc:
        return CatalogEntry(path=p, error=f"is not valid YAML: {exc}")
    if not isinstance(doc, dict):
        return CatalogEntry(path=p,
                            error="must be a YAML mapping at the top level")

    title = str(doc.get("title") or "")
    data_path: Path | None = None
    raw = doc.get("data")
    if isinstance(raw, dict) and raw.get("path"):
        d = Path(str(raw["path"])).expanduser()
        data_path = d if d.is_absolute() else (p.parent / d).resolve()

    try:
        model, _ = build(doc, p.parent, load_data=False)
    except ModelError as exc:
        return CatalogEntry(path=p, title=title, data_path=data_path,
                            error=str(exc))
    return CatalogEntry(
        path=p,
        dataset=model.dataset,
        title=model.title or title,
        dimensions=tuple((n, d.label) for n, d in model.dimensions.items()),
        metrics=tuple((n, m.label) for n, m in model.metrics.items()),
        hierarchies=tuple((h.label, h.levels)
                          for h in model.hierarchies.values()),
        time_column=model.time_column,
        time_grain=model.time_grain,
        grains=tuple(model.grains),
        data_path=data_path,
    )


def discover(directory: str | Path) -> list[CatalogEntry]:
    """Every model in a folder, one level of subfolders included so a team can
    group them. Unusable ones are returned too, with their reason."""
    d = Path(directory).expanduser()
    if not d.is_dir():
        return []
    found: list[Path] = []
    for p in sorted(d.iterdir()):
        if p.name.startswith("."):
            continue
        if p.is_file() and p.suffix.lower() in MODEL_SUFFIXES:
            found.append(p)
        elif p.is_dir():
            found += sorted(q for q in p.iterdir()
                            if q.is_file() and not q.name.startswith(".")
                            and q.suffix.lower() in MODEL_SUFFIXES)
    entries = [describe(p) for p in found]
    # Usable models first; within each group, alphabetically by display name.
    return sorted(entries, key=lambda e: (not e.ok, e.name.lower()))


def default_models_dir() -> Path:
    """Where models live: the folder beside the caller, else the one shipped
    with a source checkout."""
    here = Path.cwd() / "models"
    if here.is_dir():
        return here
    repo = Path(__file__).resolve().parents[2] / "models"
    return repo if repo.is_dir() else here


# --------------------------------------------------------------------------
# scope
# --------------------------------------------------------------------------

def members_of(frame: pd.DataFrame, column: str) -> list[str]:
    """The distinct values of a dimension column, as the text a picker shows
    and a scope stores. One conversion, used on both sides of the filter."""
    if column not in frame.columns:
        return []
    return sorted({str(v) for v in frame[column].dropna().unique()})


@dataclass(frozen=True)
class Scope:
    """What the session is about, decided before it starts.

    A filter here is a *set* of members, not the single member `focus` pins -
    "these three regions" is the question a team arrives with. An empty scope
    means the whole table.
    """

    filters: tuple[tuple[str, tuple[str, ...]], ...] = ()
    start: str | None = None
    end: str | None = None

    @staticmethod
    def of(filters: dict[str, list[str]] | None = None, *,
           start: str | None = None, end: str | None = None) -> "Scope":
        items = tuple((dim, tuple(sorted(str(m) for m in members)))
                      for dim, members in sorted((filters or {}).items())
                      if members)
        return Scope(items, start or None, end or None)

    @property
    def is_empty(self) -> bool:
        return not self.filters and not self.start and not self.end

    @property
    def filter_map(self) -> dict[str, tuple[str, ...]]:
        return dict(self.filters)

    def describe(self, model: SemanticModel | None = None) -> str:
        """A short human label - what the window title and status bar carry."""
        grain = model.time_grain if model else "month"

        def dim_label(dim: str) -> str:
            return model.label_of(dim) if model else dim.replace("_", " ").title()

        def period(key: str) -> str:
            try:
                return tg.label_of(key, grain)
            except Exception:                    # pragma: no cover
                return key

        bits: list[str] = []
        for dim, members in self.filters:
            if len(members) <= 3:
                bits.append(", ".join(members))
            else:
                bits.append(f"{len(members)} of {dim_label(dim)}")
        if self.start and self.end:
            bits.append(period(self.start) if self.start == self.end
                        else f"{period(self.start)} – {period(self.end)}")
        elif self.start:
            bits.append(f"from {period(self.start)}")
        elif self.end:
            bits.append(f"through {period(self.end)}")
        return " · ".join(bits)


@dataclass(frozen=True)
class ScopePreview:
    """What a scope would leave, reported before it is committed to."""

    rows: int
    total_rows: int
    periods: int
    span: tuple[str, str] | None
    breakdowns: tuple[str, ...]          # dimensions still worth grouping by
    problems: tuple[str, ...]            # blocking - the scope cannot be used
    notes: tuple[str, ...]               # worth knowing, not blocking

    @property
    def ok(self) -> bool:
        return not self.problems


def _mask(loaded: LoadedModel, scope: Scope) -> pd.Series:
    src, model = loaded.source, loaded.model
    mask = pd.Series(True, index=src.frame.index)
    if scope.start or scope.end:
        keys = src.keys(model.time_grain)
        if scope.start:
            mask &= keys >= scope.start
        if scope.end:
            mask &= keys <= scope.end
    for dim, members in scope.filters:
        column = model.dim(dim).column
        if column in src.frame.columns:
            mask &= src.frame[column].astype(str).isin(list(members))
    return mask


def frame_under(loaded: LoadedModel, scope: Scope) -> pd.DataFrame:
    """The rows a scope leaves. What a picker cross-filters its member lists
    against, so choosing Luzon stops offering provinces outside it."""
    return loaded.source.frame[_mask(loaded, scope)]


def preview(loaded: LoadedModel, scope: Scope) -> ScopePreview:
    """Everything the picker needs to say about a scope without applying it."""
    src, model = loaded.source, loaded.model
    frame = src.frame[_mask(loaded, scope)]
    total = src.row_count
    if frame.empty:
        return ScopePreview(
            0, total, 0, None, (),
            ("That scope selects no rows. Widen a filter or the period range.",),
            ())

    keys = src.keys(model.time_grain)[frame.index]
    periods = sorted(keys.dropna().unique().tolist())
    counts = {name: int(frame[d.column].nunique())
              for name, d in model.dimensions.items()
              if d.column in frame.columns}
    breakdowns = tuple(name for name, n in counts.items() if n > 1)

    problems: list[str] = []
    notes: list[str] = []
    if not breakdowns:
        problems.append("Every dimension is down to a single value, so there "
                        "is nothing to break the first view down by.")
    if len(periods) < 4:
        notes.append(f"{len(periods)} {model.time_grain}(s) in scope — trends "
                     f"and anomaly baselines need at least 4 periods before "
                     f"they mean anything.")
    collapsed = [n for n in model.default_grain if counts.get(n, 0) <= 1]
    if collapsed:
        notes.append(
            "The model's landing view breaks down by "
            + ", ".join(model.label_of(n) for n in collapsed)
            + ", which this scope pins to one value; the workspace will open "
              "on another dimension instead.")
    return ScopePreview(len(frame), total, len(periods),
                        (periods[0], periods[-1]), breakdowns,
                        tuple(problems), tuple(notes))


def apply_scope(loaded: LoadedModel, scope: Scope) -> LoadedModel:
    """Narrow the governed data, then re-derive everything learned from it.

    Cardinality is re-profiled against the subset, which is the point: a
    dimension the scope has collapsed to one member is a real dimension but a
    useless breakdown, and the menus should stop offering it.
    """
    if scope.is_empty:
        return loaded

    src, original = loaded.source, loaded.model
    mask = _mask(loaded, scope)
    if not bool(mask.any()):
        raise ScopeError("That scope selects no rows. Widen a filter or the "
                         "period range.")

    frame = src.frame[mask].reset_index(drop=True)
    # The time axis was already parsed once; carry the same timestamps over
    # rather than re-reading a column that may need a declared format.
    stamps = src.timestamps[mask].reset_index(drop=True)
    source = DataSource(frame=frame, time_column=src.time_column,
                        timestamps=stamps, native_grain=src.native_grain,
                        name=src.name)

    label = scope.describe(original)
    model = dataclasses.replace(
        original,
        title=f"{original.title or original.dataset} — {label}" if label
        else original.title)
    model.profile(frame)
    # A declared landing grain the scope has pinned to one member would open on
    # a one-bar chart, so fall back to whatever the model says is next.
    if any(model.member_counts.get(g, 0) <= 1 for g in model.default_grain):
        model.default_grain = ()
    if not model.first_grain():
        raise ScopeError("Every dimension is down to a single value under that "
                         "scope, so there is nothing to break the first view "
                         "down by.")
    return LoadedModel(model=model, source=source, path=loaded.path,
                       scope_label=label)


def open_model(path: str | Path, scope: Scope | None = None) -> LoadedModel:
    """Load a model and lock in its scope - the whole pre-workspace path."""
    loaded = load_model_file(path)
    return apply_scope(loaded, scope) if scope else loaded
