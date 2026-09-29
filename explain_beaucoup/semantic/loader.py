"""Load a semantic model from YAML, and check it against real data.

A team's entire set-up is one file. This module turns it into a SemanticModel
plus a DataSource, and - just as importantly - explains precisely what is
wrong when it doesn't line up with their data.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from ..core import timegrain as tg
from ..data.source import DataSource, DataSourceError, profile_frame, read_table
from .specs import (DEFAULT_COMPARISONS, ComparisonSpec, DimensionSpec,
                    Hierarchy, MetricSpec, RelationshipSpec, SemanticModel)

VALID_METRIC_KINDS = {"additive", "semi_additive", "ratio", "count"}
VALID_FORMATS = {"currency", "percent", "number"}


class ModelError(Exception):
    """A model file that cannot be loaded, with the offending key named."""


@dataclass
class Issue:
    level: str        # "error" | "warning" | "info"
    where: str
    message: str

    def __str__(self) -> str:
        return f"[{self.level.upper():7}] {self.where}: {self.message}"


@dataclass
class LoadedModel:
    model: SemanticModel
    source: DataSource
    path: Path
    # Set when the data was narrowed before the session opened; see
    # `semantic/catalog.py`. Empty means the whole table is in play.
    scope_label: str = ""

    @property
    def frame(self) -> pd.DataFrame:
        return self.source.frame


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _require(doc: dict, key: str, where: str) -> Any:
    if key not in doc or doc[key] in (None, ""):
        raise ModelError(f"{where}: missing required key '{key}'")
    return doc[key]


def _as_dict(value: Any, where: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ModelError(f"{where}: expected a mapping, got {type(value).__name__}")
    return value


def _as_list(value: Any, where: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ModelError(f"{where}: expected a list, got {type(value).__name__}")
    return value


# --------------------------------------------------------------------------
# building
# --------------------------------------------------------------------------

def build_dimensions(doc: dict) -> dict[str, DimensionSpec]:
    dims: dict[str, DimensionSpec] = {}
    for name, body in _as_dict(doc.get("dimensions"), "dimensions").items():
        body = _as_dict(body, f"dimensions.{name}")
        dims[name] = DimensionSpec(
            name=name,
            label=body.get("label", name.replace("_", " ").title()),
            column=body.get("column", name),
            describes=body.get("describes", ""),
            is_cell=bool(body.get("cell", False)),
        )
    if not dims:
        raise ModelError("dimensions: at least one dimension is required")
    return dims


def build_hierarchies(doc: dict, dims: dict[str, DimensionSpec]
                      ) -> dict[str, Hierarchy]:
    out: dict[str, Hierarchy] = {}
    for name, body in _as_dict(doc.get("hierarchies"), "hierarchies").items():
        body = _as_dict(body, f"hierarchies.{name}")
        levels = _as_list(_require(body, "levels", f"hierarchies.{name}"),
                          f"hierarchies.{name}.levels")
        for lvl in levels:
            if lvl not in dims:
                raise ModelError(
                    f"hierarchies.{name}.levels: '{lvl}' is not a declared "
                    f"dimension (declared: {', '.join(sorted(dims))})")
        out[name] = Hierarchy(name, body.get("label", name.title()),
                              tuple(levels))
        # Stamp hierarchy membership onto the dimensions themselves.
        for i, lvl in enumerate(levels):
            dims[lvl] = DimensionSpec(**{**dims[lvl].__dict__,
                                         "hierarchy": name, "level": i})
    return out


def build_metrics(doc: dict) -> dict[str, MetricSpec]:
    metrics: dict[str, MetricSpec] = {}
    for name, body in _as_dict(doc.get("metrics"), "metrics").items():
        where = f"metrics.{name}"
        body = _as_dict(body, where)
        kind = body.get("kind", "additive")
        if kind not in VALID_METRIC_KINDS:
            raise ModelError(f"{where}.kind: '{kind}' is not one of "
                             f"{', '.join(sorted(VALID_METRIC_KINDS))}")
        fmt = body.get("format", "number")
        if fmt not in VALID_FORMATS:
            raise ModelError(f"{where}.format: '{fmt}' is not one of "
                             f"{', '.join(sorted(VALID_FORMATS))}")
        if kind == "ratio":
            if not body.get("numerator") or not body.get("denominator"):
                raise ModelError(
                    f"{where}: a ratio metric needs both 'numerator' and "
                    f"'denominator' columns, so it can be recomputed at every "
                    f"grain instead of summed")
        elif not body.get("column"):
            raise ModelError(f"{where}: needs a 'column' (or kind: ratio)")
        metrics[name] = MetricSpec(
            name=name,
            label=body.get("label", name.replace("_", " ").title()),
            kind=kind, fmt=fmt,
            column=body.get("column"),
            numerator=body.get("numerator"),
            denominator=body.get("denominator"),
            higher_is_better=bool(body.get("higher_is_better", True)),
            unit=body.get("unit", ""),
            symbol=body.get("symbol", ""),
            decimals=body.get("decimals"),
            invalid_grains=frozenset(_as_list(body.get("invalid_grains"),
                                              f"{where}.invalid_grains")),
            describes=body.get("describes", ""),
        )
    if not metrics:
        raise ModelError("metrics: at least one metric is required")
    return metrics


def build_comparisons(doc: dict) -> tuple[ComparisonSpec, ...]:
    raw = _as_list(doc.get("comparisons"), "comparisons")
    if not raw:
        return DEFAULT_COMPARISONS
    out = []
    for i, body in enumerate(raw):
        body = _as_dict(body, f"comparisons[{i}]")
        name = _require(body, "name", f"comparisons[{i}]")
        spec = ComparisonSpec(name=name, label=body.get("label", ""),
                              periods=int(body.get("periods", 0)),
                              months=int(body.get("months", 0)),
                              years=int(body.get("years", 0)))
        if not (spec.periods or spec.months or spec.years):
            raise ModelError(
                f"comparisons[{i}] ('{name}'): needs one of periods / months / "
                f"years to say how far back the reference sits")
        out.append(spec)
    return tuple(out)


def build_relationships(doc: dict, metrics: dict) -> tuple[RelationshipSpec, ...]:
    out = []
    for i, body in enumerate(_as_list(doc.get("relationships"), "relationships")):
        body = _as_dict(body, f"relationships[{i}]")
        kind = body.get("kind", "volume_rate")
        spec = RelationshipSpec(
            kind=kind, metric=_require(body, "metric", f"relationships[{i}]"),
            volume=body.get("volume", ""), rate=body.get("rate", ""),
            volume_noun=body.get("volume_noun", "volume"),
            rate_noun=body.get("rate_noun", "rate"))
        for key in ("metric", "volume", "rate"):
            ref = getattr(spec, key)
            if ref and ref not in metrics:
                raise ModelError(
                    f"relationships[{i}].{key}: '{ref}' is not a declared metric")
        out.append(spec)
    return tuple(out)


# --------------------------------------------------------------------------

def build(doc: dict, base_dir: Path, *, frame: pd.DataFrame | None = None,
          load_data: bool = True) -> tuple[SemanticModel, DataSource | None]:
    if not isinstance(doc, dict):
        raise ModelError("The model file must be a YAML mapping at the top level")

    dataset = doc.get("dataset") or doc.get("name") or "dataset"
    dims = build_dimensions(doc)
    hiers = build_hierarchies(doc, dims)
    metrics = build_metrics(doc)

    time = _as_dict(_require(doc, "time", "<root>"), "time")
    time_column = _require(time, "column", "time")

    source: DataSource | None = None
    if load_data:
        if frame is None:
            data = _as_dict(_require(doc, "data", "<root>"), "data")
            path = Path(_require(data, "path", "data"))
            if not path.is_absolute():
                path = (base_dir / path).resolve()
            frame = read_table(path, sheet=data.get("sheet"),
                               separator=data.get("separator"))
        source = DataSource.build(
            frame, time_column, fmt=time.get("format"),
            dayfirst=bool(time.get("dayfirst", False)),
            native_grain=time.get("grain"), name=dataset)

    native = (source.native_grain if source
              else tg.check_grain(time.get("grain", "month")))
    grains = tuple(tg.check_grain(g) for g in
                   _as_list(time.get("grains"), "time.grains")) or (
        tuple(source.grains()) if source
        else tuple(g for g in tg.GRAINS if tg.ORDER[g] >= tg.ORDER[native]))

    defaults = _as_dict(doc.get("defaults"), "defaults")
    default_metric = defaults.get("metric") or next(iter(metrics))
    if default_metric not in metrics:
        raise ModelError(f"defaults.metric: '{default_metric}' is not a "
                         f"declared metric")
    default_grain = tuple(_as_list(defaults.get("grain"), "defaults.grain"))
    for g in default_grain:
        if g not in dims:
            raise ModelError(f"defaults.grain: '{g}' is not a declared dimension")

    cells = _as_list(doc.get("cells"), "cells")
    for c in cells:
        if c not in dims:
            raise ModelError(f"cells: '{c}' is not a declared dimension")
        dims[c] = DimensionSpec(**{**dims[c].__dict__, "is_cell": True})

    model = SemanticModel(
        dataset=dataset, dimensions=dims, hierarchies=hiers, metrics=metrics,
        time_column=time_column, time_grain=native, grains=grains,
        default_metric=default_metric, default_grain=default_grain,
        comparisons=build_comparisons(doc),
        relationships=build_relationships(doc, metrics),
        title=doc.get("title", ""),
    )
    if source is not None:
        model.profile(source.frame)
    return model, source


def load_model_file(path: str | Path) -> LoadedModel:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise ModelError(f"No such model file: {p}")
    try:
        doc = yaml.safe_load(p.read_text())
    except yaml.YAMLError as exc:
        raise ModelError(f"{p.name} is not valid YAML: {exc}") from None
    model, source = build(doc, p.parent)
    assert source is not None
    return LoadedModel(model=model, source=source, path=p)


# --------------------------------------------------------------------------
# checking
# --------------------------------------------------------------------------

def check(model: SemanticModel, source: DataSource) -> list[Issue]:
    """Everything that can be verified against the team's actual data."""
    issues: list[Issue] = []
    cols = set(map(str, source.frame.columns))

    for name, d in model.dimensions.items():
        if d.column not in cols:
            issues.append(Issue("error", f"dimensions.{name}",
                                f"column '{d.column}' is not in the data"))
            continue
        n = model.member_counts.get(name, 0)
        if n <= 1:
            issues.append(Issue(
                "warning", f"dimensions.{name}",
                f"only {n} distinct value(s) - it will never be offered as a "
                f"breakdown (still usable as a filter)"))
        elif n > 200:
            issues.append(Issue(
                "warning", f"dimensions.{name}",
                f"{n} distinct values - charts will be truncated; consider a "
                f"parent level in a hierarchy"))

    for name, m in model.metrics.items():
        for col in m.components():
            if col is None:
                continue
            if col not in cols:
                issues.append(Issue("error", f"metrics.{name}",
                                    f"column '{col}' is not in the data"))
            elif not pd.api.types.is_numeric_dtype(source.frame[col]):
                issues.append(Issue(
                    "error", f"metrics.{name}",
                    f"column '{col}' is {source.frame[col].dtype}, not numeric"))
        if m.is_ratio and m.denominator in cols:
            zeros = int((source.frame[m.denominator] == 0).sum())
            if zeros:
                issues.append(Issue(
                    "info", f"metrics.{name}",
                    f"denominator '{m.denominator}' is zero in {zeros:,} rows; "
                    f"those cells return no value rather than infinity"))

    for hname, h in model.hierarchies.items():
        usable = [lvl for lvl in h.levels if model.member_counts.get(lvl, 0) > 1]
        if not usable:
            issues.append(Issue("warning", f"hierarchies.{hname}",
                                "no level has more than one member"))
        for parent, child in zip(h.levels, h.levels[1:]):
            pc = model.member_counts.get(parent, 0)
            cc = model.member_counts.get(child, 0)
            if pc and cc and cc < pc:
                issues.append(Issue(
                    "warning", f"hierarchies.{hname}",
                    f"'{child}' has fewer members ({cc}) than its parent "
                    f"'{parent}' ({pc}) - the levels may be in the wrong order "
                    f"(they must run coarse to fine)"))

    span = source.span(model.time_grain)
    n_periods = tg.diff(span[1], span[0], model.time_grain) + 1
    issues.append(Issue("info", "time",
                        f"{source.row_count:,} rows spanning {n_periods} "
                        f"{model.time_grain}(s): {span[0]} to {span[1]}"))
    for c in model.comparisons:
        if c.years and n_periods < 13 and model.time_grain == "month":
            issues.append(Issue(
                "warning", f"comparisons.{c.name}",
                "not enough history for a year-on-year comparison"))
    if n_periods < 4:
        issues.append(Issue(
            "warning", "time",
            "fewer than 4 periods - trends and anomaly baselines will be weak"))

    if not model.first_grain():
        issues.append(Issue("error", "defaults.grain",
                            "no dimension has more than one member, so there "
                            "is nothing to break the first view down by"))
    return issues


