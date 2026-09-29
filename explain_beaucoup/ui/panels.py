"""Side panels: breadcrumbs, investigation map, explain evidence, context
inspector, suggested questions and pins."""

from __future__ import annotations

from typing import Any, Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QListWidget,
                             QListWidgetItem, QScrollArea, QSizePolicy,
                             QToolButton, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout, QWidget)

from ..core.graph import InvestigationGraph, Node
from ..engine.engine import ResultHandle
from ..engine.explain import Evidence, ExplanationResult
from ..semantic.specs import SemanticModel
from ..view import format as fmt
from ..view.theme import Theme

KIND_LABEL = {
    "change_contribution": "CHANGE DRIVER",
    "offset": "OFFSET",
    "anomaly": "ANOMALY",
    "new_member": "NEW",
    "missing_member": "MISSING",
    "concentration": "CONCENTRATION",
    "contribution": "CONTRIBUTION",
    "metric_relationship": "METRIC LINK",
}


def _kind_color(kind: str, direction: int, t: Theme) -> str:
    if kind in ("offset",):
        return t.series[1]
    if kind in ("anomaly", "missing_member"):
        return t.neg if direction < 0 else t.pos
    if kind == "change_contribution":
        return t.neg if direction < 0 else t.pos
    if kind == "metric_relationship":
        return t.series[6]
    return t.muted


# --------------------------------------------------------------------------

class Card(QFrame):
    """A clickable panel row."""

    clicked = pyqtSignal()

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFrameShape(QFrame.Shape.NoFrame)
        hover = "rgba(255,255,255,0.05)" if theme.is_dark else "rgba(11,11,11,0.035)"
        self.setStyleSheet(
            f"Card {{ background: {theme.surface}; border: 1px solid "
            f"{theme.border}; border-radius: 8px; }}"
            f"Card:hover {{ background: {hover}; }}")

    def mouseReleaseEvent(self, ev: Any) -> None:       # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(ev)


def _label(text: str, *, color: str, size: float = 12.0, bold: bool = False,
           wrap: bool = True) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(wrap)
    f = lab.font()
    f.setPointSizeF(size)
    f.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    lab.setFont(f)
    lab.setStyleSheet(f"color: {color}; background: transparent; border: none;")
    lab.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
    return lab


class ScrollList(QScrollArea):
    """A vertical stack of cards in a scroll area."""

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._host = QWidget()
        self._host.setStyleSheet(f"background: {theme.surface};")
        self.box = QVBoxLayout(self._host)
        self.box.setContentsMargins(10, 10, 10, 10)
        self.box.setSpacing(8)
        self.box.addStretch(1)
        self.setWidget(self._host)

    def clear(self) -> None:
        while self.box.count() > 1:
            item = self.box.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def add(self, widget: QWidget) -> None:
        self.box.insertWidget(self.box.count() - 1, widget)

    def add_note(self, text: str) -> None:
        self.add(_label(text, color=self.theme.muted, size=11.5))


# --------------------------------------------------------------------------

class BreadcrumbBar(QWidget):
    """The path through the investigation, always visible - the plan's
    alternative to hiding state inside filters."""

    node_clicked = pyqtSignal(str)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.setStyleSheet(
            f"background: {theme.surface}; border-bottom: 1px solid {theme.border};")
        self.box = QHBoxLayout(self)
        self.box.setContentsMargins(12, 7, 12, 7)
        self.box.setSpacing(2)
        self.box.addStretch(1)

    def set_path(self, model: SemanticModel, path: list[Node],
                 siblings: list[Node]) -> None:
        while self.box.count():
            item = self.box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        t = self.theme
        for i, node in enumerate(path):
            if i:
                self.box.addWidget(_label("›", color=t.muted, size=12.5, wrap=False))
            last = i == len(path) - 1
            btn = QToolButton()
            btn.setText(_crumb_text(model, node))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setAutoRaise(True)
            color = t.ink if last else t.ink_2
            weight = "600" if last else "400"
            btn.setStyleSheet(
                f"QToolButton {{ border: none; background: transparent; "
                f"color: {color}; font-weight: {weight}; padding: 3px 6px; "
                f"border-radius: 5px; }}"
                f"QToolButton:hover {{ background: {t.border}; }}")
            btn.clicked.connect(lambda _=False, u=node.uid: self.node_clicked.emit(u))
            if node.pinned:
                btn.setText("● " + btn.text())
            self.box.addWidget(btn)
        if siblings:
            self.box.addWidget(_label("  |  branches:", color=t.muted,
                                      size=11, wrap=False))
            for s in siblings:
                chip = QToolButton()
                chip.setText(_crumb_text(model, s))
                chip.setCursor(Qt.CursorShape.PointingHandCursor)
                chip.setStyleSheet(
                    f"QToolButton {{ border: 1px dashed {t.axis}; background: "
                    f"transparent; color: {t.ink_2}; padding: 2px 8px; "
                    f"border-radius: 9px; font-size: 11px; }}"
                    f"QToolButton:hover {{ background: {t.border}; }}")
                chip.clicked.connect(
                    lambda _=False, u=s.uid: self.node_clicked.emit(u))
                self.box.addWidget(chip)
        self.box.addStretch(1)


