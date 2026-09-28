"""ViewSpec: map a deterministic ResultHandle into a Vega-Lite specification.

Vega renders marks and emits selections. It does not decide what anything
means - the metric, grain, scope and legal operations all live upstream
(design plan section 10).
"""

from __future__ import annotations

from typing import Any

from ..core import timegrain as tg
from ..core.operations import (BREAKDOWN, CHANGE, DISTRIBUTION, EXCEPTIONS,
                               TIMESERIES)
from ..engine.engine import ResultHandle, comparison_label
from ..semantic.specs import SemanticModel
from . import format as fmt
from .theme import Theme

FONT = "system-ui, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
MAX_BARS = 18
LABEL_LIMIT = 14          # direct-label only while the labels can breathe


# --------------------------------------------------------------------------

def _scale(domain: list[float] | None) -> dict[str, Any]:
    return {"domain": domain, "nice": False} if domain else {"zero": True}


def config(theme: Theme) -> dict[str, Any]:
    return {
        "background": theme.surface,
        "font": FONT,
        "padding": {"left": 8, "top": 8, "right": 20, "bottom": 8},
        "view": {"stroke": None},
        "axis": {
            "labelColor": theme.muted, "titleColor": theme.ink_2,
            "domainColor": theme.axis, "tickColor": theme.axis,
            "labelFontSize": 11, "titleFontSize": 11, "titleFontWeight": 500,
            "labelPadding": 6, "titlePadding": 12, "grid": False,
            "labelFont": FONT, "titleFont": FONT,
        },
        "legend": {
            "labelColor": theme.ink_2, "titleColor": theme.ink_2,
            "labelFontSize": 11, "titleFontSize": 11, "symbolType": "square",
            "symbolSize": 90, "orient": "top", "direction": "horizontal",
            "offset": 4, "labelFont": FONT, "titleFont": FONT, "title": None,
        },
        "title": {
            "color": theme.ink, "fontSize": 14, "fontWeight": 600,
            "anchor": "start", "subtitleColor": theme.ink_2,
            "subtitleFontSize": 11.5, "subtitlePadding": 6, "offset": 6,
            "font": FONT, "subtitleFont": FONT,
        },
        "bar": {"cornerRadiusEnd": 4},
        "line": {"strokeWidth": 2, "strokeCap": "round", "strokeJoin": "round"},
        "point": {"size": 64, "filled": True},
        "rule": {"color": theme.axis},
        "text": {"font": FONT, "fontSize": 11},
    }


def _value_axis(theme: Theme, title: str, format_: str) -> dict[str, Any]:
    return {"title": title, "format": format_, "grid": True,
            "gridColor": theme.grid, "gridWidth": 1, "gridDash": [],
            "domain": False, "ticks": False, "tickCount": 5}


def _padded_domain(values: list[float], *, pad: float = 0.16
                   ) -> list[float] | None:
    """Reserve room at the data end for the direct labels.

    Without this, a bar that reaches the edge of the plot puts its value label
    on top of the opposite axis's category labels.
    """
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or abs(hi) or 1.0
    return [min(lo - span * pad, 0.0) if lo < 0 else 0.0,
            max(hi + span * pad, 0.0) if hi > 0 else 0.0]


def _cat_axis(title: str | None = None) -> dict[str, Any]:
    return {"title": title, "domain": False, "ticks": False, "labelLimit": 160}


