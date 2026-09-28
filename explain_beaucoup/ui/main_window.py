"""The analytical workspace window.

Event loop (design plan section 9):
    select mark -> emit structured selection -> resolve Context
    -> advertise valid operations -> execute deterministic operation
    -> add graph node -> render the resulting view
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (QAction, QComboBox, QDockWidget, QFileDialog,
                             QLabel, QMainWindow, QMenu, QMessageBox,
                             QTabWidget, QTextBrowser, QToolBar,
                             QVBoxLayout, QWidget)

from ..core import timegrain as tg
from ..core.context import Context, TimeWindow
from ..core.graph import InvestigationGraph, Node
from ..core.operations import (BREAKDOWN, CHANGE, DISTRIBUTION, EXCEPTIONS,
                               TIMESERIES, Operation, apply, result_kind)
from ..data.source import DataSource
from ..engine.engine import AnalyticalEngine, comparison_label
from ..engine.explain import Evidence, explain
from ..engine.narrate import (narrate_explanation, narrate_investigation,
                              suggested_questions)
from ..semantic.specs import SemanticError, SemanticModel
from ..view import format as fmt
from ..view.theme import THEMES, Theme
from ..view.vega import spec_for
from .chart_view import ChartView
from .panels import (BreadcrumbBar, ContextInspector, ExplainPanel,
                     InvestigationTree, PinsPanel, QuestionsPanel)
from .style import stylesheet

# Verbs that can be scoped to the mark under the cursor.
MEMBER_VERBS = {"drill_down", "decompose", "trend", "contribution",
                "change_contribution", "exceptions", "distribution", "focus"}
# Verbs that re-read the same node rather than changing its family of result.
PRESERVE_KIND = {"compare", "switch_metric", "set_time", "clear_comparison"}
GROUP_ORDER = ["Explain", "Composition", "Change", "Trend", "Exception",
               "Distribution", "Metric"]
LENSES = {
    "Composition": (BREAKDOWN, None),
    "Change": (CHANGE, "change_contribution"),
    "Trend": (TIMESERIES, "trend"),
    "Exception": (EXCEPTIONS, "exceptions"),
    "Distribution": (DISTRIBUTION, "distribution"),
}


class MainWindow(QMainWindow):
    def __init__(self, data: DataSource | pd.DataFrame,
                 model: SemanticModel) -> None:
        super().__init__()
        self.model = model
        frame = data.frame if isinstance(data, DataSource) else data
        if not model.member_counts:
            model.profile(frame)
        self.engine = AnalyticalEngine(data, model)
        self.graph = InvestigationGraph()
        self.theme_name = "light"
        self.selected_key: str | None = None
        self._syncing = False

        title = model.title or model.dataset.replace("_", " ").title()
        self.setWindowTitle(f"{title} - Explain Beaucoup")
        self.resize(1560, 940)

        self._build_chrome()
        self._build_docks()
        self._apply_theme(self.theme_name)

        # The landing view is whatever the model declares, not a hardcoded one.
        grain = model.first_grain()
        metric = model.metric(model.default_metric)
        root = Context.new(
            model.dataset, model.default_metric,
            TimeWindow.single(self.engine.source.latest(model.time_grain),
                              model.time_grain),
            grain=grain)
        self.navigate(root, BREAKDOWN,
                      title=f"{metric.label} by {model.label_of(grain[0])}"
                            if grain else metric.label)
        self.explain_panel.show_placeholder()

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------

    @property
    def theme(self) -> Theme:
        return THEMES[self.theme_name]

    @property
    def node(self) -> Node:
        n = self.graph.current_node
        assert n is not None
        return n

    def _build_chrome(self) -> None:
        self._grain: str = self.model.time_grain

        tb = QToolBar("Analysis")
        tb.setMovable(False)
        self.addToolBar(tb)

        tb.addWidget(QLabel("Metric"))
        self.metric_box = QComboBox()
        for name, m in self.model.metrics.items():
            self.metric_box.addItem(m.label, name)
        self.metric_box.currentIndexChanged.connect(self._on_metric)
        tb.addWidget(self.metric_box)

        tb.addWidget(QLabel("By"))
        self.grain_box = QComboBox()
        for g in self.model.grains:
            self.grain_box.addItem(g.title(), g)
        self.grain_box.currentIndexChanged.connect(self._on_grain)
        tb.addWidget(self.grain_box)

        tb.addWidget(QLabel("Period"))
        self.period_box = QComboBox()
        self._reload_periods(self.model.time_grain)
        self.period_box.currentIndexChanged.connect(self._on_period)
        tb.addWidget(self.period_box)

        tb.addWidget(QLabel("Compare"))
        self.cmp_box = QComboBox()
        self.cmp_box.addItem("None", None)
        for c in self.model.comparisons:
            self.cmp_box.addItem(c.name.upper(), c.name)
            self.cmp_box.setItemData(self.cmp_box.count() - 1,
                                     c.label_for(self.model.time_grain),
                                     Qt.ItemDataRole.ToolTipRole)
        self.cmp_box.currentIndexChanged.connect(self._on_comparison)
        tb.addWidget(self.cmp_box)

        tb.addWidget(QLabel("Lens"))
        self.lens_box = QComboBox()
        for lens in LENSES:
            self.lens_box.addItem(lens, lens)
        self.lens_box.currentIndexChanged.connect(self._on_lens)
        tb.addWidget(self.lens_box)

        tb.addSeparator()
        self.act_explain = self._action(tb, "Explain", "Ctrl+E", self.do_explain)
        self.act_up = self._action(tb, "Drill up", "Ctrl+Up",
                                   lambda: self.run_op("drill_up", {}))
        self.act_back = self._action(tb, "Back", "Ctrl+[", self.go_back)
        self.act_branch = self._action(tb, "Branch", "Ctrl+B", self.branch_here)
        self.act_pin = self._action(tb, "Pin", "Ctrl+P", self.pin_here)
        tb.addSeparator()
        self._action(tb, "Export report", "Ctrl+S", self.export_report)
        self.act_theme = self._action(tb, "Dark mode", "Ctrl+D", self.toggle_theme)

        centre = QWidget()
        box = QVBoxLayout(centre)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        self.crumbs = BreadcrumbBar(self.theme)
        self.crumbs.node_clicked.connect(self.goto_node)
        box.addWidget(self.crumbs)
        self.chart = ChartView()
        self.chart.bridge.selected.connect(self._on_mark_selected)
        self.chart.bridge.menu_requested.connect(self._on_mark_menu)
        self.chart.bridge.activated.connect(self._on_mark_activated)
        self.chart.bridge.error.connect(
            lambda msg: self.statusBar().showMessage(f"Chart error: {msg}", 8000))
        box.addWidget(self.chart, 1)
        self.setCentralWidget(centre)
        self.statusBar().showMessage("Ready")

    def _action(self, tb: QToolBar, text: str, shortcut: str, slot: Any) -> QAction:
        act = QAction(text, self)
        act.setShortcut(QKeySequence(shortcut))
        act.triggered.connect(slot)
        tb.addAction(act)
        self.addAction(act)
        return act

    def _build_docks(self) -> None:
        t = self.theme
        left = QDockWidget("Investigation")
        left.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable)
        left_tabs = QTabWidget()
        self.tree = InvestigationTree(t)
        self.tree.node_selected.connect(self.goto_node)
        self.pins = PinsPanel(t)
        self.pins.pin_clicked.connect(self.goto_node)
        left_tabs.addTab(self.tree, "Map")
        left_tabs.addTab(self.pins, "Pins")
        left.setWidget(left_tabs)
        left.setMinimumWidth(268)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, left)

        right = QDockWidget("Analysis")
        right.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable)
        self.right_tabs = QTabWidget()
        self.explain_panel = ExplainPanel(t)
        self.explain_panel.evidence_clicked.connect(self._on_evidence)
        self.questions = QuestionsPanel(t)
        self.questions.question_clicked.connect(
            lambda verb, params: self.run_op(verb, params))
        self.inspector = ContextInspector(t)
        self.report = QTextBrowser()
        self.report.setOpenExternalLinks(False)
        self.right_tabs.addTab(self.explain_panel, "Explain")
        self.right_tabs.addTab(self.questions, "Next")
        self.right_tabs.addTab(self.inspector, "Context")
        self.right_tabs.addTab(self.report, "Report")
        right.setWidget(self.right_tabs)
        right.setMinimumWidth(372)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, right)

    # ------------------------------------------------------------------
    # navigation
    # ------------------------------------------------------------------

    def navigate(self, ctx: Context, kind: str = BREAKDOWN, *,
                 op: Operation | None = None, parent: str | None = "__current__",
                 branch: bool = False, title: str = "") -> None:
        try:
            res = self.engine.execute(ctx, kind)
        except SemanticError as exc:
            self._blocked(str(exc))
            return
        parent_uid = self.graph.current if parent == "__current__" else parent
        if branch and parent_uid:
            node = self.graph.branch_from(parent_uid, ctx, kind=kind, op=op,
                                          title=title)
        else:
            node = self.graph.add(ctx, kind=kind, op=op, parent=parent_uid,
                                  title=title or self._node_title(ctx, kind))
        self.selected_key = (op.param_map.get("member") if op else None)
        self._render(node, res)

    def goto_node(self, uid: str) -> None:
        node = self.graph.goto(uid)
        try:
            res = self.engine.execute(node.context, node.kind)
        except SemanticError as exc:
            self._blocked(str(exc))
            return
        self.selected_key = None
        self._render(node, res)

    def go_back(self) -> None:
        node = self.node
        if node.parent:
            self.goto_node(node.parent)
        else:
            self.statusBar().showMessage("Already at the root of this path", 3000)

    def run_op(self, verb: str, params: dict[str, Any], *,
               member: str | None = None, branch: bool = False,
               source: str = "ui") -> None:
        if verb == "explain":
            self.do_explain(member=member)
            return
        p = dict(params)
        if member and verb in MEMBER_VERBS:
            p["member"] = member
        if verb == "set_grain" and "end" not in p:
            # Only the data layer knows where the data actually stops.
            p["end"] = self.engine.source.latest(p.get("grain",
                                                       self.node.context.time.grain))
            p.pop("latest", None)
        op = Operation.of(verb, source, **p)
        try:
            new_ctx = apply(self.model, self.node.context, op)
        except SemanticError as exc:
            self._blocked(str(exc))
            return
        except Exception as exc:                              # pragma: no cover
            self._blocked(str(exc))
            return
        kind = result_kind(self.model, new_ctx, op)
        if verb in PRESERVE_KIND and kind == BREAKDOWN:
            kind = self.node.kind if self.node.kind != TIMESERIES else BREAKDOWN
        self.navigate(new_ctx, kind, op=op, branch=branch)

    def branch_here(self) -> None:
        """Open a competing hypothesis beside the current path."""
        node = self.node
        alts = self.model.alternative_dimensions(node.context)
        if not alts:
            self.statusBar().showMessage(
                "No alternative dimension available to branch on", 4000)
            return
        menu = QMenu(self)
        menu.addSection("Branch into a competing hypothesis")
        for dim in alts[:6]:
            act = menu.addAction(f"Break down by {self.model.label_of(dim)}")
            act.triggered.connect(
                lambda _=False, d=dim: self.run_op(
                    "decompose", {"dimension": d}, member=self.selected_key,
                    branch=True, source="branch"))
        menu.exec_(self.mapToGlobal(QPoint(self.width() // 2, 120)))

    def pin_here(self) -> None:
        node = self.node
        if node.pinned:
            self.graph.unpin(node.uid)
            self.statusBar().showMessage("Unpinned", 2500)
        else:
            res = self.engine.execute(node.context, node.kind)
            metric = self.model.metric(node.context.metric)
            self.graph.pin(node.uid,
                           f"{metric.label} {fmt.value(metric, res.total, short=True)}")
            self.statusBar().showMessage("Pinned for comparison and the report", 2500)
        self._refresh_side()

    # ------------------------------------------------------------------
    # explain
    # ------------------------------------------------------------------

    def do_explain(self, *, member: str | None = None) -> None:
        ctx = self.node.context
        if member:
            dim = ctx.grain[0] if ctx.grain else None
            if dim and dim != self.model.time_column:
                from ..core.context import LineageStep
                ctx = ctx.with_filter(dim, member, LineageStep(
                    "focus", f"Focus on {member}",
                    (("dimension", dim), ("member", member)), "explain"))
        try:
            exp = explain(self.engine, self.model, ctx)
        except SemanticError as e:
            self._blocked(str(e))
            return
        self._last_explanation = exp
        self.explain_panel.show_explanation(self.model, exp)
        self.right_tabs.setCurrentIndex(0)
        self.report.setMarkdown(narrate_explanation(self.model, exp))
        self.statusBar().showMessage(
            f"Explained {ctx.id} - {len(exp.evidence)} pieces of evidence from "
            f"{len(exp.provenance)} governed queries", 6000)

    def _on_evidence(self, ev: Evidence) -> None:
        if ev.target is None:
            return
        op = Operation.of("focus", "explain-evidence",
                          **({"dimension": ev.dimension, "member": ev.member}
                             if ev.dimension and ev.member else {}))
        kind = BREAKDOWN
        ctx = ev.target
        if ctx.grain and ctx.grain[0] == self.model.time_column:
            kind = TIMESERIES
        title = ev.headline
        self.navigate(ctx, kind, op=op if ev.member else None, title=title)
        self.statusBar().showMessage(f"Opened evidence: {ev.headline}", 5000)

    # ------------------------------------------------------------------
    # chart interaction
    # ------------------------------------------------------------------

    def _on_mark_selected(self, payload: dict) -> None:
        key = payload.get("mark", {}).get("key")
        self.selected_key = key
        node = self.node
        res = self.engine.execute(node.context, node.kind)
        self._render(node, res, rebuild_side=False)
        if key:
            row = res.row(str(key))
            metric = self.model.metric(node.context.metric)
            if row:
                bits = [f"{key}: {fmt.value(metric, row.value)}"]
                if row.share is not None:
                    bits.append(f"{row.share:.1%} of total")
                if row.delta is not None:
                    bits.append(f"{fmt.signed(metric, row.delta)} vs reference")
                self.statusBar().showMessage("  ·  ".join(bits) +
                                             "   (right-click for operations)")

    def _on_mark_activated(self, payload: dict) -> None:
        """Double-click = the most natural drill from here."""
        key = payload.get("mark", {}).get("key")
        if not key:
            return
        ctx = self.node.context
        current = ctx.grain[0] if ctx.grain else None
        child = self.model.child_dimension(current) if current else None
        if child:
            self.run_op("drill_down", {"dimension": child}, member=str(key),
                        source=f"mark:{key}/dblclick")
        else:
            alts = self.model.alternative_dimensions(ctx)
            if alts:
                self.run_op("decompose", {"dimension": alts[0]}, member=str(key),
                            source=f"mark:{key}/dblclick")

    def _on_mark_menu(self, payload: dict) -> None:
        """Build the context menu from the semantic model's capabilities.

        The same selected context exposes the same grammar no matter which
        chart type it was selected from.
        """
        mark = payload.get("mark", {})
        key = mark.get("key")
        if key is not None:
            self.selected_key = str(key)
        ctx = self.node.context
        caps = self.model.capabilities(ctx)

        menu = QMenu(self)
        if key is not None:
            metric = self.model.metric(ctx.metric)
            value = mark.get("value")
            head = f"{key}"
            if value is not None:
                head += f" — {fmt.value(metric, float(value), short=True)}"
            menu.addSection(head)
        else:
            menu.addSection(str(ctx))

        source = f"view:{self.node.uid}/mark={key}" if key else f"view:{self.node.uid}"
        by_group: dict[str, list] = {}
        for cap in caps:
            by_group.setdefault(cap.group, []).append(cap)

        for group in GROUP_ORDER:
            caps_in = by_group.get(group)
            if not caps_in:
                continue
            if group != "Explain":
                menu.addSeparator()
            target: Any = menu
            if group == "Metric" and len(caps_in) > 2:
                target = menu.addMenu("Switch metric")
            for cap in caps_in:
                label = cap.label
                if key is not None and cap.verb in MEMBER_VERBS:
                    label = _member_label(cap.verb, label, str(key))
                if group == "Metric" and target is not menu:
                    label = label.replace("Switch metric to ", "")
                act = target.addAction(label)
                if cap.hint:
                    act.setToolTip(cap.hint)
                act.triggered.connect(
                    lambda _=False, v=cap.verb, p=cap.param_map,
                    k=(str(key) if key is not None else None), s=source:
                    self.run_op(v, p, member=k, source=s))

        menu.addSeparator()
        if key is not None:
            dim = ctx.grain[0] if ctx.grain else None
            if dim and dim != self.model.time_column:
                act = menu.addAction(f"Focus on {key} (keep this grain)")
                act.triggered.connect(
                    lambda _=False, d=dim, k=str(key), s=source:
                    self.run_op("focus", {"dimension": d, "member": k}, source=s))
        act_branch = menu.addAction("Branch from here…")
        act_branch.triggered.connect(self.branch_here)
        act_pin = menu.addAction("Unpin this node" if self.node.pinned
                                 else "Pin this node")
        act_pin.triggered.connect(self.pin_here)

        pos = self.chart.mapToGlobal(QPoint(int(payload.get("x", 0)),
                                            int(payload.get("y", 0))))
        menu.exec_(pos)

    # ------------------------------------------------------------------
    # toolbar handlers
    # ------------------------------------------------------------------

    def _on_metric(self) -> None:
        if self._syncing:
            return
        name = self.metric_box.currentData()
        if name and name != self.node.context.metric:
            self.run_op("switch_metric", {"metric": name}, source="toolbar")

    def _reload_periods(self, grain: str) -> None:
        """Repopulate the period picker for a calendar grain."""
        self._grain = grain
        self._periods = self.engine.source.periods(grain)
        self.period_box.blockSignals(True)
        self.period_box.clear()
        for key in self._periods:
            self.period_box.addItem(tg.label_of(key, grain), key)
        self.period_box.setCurrentIndex(len(self._periods) - 1)
        self.period_box.blockSignals(False)

    def _on_period(self) -> None:
        if self._syncing:
            return
        key = self.period_box.currentData()
        ctx = self.node.context
        if not key:
            return
        if ctx.grain and ctx.grain[0] == self.model.time_column:
            self.run_op("set_time",
                        {"start": tg.add(key, ctx.time.grain,
                                         -(ctx.time.length - 1)),
                         "end": key, "grain": ctx.time.grain}, source="toolbar")
            return
        if (ctx.time.start, ctx.time.end) != (key, key):
            self.run_op("set_time", {"start": key, "end": key,
                                     "grain": ctx.time.grain}, source="toolbar")

    def _on_grain(self) -> None:
        if self._syncing:
            return
        grain = self.grain_box.currentData()
        if grain and grain != self.node.context.time.grain:
            self.run_op("set_grain", {"grain": grain, "latest": True},
                        source="toolbar")

    def _on_comparison(self) -> None:
        if self._syncing:
            return
        cmp_ = self.cmp_box.currentData()
        if cmp_ == self.node.context.comparison:
            return
        if cmp_ is None:
            self.run_op("clear_comparison", {}, source="toolbar")
        else:
            self.run_op("compare", {"period": cmp_}, source="toolbar")

    def _on_lens(self) -> None:
        if self._syncing:
            return
        lens = self.lens_box.currentData()
        kind, verb = LENSES[lens]
        if verb is None:
            ctx = self.node.context
            if ctx.grain and ctx.grain[0] == self.model.time_column:
                alts = self.model.alternative_dimensions(ctx)
                if alts:
                    self.run_op("decompose", {"dimension": alts[0]}, source="lens")
                return
            self.navigate(ctx, BREAKDOWN, op=Operation.of("contribution", "lens"),
                          title=self._node_title(ctx, BREAKDOWN))
            return
        n = 12 if self.node.context.time.grain in ("day", "week", "month") else 8
        params: dict[str, Any] = {"periods": n} if verb == "trend" else {}
        self.run_op(verb, params, member=self.selected_key, source="lens")

    def toggle_theme(self) -> None:
        self.theme_name = "dark" if self.theme_name == "light" else "light"
        self.act_theme.setText("Light mode" if self.theme_name == "dark"
                               else "Dark mode")
        self._apply_theme(self.theme_name)
        # Panels hold token colours, so rebuild them against the new theme.
        self._build_docks_refresh()
        node = self.node
        self._render(node, self.engine.execute(node.context, node.kind))

    def _apply_theme(self, name: str) -> None:
        self.setStyleSheet(stylesheet(THEMES[name]))

    def _build_docks_refresh(self) -> None:
        t = self.theme
        for panel in (self.explain_panel, self.questions, self.inspector):
            panel.theme = t
            panel.widget().setStyleSheet(f"background: {t.surface};")
        self.tree.theme = t
        self.pins.theme = t
        self.crumbs.theme = t
        self.crumbs.setStyleSheet(
            f"background: {t.surface}; border-bottom: 1px solid {t.border};")

    def export_report(self) -> None:
        text = narrate_investigation(self.model, self.engine, self.graph)
        path, _ = QFileDialog.getSaveFileName(
            self, "Export investigation", "investigation.md",
            "Markdown (*.md);;All files (*)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        self.statusBar().showMessage(f"Investigation exported to {path}", 6000)

    # ------------------------------------------------------------------
    # rendering
    # ------------------------------------------------------------------

    def _render(self, node: Node, res: Any, *, rebuild_side: bool = True) -> None:
        spec = spec_for(self.model, res, self.theme, selected=self.selected_key)
        self.chart.render_spec(spec, node.uid, node.context.id, self.theme_name)
        self.crumbs.set_path(self.model, self.graph.path_to(node.uid),
                             self.graph.siblings(node.uid))
        self._sync_toolbar(node)
        if rebuild_side:
            self._refresh_side()
            self.inspector.show_context(self.model, node, res)
            self.questions.set_questions(suggested_questions(self.model, node))
            self.report.setMarkdown(
                narrate_investigation(self.model, self.engine, self.graph, node))
        metric = self.model.metric(node.context.metric)
        total = fmt.value(metric, res.total)
        delta = (f"   ·   {fmt.signed(metric, res.delta_total)} vs reference"
                 if res.delta_total is not None else "")
        self.statusBar().showMessage(
            f"{node.context}   ·   {total}{delta}   ·   {res.fact_rows:,} fact "
            f"rows in {res.computed_ms:.0f} ms   ·   cache {self.engine.cache_size()}")

    def _refresh_side(self) -> None:
        self.tree.rebuild(self.model, self.graph)
        self.pins.refresh(self.model, self.graph, self.engine)
        self.act_pin.setText("Unpin" if self.node.pinned else "Pin")

    def _sync_toolbar(self, node: Node) -> None:
        self._syncing = True
        ctx = node.context
        i = self.metric_box.findData(ctx.metric)
        if i >= 0:
            self.metric_box.setCurrentIndex(i)
        if ctx.time.grain != self._grain:
            self._reload_periods(ctx.time.grain)
        i = self.grain_box.findData(ctx.time.grain)
        if i >= 0:
            self.grain_box.setCurrentIndex(i)
        i = self.period_box.findData(ctx.time.end)
        if i >= 0:
            self.period_box.setCurrentIndex(i)
        i = self.cmp_box.findData(ctx.comparison)
        if i >= 0:
            self.cmp_box.setCurrentIndex(i)
        lens = {BREAKDOWN: "Composition", CHANGE: "Change", TIMESERIES: "Trend",
                EXCEPTIONS: "Exception", DISTRIBUTION: "Distribution"}.get(node.kind)
        if lens:
            i = self.lens_box.findData(lens)
            if i >= 0:
                self.lens_box.setCurrentIndex(i)
        self.act_up.setEnabled(bool(ctx.grain))
        self.act_back.setEnabled(bool(node.parent))
        self._syncing = False

    def _node_title(self, ctx: Context, kind: str) -> str:
        metric = self.model.metric(ctx.metric)
        if kind == TIMESERIES:
            return (f"{metric.label} by {ctx.time.grain} - "
                    f"{ctx.scope_label()}")
        if kind == CHANGE:
            return f"What moved {metric.label} - {ctx.scope_label()}"
        if kind == EXCEPTIONS:
            return f"Exceptions - {ctx.scope_label()}"
        if kind == DISTRIBUTION:
            return f"Distribution - {ctx.scope_label()}"
        if ctx.grain:
            return f"{metric.label} by {self.model.label_of(ctx.grain[0])}"
        return f"{metric.label} - {ctx.scope_label()}"

    def _blocked(self, reason: str) -> None:
        """Semantic safety: say why, and change nothing."""
        self.statusBar().showMessage(f"Blocked: {reason}", 8000)
        QMessageBox.information(self, "Operation not available", reason)


def _member_label(verb: str, label: str, key: str) -> str:
    if verb == "drill_down":
        return label.replace("Drill down to", f"Drill into {key} by")
    if verb == "decompose":
        return label.replace("Break down by", f"Break {key} down by")
    if verb == "trend":
        return f"Trend {key} over 12 months"
    if verb == "exceptions":
        return f"Find exceptions within {key}"
    if verb == "distribution":
        return f"Show distribution within {key}"
    if verb == "change_contribution":
        return f"What explains {key}'s change?"
    if verb == "contribution":
        return f"Show {key}'s contribution"
    return label
