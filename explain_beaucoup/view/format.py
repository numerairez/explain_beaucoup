"""Value formatting driven by the semantic model's metric metadata."""

from __future__ import annotations

from ..semantic.specs import MetricSpec

DEFAULT_CURRENCY = ""   # the model supplies the symbol


def compact(v: float | None, *, decimals: int = 1) -> str:
    if v is None:
        return "-"
    a = abs(v)
    if a >= 1e12:
        return f"{v/1e12:,.{decimals}f}T"
    if a >= 1e9:
        return f"{v/1e9:,.{decimals}f}B"
    if a >= 1e6:
        return f"{v/1e6:,.{decimals}f}M"
    if a >= 1e3:
        return f"{v/1e3:,.{decimals}f}K"
    return f"{v:,.0f}"


def value(metric: MetricSpec, v: float | None, *, short: bool = False) -> str:
    if v is None:
        return "-"
    if metric.fmt == "percent":
        return f"{v:.2%}" if not short else f"{v:.1%}"
    if metric.fmt == "currency":
        sym = metric.symbol or DEFAULT_CURRENCY
        dp = 0 if metric.decimals is None else metric.decimals
        return f"{sym}{compact(v)}" if short else f"{sym}{v:,.{dp}f}"
    return compact(v) if short else f"{v:,.0f}"


def signed(metric: MetricSpec, v: float | None, *, short: bool = True) -> str:
    if v is None:
        return "-"
    body = value(metric, abs(v), short=short)
    if metric.fmt == "percent":
        return f"{'+' if v >= 0 else '-'}{abs(v):.2%}"
    return ("+" if v >= 0 else "-") + body


def pct(v: float | None, *, decimals: int = 1) -> str:
    return "-" if v is None else f"{v:+.{decimals}%}"


def vega_format(metric: MetricSpec, *, short: bool = True) -> str:
    """A d3-format string matching the metric's declared formatting."""
    if metric.fmt == "percent":
        return ".1%"
    if short:
        return "~s"
    return ",.0f"