def _titles(model: SemanticModel, res: ResultHandle) -> tuple[str, str]:
    ctx = res.context
    metric = res.metric
    dim = res.dimension
    if res.kind == TIMESERIES:
        head = f"{metric.label} over time"
    elif res.kind == CHANGE:
        head = (f"What moved {metric.label.lower()}"
                + (f" - by {model.label_of(dim)}" if dim else ""))
    elif res.kind == EXCEPTIONS:
        head = (f"Exceptions in {metric.label.lower()}"
                + (f" by {model.label_of(dim)}" if dim else ""))
    elif res.kind == DISTRIBUTION:
        head = f"Distribution of {metric.label.lower()}"
    elif dim:
        head = f"{metric.label} by {model.label_of(dim)}"
    else:
        head = metric.label

    bits = [ctx.scope_label() if ctx.filters else "All " + model.dataset,
            ctx.time.label]
    if ctx.comparison:
        bits.append(f"vs {comparison_label(model, ctx)}")
    if res.total is not None and res.kind != DISTRIBUTION:
        word = "latest" if res.kind == TIMESERIES else "total"
        bits.append(f"{word} {fmt.value(metric, res.total, short=True)}")
    if res.kind == TIMESERIES and any(
            dict(r.extra).get("partial") for r in res.rows):
        bits.append(f"final {ctx.time.grain} incomplete")
    return head, "  ·  ".join(bits)


# --------------------------------------------------------------------------

def spec_for(model: SemanticModel, res: ResultHandle, theme: Theme, *,
             selected: str | None = None, width: int = 720,
             height: int = 380) -> dict[str, Any]:
    builders = {
        TIMESERIES: _timeseries_spec,
        CHANGE: _change_spec,
        EXCEPTIONS: _exceptions_spec,
        DISTRIBUTION: _distribution_spec,
    }
    build = builders.get(res.kind, _breakdown_spec)
    spec = build(model, res, theme, selected, width, height)
    head, sub = _titles(model, res)
    spec["title"] = {"text": head, "subtitle": sub}
    spec["config"] = config(theme)
    spec["$schema"] = "https://vega.github.io/schema/vega-lite/v6.json"
    if not isinstance(spec.get("height"), dict):
        # Only continuous heights can be fitted; a band `step` height sizes
        # itself and Vega-Lite warns if asked to fit it.
        spec["autosize"] = {"type": "fit-x", "contains": "padding", "resize": True}
    # The Qt dock is resizable, so let the chart follow its container rather
    # than baking in a pixel width.
    spec["width"] = "container"
    return spec


# -- breakdown -------------------------------------------------------------