KIND_CRUMB = {"change_contribution": "what moved", "exceptions": "exceptions",
              "distribution": "distribution"}


def _crumb_text(model: SemanticModel, node: Node) -> str:
    """Name what the step *did*, so two nodes at the same grain don't read as
    the same crumb."""
    ctx = node.context
    op = node.op
    if op and op.param_map.get("member"):
        return str(op.param_map["member"])
    if node.kind in KIND_CRUMB:
        return KIND_CRUMB[node.kind]
    if op and op.verb == "set_grain":
        return f"by {ctx.time.grain}"
    if op and op.verb == "compare":
        return f"vs {str(op.param_map.get('period', '')).upper()}"
    if op and op.verb == "clear_comparison":
        return "no comparison"
    if op and op.verb == "switch_metric":
        return model.metric(ctx.metric).label
    if op and op.verb == "set_time":
        return ctx.time.label
    if ctx.grain:
        g = ctx.grain[0]
        return ("Trend" if g == model.time_column
                else f"by {model.label_of(g)}")
    return model.metric(ctx.metric).label


# --------------------------------------------------------------------------

class ExplainPanel(ScrollList):
    """Ranked, clickable evidence. Each card is a place you can go."""

    evidence_clicked = pyqtSignal(object)

    def show_placeholder(self) -> None:
        self.clear()
        t = self.theme
        self.add(_label("Nothing explained yet", color=t.ink, size=13, bold=True))
        self.add(_label(
            "Press <b>Explain</b> (Ctrl+E), or right-click any mark and choose "
            "<b>Explain this</b>. The engine ranks contribution, change "
            "contribution, offsets, concentration, anomalies and structural "
            "change — deterministically, with the numbers attached.",
            color=t.ink_2, size=11.5))
        self.add(_label(
            "Every card is a node you can open, so an explanation is always a "
            "place you can go rather than a claim you have to trust.",
            color=t.muted, size=11))

    def show_explanation(self, model: SemanticModel,
                         exp: ExplanationResult) -> None:
        self.clear()
        t = self.theme
        metric = model.metric(exp.context.metric)

        header = QFrame()
        header.setStyleSheet("background: transparent; border: none;")
        hb = QVBoxLayout(header)
        hb.setContentsMargins(2, 0, 2, 2)
        hb.setSpacing(2)
        hb.addWidget(_label(fmt.value(metric, exp.total), color=t.ink, size=21,
                            bold=True, wrap=False))
        if exp.delta is not None:
            col = t.good if (exp.delta >= 0) == metric.higher_is_better else t.critical
            arrow = "▲" if exp.delta >= 0 else "▼"
            hb.addWidget(_label(
                f"{arrow} {fmt.signed(metric, exp.delta)} ({exp.delta_pct:+.1%}) "
                f"vs {exp.comparison.upper() if exp.comparison else ''}",
                color=col, size=12, bold=True, wrap=False))
        hb.addWidget(_label(
            f"{metric.label} · {exp.context.scope_label()} · "
            f"{exp.context.time.label}", color=t.muted, size=11))
        if exp.best_dimension:
            hb.addWidget(_label(
                f"Strongest explanatory breakdown: "
                f"<b>{model.label_of(exp.best_dimension)}</b>",
                color=t.ink_2, size=11.5))
        self.add(header)

        if not exp.evidence:
            self.add_note("No evidence passed the ranking thresholds for this "
                          "node. Try a comparison period, or a coarser grain.")
            return

        for ev in exp.top(9):
            self.add(self._card(model, ev))

    def _card(self, model: SemanticModel, ev: Evidence) -> Card:
        t = self.theme
        card = Card(t)
        box = QVBoxLayout(card)
        box.setContentsMargins(11, 9, 11, 10)
        box.setSpacing(5)

        top = QHBoxLayout()
        top.setSpacing(6)
        chip = _label(KIND_LABEL.get(ev.kind, ev.kind.upper()),
                      color=_kind_color(ev.kind, ev.direction, t), size=9.5,
                      bold=True, wrap=False)
        top.addWidget(chip)
        top.addStretch(1)
        top.addWidget(ScoreBar(ev.score, _kind_color(ev.kind, ev.direction, t), t))
        box.addLayout(top)

        box.addWidget(_label(ev.headline, color=t.ink, size=12.5, bold=True))
        box.addWidget(_label(ev.detail, color=t.ink_2, size=11.5))
        if ev.target is not None:
            box.addWidget(_label("Click to open this as a node →",
                                 color=t.muted, size=10.5))
        card.clicked.connect(lambda e=ev: self.evidence_clicked.emit(e))
        return card