# --------------------------------------------------------------------------
# scaffolding
# --------------------------------------------------------------------------

def scaffold(frame: pd.DataFrame, data_path: str, *, dataset: str,
             time_column: str | None = None) -> str:
    """Propose a starter model from a team's file. A guess, meant to be
    edited - every choice it makes is a line they can see and change."""
    profiles = profile_frame(frame)
    times = [p for p in profiles if p.role == "time"]
    if time_column is None:
        if not times:
            raise ModelError(
                "Could not find a time column. Pass --time <column>; the "
                "framework needs one to bucket periods.")
        time_column = times[0].name

    grain = "month"
    try:
        grain = tg.detect_grain(
            DataSource.build(frame, time_column).timestamps)
    except DataSourceError:
        pass

    dims = [p for p in profiles
            if p.role == "dimension" and p.name != time_column]
    measures = [p for p in profiles
                if p.role == "measure" and p.name != time_column]
    ignored = [p for p in profiles if p.role == "ignore"]

    lines: list[str] = [
        f"# Semantic model for {dataset}",
        "#",
        "# Generated by `explain-beaucoup init` - every line is a guess you",
        "# should review. The three things worth your attention:",
        "#   1. hierarchies  - drill paths, coarse to fine (none are guessed)",
        "#   2. ratio metrics - anything that must not be summed",
        "#   3. defaults     - the landing view",
        "",
        f"dataset: {dataset}",
        f"title: {dataset.replace('_', ' ').title()}",
        "",
        "data:",
        f"  path: {data_path}",
        "",
        "time:",
        f"  column: {time_column}",
        f"  grain: {grain}          # finest grain detected in the data",
        "",
        "dimensions:",
    ]
    if not dims:
        lines.append("  # none detected - declare at least one")
    for p in dims:
        label = p.name.replace("_", " ").title()
        note = f"{p.distinct} distinct"
        lines.append(f"  {p.name}:")
        lines.append(f"    label: {label}")
        lines.append(f"    column: {p.name}        # {note}")

    lines += ["", "hierarchies:",
              "  # Drill paths are NOT guessed - they are the one thing only",
              "  # you know. List levels coarse to fine, e.g.:",
              "  #",
              "  # geography:",
              "  #   label: Geography",
              "  #   levels: [country, region, city]",
              "", "metrics:"]
    if not measures:
        lines.append("  # no numeric columns detected - declare at least one")
    for p in measures:
        label = p.name.replace("_", " ").title()
        lines.append(f"  {p.name}:")
        lines.append(f"    label: {label}")
        lines.append("    kind: additive")
        lines.append("    format: number        # currency | percent | number")
        lines.append(f"    column: {p.name}")
    lines += [
        "",
        "  # A ratio must never be summed. Declare it from its components and",
        "  # the engine recomputes it correctly at every grain:",
        "  #",
        "  # margin_pct:",
        "  #   label: Margin %",
        "  #   kind: ratio",
        "  #   format: percent",
        "  #   numerator: margin",
        "  #   denominator: revenue",
        "",
        "comparisons:",
        f"  - {{name: prev, label: previous {grain}, periods: 1}}",
        "  - {name: yoy, label: same period last year, years: 1}",
        "",
        "# Optional: declare that a metric is volume x rate, and the explain",
        "# engine can split a movement into a volume effect and a rate effect.",
        "# relationships:",
        "#   - {kind: volume_rate, metric: revenue, volume: units,",
        "#      rate: avg_price, volume_noun: units, rate_noun: average price}",
        "",
        "defaults:",
        f"  metric: {measures[0].name if measures else '<metric>'}",
        f"  grain: [{dims[0].name if dims else '<dimension>'}]",
    ]
    if ignored:
        lines += ["", "# Columns left out (too many distinct values to group by):",
                  "#   " + ", ".join(f"{p.name} ({p.distinct})" for p in ignored)]
    return "\n".join(lines) + "\n"