def _breakdown_spec(model: SemanticModel, res: ResultHandle, theme: Theme,
                    selected: str | None, width: int, height: int) -> dict[str, Any]:
    metric = res.metric
    rows = [r for r in res.rows if r.value is not None][:MAX_BARS]
    vfmt = fmt.vega_format(metric)
    data = [{
        "key": r.key, "label": r.label, "value": r.value,
        "prior": r.prior, "delta": r.delta, "delta_pct": r.delta_pct,
        "share": r.share,
        "value_txt": fmt.value(metric, r.value, short=True),
        "delta_txt": fmt.signed(metric, r.delta) if r.delta is not None else None,
        "share_txt": f"{r.share:.1%}" if r.share is not None else None,
        "selected": r.key == selected,
    } for r in rows]

    tooltip = [
        {"field": "label", "title": model.label_of(res.dimension or "")},
        {"field": "value_txt", "title": metric.label},
    ]
    if any(d["share"] is not None for d in data):
        tooltip.append({"field": "share_txt", "title": "Share of total"})
    if res.context.comparison:
        tooltip += [{"field": "delta_txt", "title": "Change"},
                    {"field": "delta_pct", "title": "Change %", "format": ".1%"}]

    order = [d["key"] for d in data]
    comparing = bool(res.context.comparison) and any(d["prior"] is not None for d in data)

    if comparing:
        # Two series -> long form, legend present, categorical slots 1 and 2.
        long: list[dict[str, Any]] = []
        for d in data:
            long.append({**d, "series": "This period", "v": d["value"]})
            if d["prior"] is not None:
                long.append({**d, "series": "Reference", "v": d["prior"],
                             "value_txt": fmt.value(metric, d["prior"], short=True)})
        layer_bar = {
            "data": {"values": long},
            "mark": {"type": "bar", "cornerRadiusEnd": 4},
            "encoding": {
                "y": {"field": "label", "type": "nominal", "sort": order,
                      "axis": _cat_axis()},
                "yOffset": {"field": "series", "type": "nominal",
                            "sort": ["This period", "Reference"],
                            "scale": {"paddingInner": 0.18}},
                "x": {"field": "v", "type": "quantitative",
                      "axis": _value_axis(theme, metric.label, vfmt),
                      "scale": _scale(_padded_domain(
                          [d["v"] for d in long]))},
                "color": {"field": "series", "type": "nominal",
                          "sort": ["This period", "Reference"],
                          "scale": {"range": [theme.series[0], theme.series[1]]},
                          "legend": {"orient": "top"}},
                "stroke": {"condition": {"test": "datum.selected",
                                         "value": theme.selected},
                           "value": theme.surface},
                "strokeWidth": {"condition": {"test": "datum.selected", "value": 2},
                                "value": 1},
                "tooltip": tooltip,
            },
            "height": {"step": 30},
        }
        layers: list[dict[str, Any]] = [layer_bar]
        if len(long) <= LABEL_LIMIT:
            layers.append({
                "data": {"values": long},
                "mark": {"type": "text", "align": "left", "dx": 7,
                         "baseline": "middle", "color": theme.ink_2,
                         "fontSize": 10.5},
                "encoding": {
                    "y": {"field": "label", "type": "nominal", "sort": order},
                    "yOffset": {"field": "series", "type": "nominal",
                                "sort": ["This period", "Reference"],
                                "scale": {"paddingInner": 0.18}},
                    "x": {"field": "v", "type": "quantitative"},
                    "text": {"field": "value_txt"},
                },
            })
        return {"layer": layers, "width": width}

    bar = {
        "mark": {"type": "bar", "cornerRadiusEnd": 4, "color": theme.series[0]},
        "encoding": {
            "y": {"field": "label", "type": "nominal", "sort": order,
                  "axis": _cat_axis(),
                  "scale": {"paddingInner": 0.09, "paddingOuter": 0.14}},
            "x": {"field": "value", "type": "quantitative",
                  "axis": _value_axis(theme, metric.label, vfmt),
                  "scale": _scale(_padded_domain([d["value"] for d in data]))},
            "fillOpacity": {"condition": {"test": "datum.selected", "value": 1},
                            "value": 0.92} if selected else {"value": 0.92},
            "stroke": {"condition": {"test": "datum.selected",
                                     "value": theme.selected},
                       "value": "transparent"},
            "strokeWidth": {"condition": {"test": "datum.selected", "value": 2},
                            "value": 0},
            "tooltip": tooltip,
        },
    }
    layers: list[dict[str, Any]] = [bar]
    if len(data) <= LABEL_LIMIT:
        layers.append({
            "mark": {"type": "text", "align": "left", "dx": 7, "baseline": "middle",
                     "color": theme.ink_2, "fontSize": 11},
            "encoding": {
                "y": {"field": "label", "type": "nominal", "sort": order},
                "x": {"field": "value", "type": "quantitative"},
                "text": {"field": "value_txt"},
            },
        })
    return {"data": {"values": data}, "layer": layers, "width": width,
            "height": {"step": 28}}


# -- timeseries ------------------------------------------------------------