class ScoreBar(QWidget):
    """A small deterministic-score meter. Never the only signal - the number
    and the label carry the meaning."""

    def __init__(self, score: float, color: str, theme: Theme) -> None:
        super().__init__()
        self.score = max(0.0, min(score, 1.5)) / 1.5
        self.color = color
        self.theme = theme
        self.setFixedSize(54, 10)
        self.setToolTip(f"Evidence score {score:.2f}")

    def paintEvent(self, _ev: Any) -> None:       # noqa: N802
        from PyQt6.QtGui import QPainter
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.theme.grid))
        p.drawRoundedRect(0, 3, 54, 4, 2, 2)
        p.setBrush(QColor(self.color))
        p.drawRoundedRect(0, 3, max(4, int(54 * self.score)), 4, 2, 2)
        p.end()


# --------------------------------------------------------------------------

class QuestionsPanel(ScrollList):
    """Next questions generated from the semantic model, not hardcoded."""

    question_clicked = pyqtSignal(str, dict)

    def set_questions(self, questions: list[tuple[str, str, dict]]) -> None:
        self.clear()
        t = self.theme
        self.add_note("Generated from the current metric, grain, hierarchy and "
                      "comparison. Each runs the operation it names.")
        for text, verb, params in questions:
            card = Card(t)
            box = QHBoxLayout(card)
            box.setContentsMargins(11, 9, 11, 9)
            box.setSpacing(8)
            box.addWidget(_label(text, color=t.ink, size=12), 1)
            box.addWidget(_label("→", color=t.muted, size=13, wrap=False))
            card.clicked.connect(
                lambda v=verb, p=params: self.question_clicked.emit(v, p))
            self.add(card)


# --------------------------------------------------------------------------

class ContextInspector(ScrollList):
    """The Context object itself, on screen. The plan's section-3 table."""

    def show_context(self, model: SemanticModel, node: Node,
                     res: ResultHandle) -> None:
        self.clear()
        t = self.theme
        ctx = node.context
        metric = model.metric(ctx.metric)

        rows: list[tuple[str, str]] = [
            ("dataset", ctx.dataset),
            ("metric", f"{metric.label}  ({metric.kind})"),
            ("aggregation", metric.aggregation),
            ("scope / filters",
             "; ".join(f"{model.label_of(d)}={m}" for d, m in ctx.filters) or "-"),
            ("grain", "/".join(model.label_of(g) for g in ctx.grain) or "total"),
            ("time", ctx.time.label),
            ("comparison", ctx.comparison.upper() if ctx.comparison else "-"),
            ("context id", ctx.id),
            ("node id", node.uid),
            ("result id", res.id),
            ("fact rows", f"{res.fact_rows:,}"),
            ("computed in", f"{res.computed_ms:.1f} ms"),
        ]
        grid = QFrame()
        grid.setStyleSheet("background: transparent; border: none;")
        gb = QVBoxLayout(grid)
        gb.setContentsMargins(0, 0, 0, 0)
        gb.setSpacing(6)
        for k, v in rows:
            row = QHBoxLayout()
            row.setSpacing(10)
            key = _label(k, color=t.muted, size=11, wrap=False)
            key.setFixedWidth(104)
            key.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            row.addWidget(key)
            row.addWidget(_label(v, color=t.ink, size=11.5), 1)
            holder = QWidget()
            holder.setStyleSheet("background: transparent;")
            holder.setLayout(row)
            gb.addWidget(holder)
        self.add(grid)

        self.add(_label("PROVENANCE", color=t.muted, size=9.5, bold=True))
        prov = _label(res.provenance, color=t.ink_2, size=11)
        prov.setStyleSheet(
            f"color: {t.ink_2}; background: {t.plane}; border: 1px solid "
            f"{t.border}; border-radius: 6px; padding: 8px; "
            f"font-family: ui-monospace, Menlo, monospace; font-size: 10.5px;")
        prov.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.add(prov)

        if res.notes:
            self.add(_label("NOTES", color=t.muted, size=9.5, bold=True))
            for n in res.notes:
                self.add(_label("· " + n, color=t.ink_2, size=11))

        self.add(_label("LINEAGE", color=t.muted, size=9.5, bold=True))
        if not ctx.lineage:
            self.add_note("Root node.")
        for i, step in enumerate(ctx.lineage, 1):
            self.add(_label(f"{i}. <b>{step.verb}</b> — {step.summary}"
                            + (f"  <i>({step.source})</i>" if step.source else ""),
                            color=t.ink_2, size=11))