def _timeseries_spec(model: SemanticModel, res: ResultHandle, theme: Theme,
                     selected: str | None, width: int, height: int) -> dict[str, Any]:
    metric = res.metric
    vfmt = fmt.vega_format(metric)
    grain = res.context.time.grain
    # A period key is not a date string - "2026-W14" has no day part. Anchor
    # every key to the real first instant of its period instead.
    data = [{
        "key": r.key, "t": tg.anchor(r.key, grain).strftime("%Y-%m-%dT%H:%M:%S"),
        "label": r.label, "value": r.value,
        "prior": r.prior, "delta": r.delta, "delta_pct": r.delta_pct,
        "value_txt": fmt.value(metric, r.value, short=True),
        "delta_txt": fmt.signed(metric, r.delta) if r.delta is not None else None,
        "selected": r.key == selected,
        "is_last": r.key == res.rows[-1].key if res.rows else False,
        "partial": bool(dict(r.extra).get("partial")),
        "partial_txt": "incomplete period" if dict(r.extra).get("partial") else "",
    } for r in res.rows if r.value is not None]

    tooltip = [{"field": "label", "title": model.label_of(model.time_column)},
               {"field": "value_txt", "title": metric.label},
               {"field": "delta_txt", "title": "Change"}]
    if any(d["partial"] for d in data):
        tooltip.append({"field": "partial_txt", "title": "Note"})
    hover = {"name": "hover", "select": {
        "type": "point", "on": "pointerover", "nearest": True,
        "fields": ["t"], "clear": "pointerout"}}

    axis_format = {"day": "%d %b", "week": "%d %b", "month": "%b %y",
                   "quarter": "%b %y", "year": "%Y"}[grain]
    x_enc = {"field": "t", "type": "temporal",
             "axis": {"title": None, "format": axis_format, "grid": False,
                      "domain": True, "domainColor": theme.axis,
                      "ticks": False, "labelColor": theme.muted,
                      "tickCount": 8, "labelOverlap": "greedy"}}
    y_enc = {"field": "value", "type": "quantitative",
             "axis": _value_axis(theme, metric.label, vfmt),
             "scale": {"zero": metric.fmt != "percent", "nice": True}}

    layers: list[dict[str, Any]] = [
        {"mark": {"type": "line", "color": theme.series[0], "strokeWidth": 2},
         "encoding": {"x": x_enc, "y": y_enc}},
        # Crosshair: a rule that follows the nearest point.
        {"mark": {"type": "rule", "color": theme.axis, "strokeWidth": 1},
         "params": [hover],
         "encoding": {
             "x": x_enc,
             "opacity": {"condition": {"param": "hover", "empty": False, "value": 1},
                         "value": 0},
             "tooltip": tooltip}},
        {"mark": {"type": "point", "filled": True, "color": theme.series[0],
                  "stroke": theme.surface, "strokeWidth": 2},
         "encoding": {
             "x": x_enc, "y": y_enc,
             "size": {"condition": {"param": "hover", "empty": False, "value": 110},
                      "value": 0},
             "tooltip": tooltip}},
        # Partial periods are marked on the chart, not just in the notes.
        {"transform": [{"filter": "datum.partial"}],
         "mark": {"type": "point", "filled": False, "size": 80,
                  "strokeWidth": 2, "color": theme.series[0]},
         "encoding": {"x": x_enc, "y": y_enc, "tooltip": tooltip}},
        # Direct label on the final point - never a number on every point.
        {"transform": [{"filter": "datum.is_last"}],
         "mark": {"type": "text", "align": "right", "dy": -14, "dx": -2,
                  "color": theme.ink, "fontSize": 11.5, "fontWeight": 600},
         "encoding": {"x": x_enc, "y": y_enc, "text": {"field": "value_txt"}}},
    ]
    return {"data": {"values": data}, "layer": layers,
            "width": width, "height": height}


# -- change contribution ---------------------------------------------------

def _change_spec(model: SemanticModel, res: ResultHandle, theme: Theme,
                 selected: str | None, width: int, height: int) -> dict[str, Any]:
    metric = res.metric
    movers = [r for r in res.rows if r.delta is not None and r.delta != 0]
    movers.sort(key=lambda r: -abs(r.delta or 0))
    movers = movers[:MAX_BARS]
    movers.sort(key=lambda r: r.delta or 0)
    parent = res.delta_total or 0.0
    data = [{
        "key": r.key, "label": r.label, "delta": r.delta,
        "value": r.value, "prior": r.prior,
        "share_of_change": (r.delta / parent) if parent else None,
        "delta_txt": fmt.signed(metric, r.delta),
        "share_txt": f"{(r.delta/parent):.0%}" if parent else "-",
        "offsets": (parent != 0) and ((r.delta or 0) > 0) != (parent > 0),
        "selected": r.key == selected,
    } for r in movers]

    tooltip = [{"field": "label", "title": model.label_of(res.dimension or "")},
               {"field": "delta_txt", "title": "Change"},
               {"field": "share_txt", "title": "Share of total change"},
               {"field": "delta_pct", "title": "vs own base", "format": ".1%"}]

    order = [d["key"] for d in data]
    bars = {
        "mark": {"type": "bar", "cornerRadiusEnd": 4},
        "encoding": {
            "y": {"field": "label", "type": "nominal", "sort": order,
                  "axis": _cat_axis(),
                  "scale": {"paddingInner": 0.09, "paddingOuter": 0.14}},
            "x": {"field": "delta", "type": "quantitative",
                  "axis": _value_axis(theme, f"Change in {metric.label}",
                                      fmt.vega_format(metric)),
                  "scale": _scale(_padded_domain(
                      [d["delta"] for d in data], pad=0.22))},
            # Diverging: two poles, no hue at the midpoint.
            "color": {"condition": {"test": "datum.delta > 0",
                                    "value": theme.pos},
                      "value": theme.neg},
            "stroke": {"condition": {"test": "datum.selected",
                                     "value": theme.selected},
                       "value": "transparent"},
            "strokeWidth": {"condition": {"test": "datum.selected", "value": 2},
                            "value": 0},
            "tooltip": tooltip,
        },
    }
    # align/dx are mark properties in Vega-Lite, not encoding channels, so a
    # data-driven offset needs one filtered layer per side of the zero line.
    def label_layer(positive: bool) -> dict[str, Any]:
        return {
            "transform": [{"filter": f"datum.delta {'>' if positive else '<='} 0"}],
            "mark": {"type": "text", "baseline": "middle", "fontSize": 11,
                     "color": theme.ink_2,
                     "align": "left" if positive else "right",
                     "dx": 7 if positive else -7},
            "encoding": {
                "y": {"field": "label", "type": "nominal", "sort": order},
                "x": {"field": "delta", "type": "quantitative"},
                "text": {"field": "delta_txt"},
            },
        }
    labels = [label_layer(True), label_layer(False)]
    zero = {"mark": {"type": "rule", "color": theme.axis, "strokeWidth": 1},
            "encoding": {"x": {"datum": 0}}}
    layers = [zero, bars] + (labels if len(data) <= LABEL_LIMIT else [])
    return {"data": {"values": data}, "layer": layers, "width": width,
            "height": {"step": 28}}


# -- exceptions ------------------------------------------------------------