# --------------------------------------------------------------------------

class InvestigationTree(QTreeWidget):
    """The investigation map: nodes, branches and pins - nothing is replaced."""

    node_selected = pyqtSignal(str)
    pin_toggled = pyqtSignal(str)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.setHeaderHidden(True)
        self.setIndentation(14)
        self.setAnimated(True)
        self.setColumnCount(1)
        self.itemClicked.connect(self._on_click)
        self._items: dict[str, QTreeWidgetItem] = {}

    def _on_click(self, item: QTreeWidgetItem, _col: int) -> None:
        uid = item.data(0, Qt.ItemDataRole.UserRole)
        if uid:
            self.node_selected.emit(uid)

    def rebuild(self, model: SemanticModel, graph: InvestigationGraph) -> None:
        self.clear()
        self._items = {}
        for root in graph.roots:
            self._add(model, graph, root, None)
        self.expandAll()
        cur = self._items.get(graph.current or "")
        if cur:
            self.setCurrentItem(cur)

    def _add(self, model: SemanticModel, graph: InvestigationGraph,
             uid: str, parent: QTreeWidgetItem | None) -> None:
        node = graph.get(uid)
        text = _crumb_text(model, node)
        verb = node.op.verb.replace("_", " ") if node.op else "start"
        label = f"{text}   ·  {verb}"
        if node.pinned:
            label = "● " + label
        item = QTreeWidgetItem([label])
        item.setData(0, Qt.ItemDataRole.UserRole, uid)
        item.setToolTip(0, f"{node.title}\n{node.context.id}")
        if node.branch:
            item.setForeground(0, QColor(self.theme.series[
                (node.branch) % len(self.theme.series)]))
        if node.pinned:
            f = item.font(0)
            f.setWeight(QFont.Weight.DemiBold)
            item.setFont(0, f)
        if parent is None:
            self.addTopLevelItem(item)
        else:
            parent.addChild(item)
        self._items[uid] = item
        for child in node.children:
            self._add(model, graph, child, item)


class PinsPanel(QListWidget):
    """Frozen findings, kept for comparison and for the report."""

    pin_clicked = pyqtSignal(str)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.setWordWrap(True)
        self.itemClicked.connect(
            lambda it: self.pin_clicked.emit(it.data(Qt.ItemDataRole.UserRole)))

    def refresh(self, model: SemanticModel, graph: InvestigationGraph,
                engine: Any) -> None:
        self.clear()
        for node in graph.pins:
            metric = model.metric(node.context.metric)
            try:
                res = engine.execute(node.context, node.kind)
                val = fmt.value(metric, res.total, short=True)
            except Exception:
                val = "-"
            text = f"● {node.title}\n   {metric.label} {val}"
            if node.note:
                text += f"\n   “{node.note}”"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, node.uid)
            item.setToolTip(node.context.id)
            self.addItem(item)
        if not graph.pins:
            hint = QListWidgetItem("No pins yet.\nPin a node to keep a finding "
                                   "for comparison and for the report.")
            hint.setForeground(QColor(self.theme.muted))
            hint.setFlags(Qt.ItemFlag.NoItemFlags)
            self.addItem(hint)