def _exceptions_spec(model: SemanticModel, res: ResultHandle, theme: Theme,
                     selected: str | None, width: int, height: int) -> dict[str, Any]:
    metric = res.metric
    rows = res.rows[:MAX_BARS]
    data = [{
        "key": r.key, "label": r.label, "z": dict(r.extra).get("z", 0.0),
        "value": r.value, "baseline": dict(r.extra).get("baseline"),
        "value_txt": fmt.value(metric, r.value, short=True),
        "baseline_txt": fmt.value(metric, dict(r.extra).get("baseline"), short=True),
        "z_txt": f"{dict(r.extra).get('z', 0.0):+.1f} sd",
        "selected": r.key == selected,
    } for r in rows]
    order = [d["key"] for d in sorted(data, key=lambda d: d["z"])]
    tooltip = [{"field": "label", "title": model.label_of(res.dimension or "")},
               {"field": "value_txt", "title": metric.label},
               {"field": "baseline_txt", "title": "12-month baseline"},
               {"field": "z_txt", "title": "Deviation"}]
    bars = {
        "mark": {"type": "bar", "cornerRadiusEnd": 4},
        "encoding": {
            "y": {"field": "label", "type": "nominal", "sort": order,
                  "axis": _cat_axis(),
                  "scale": {"paddingInner": 0.09, "paddingOuter": 0.14}},
            "x": {"field": "z", "type": "quantitative",
                  "axis": _value_axis(theme, "Standard deviations from baseline",
                                      ".1f"),
                  "scale": _scale(_padded_domain([d["z"] for d in data],
                                                 pad=0.22))},
            "color": {"condition": {"test": "datum.z > 0", "value": theme.pos},
                      "value": theme.neg},
            "stroke": {"condition": {"test": "datum.selected", "value": theme.selected},
                       "value": "transparent"},
            "strokeWidth": {"condition": {"test": "datum.selected", "value": 2},
                            "value": 0},
            "tooltip": tooltip,
        },
    }
    def z_label_layer(positive: bool) -> dict[str, Any]:
        return {
            "transform": [{"filter": f"datum.z {'>' if positive else '<='} 0"}],
            "mark": {"type": "text", "baseline": "middle", "fontSize": 11,
                     "color": theme.ink_2,
                     "align": "left" if positive else "right",
                     "dx": 7 if positive else -7},
            "encoding": {
                "y": {"field": "label", "type": "nominal", "sort": order},
                "x": {"field": "z", "type": "quantitative"},
                "text": {"field": "z_txt"},
            },
        }
    labels = [z_label_layer(True), z_label_layer(False)]
    bands = [{"mark": {"type": "rule", "color": theme.grid, "strokeDash": [3, 3]},
              "encoding": {"x": {"datum": s}}} for s in (-2, 2)]
    zero = {"mark": {"type": "rule", "color": theme.axis},
            "encoding": {"x": {"datum": 0}}}
    return {"data": {"values": data},
            "layer": bands + [zero, bars] + (labels if len(data) <= LABEL_LIMIT else []),
            "width": width, "height": {"step": 28}}


# -- distribution ----------------------------------------------------------

def _distribution_spec(model: SemanticModel, res: ResultHandle, theme: Theme,
                       selected: str | None, width: int, height: int) -> dict[str, Any]:
    metric = res.metric
    data = [{"key": r.key, "value": r.value} for r in res.rows
            if r.value is not None]
    vals = sorted(d["value"] for d in data)
    median = vals[len(vals) // 2] if vals else 0

    hist = {
        "mark": {"type": "bar", "color": theme.series[0], "opacity": 0.92},
        "encoding": {
            "x": {"field": "value", "type": "quantitative",
                  "bin": {"maxbins": 36},
                  "axis": _value_axis(theme, metric.label,
                                      fmt.vega_format(metric))},
            "y": {"aggregate": "count", "type": "quantitative",
                  "axis": {"title": "Cells", "grid": True,
                           "gridColor": theme.grid, "domain": False,
                           "ticks": False, "tickCount": 4}},
            "tooltip": [{"aggregate": "count", "type": "quantitative",
                         "title": "Cells"},
                        {"field": "value", "bin": {"maxbins": 36},
                         "title": metric.label,
                         "format": fmt.vega_format(metric)}],
        },
    }
    median_rule = {
        "data": {"values": [{"m": median}]},
        "mark": {"type": "rule", "color": theme.ink_2, "strokeWidth": 1.5,
                 "strokeDash": [4, 3]},
        "encoding": {"x": {"field": "m", "type": "quantitative"}},
    }
    median_label = {
        "data": {"values": [{"m": median,
                             "t": f"median {fmt.value(metric, median, short=True)}"}]},
        "mark": {"type": "text", "align": "left", "dx": 6, "dy": -6,
                 "baseline": "top", "color": theme.ink_2, "fontSize": 11},
        "encoding": {"x": {"field": "m", "type": "quantitative"},
                     "y": {"value": 0}, "text": {"field": "t"}},
    }
    return {"data": {"values": data},
            "layer": [hist, median_rule, median_label],
            "width": width, "height": height}
