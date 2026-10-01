"""The analytical workspace window.

Event loop (design plan section 9):
    select mark -> emit structured selection -> resolve Context
    -> advertise valid operations -> execute deterministic operation
    -> add graph node -> render the resulting view

Every analytical decision in that loop - where a result lands, which chart
kind it is read as, what the menu offers - belongs to `core/session.py`. This
window forwards gestures to the session and renders what it says.

The chart surface is a *stack*, not a slot. The node in focus renders full
size on top; the views drilled out of it render compact underneath, so a
drill-down never destroys the chart it came from. Raising a child moves it to
the top, where it in turn spawns its own children.
"""

from __future__ import annotations

from typing import Any, Callable

import pandas as pd
from PyQt6.QtCore import QMimeData, QPoint, Qt
from PyQt6.QtGui import QAction, QGuiApplication, QKeySequence
from PyQt6.QtWidgets import (QComboBox, QDockWidget, QFileDialog, QHBoxLayout,
                             QLabel, QMainWindow, QMenu, QMessageBox,
                             QTabWidget, QTextBrowser, QToolBar, QToolButton,
                             QVBoxLayout, QWidget)

from ..core import timegrain as tg
from ..core.graph import InvestigationGraph, Node
from ..core.session import LENSES, Landing, MenuItem, Session
from ..data.source import DataSource
from ..engine.commentary import TIMELINE, TREE, Commentary
from ..engine.engine import AnalyticalEngine
from ..engine.explain import Evidence, ExplanationResult
from ..engine.narrate import (narrate_explanation, narrate_investigation,
                              suggested_questions)
from ..semantic.specs import SemanticError, SemanticModel
from ..view.stack import stack
from ..view.theme import LIGHT, THEMES, Theme
from .chart_view import ChartView
from .panels import (BreadcrumbBar, ContextInspector, ExplainPanel,
                     InvestigationTree, PinsPanel, QuestionsPanel)
from .style import stylesheet


class MainWindow(QMainWindow):
    def __init__(self, data: DataSource | pd.DataFrame,
                 model: SemanticModel, *, scope_label: str = "") -> None:
        super().__init__()
        # Set when the data was narrowed before the window opened (see
        # `semantic/catalog.py`). Every total here is a total of that subset, so
        # it stays on screen rather than being inferred from the title.
        self.scope_label = scope_label
        self.session = Session(data, model)
        self.theme_name = "light"
        self._syncing = False

        title = model.title or model.dataset.replace("_", " ").title()
        self.setWindowTitle(f"{title} - Explain Beaucoup")
        self.resize(1560, 940)

        self._build_chrome()
        self._build_docks()
        self._apply_theme(self.theme_name)

        self.session.journal_listeners.append(
            lambda _entry: self._refresh_commentary())
        self._refresh_commentary()
        self._render(scroll_to=self.session.node.uid)
        self.explain_panel.show_placeholder()

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------

    @property
    def model(self) -> SemanticModel:
        return self.session.model

    @property
    def engine(self) -> AnalyticalEngine:
        return self.session.engine

    @property
    def graph(self) -> InvestigationGraph:
        return self.session.graph

    @property
    def commentary(self) -> Commentary:
        return self.session.commentary

    @property
    def theme(self) -> Theme:
        return THEMES[self.theme_name]

    @property
    def node(self) -> Node:
        return self.session.node

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
        self.chart.bridge.panel_action.connect(self._on_panel_action)
        self.chart.bridge.error.connect(
            lambda msg: self.statusBar().showMessage(f"Chart error: {msg}", 8000))
        box.addWidget(self.chart, 1)
        self.setCentralWidget(centre)
        if self.scope_label:
            note = QLabel(f"Scope: {self.scope_label}")
            note.setToolTip(
                "The data was narrowed before this session opened. Every "
                "total, share and baseline here is computed against that "
                "subset — reopen the picker to change it.")
            self.statusBar().addPermanentWidget(note)
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
        self.right_tabs.addTab(self._build_commentary(), "Commentary")
        self.right_tabs.addTab(self.questions, "Next")
        self.right_tabs.addTab(self.inspector, "Context")
        self.right_tabs.addTab(self.report, "Report")
        right.setWidget(self.right_tabs)
        right.setMinimumWidth(372)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, right)

    # ------------------------------------------------------------------
    # forwarding to the session
    # ------------------------------------------------------------------

    def _do(self, move: Callable[[], Any]) -> None:
        """Run a session move and render whatever it produced.

        A refusal changes nothing; it says why. The session has already
        written the dead end to the journal.
        """
        try:
            out = move()
        except SemanticError as exc:
            self._blocked(str(exc))
            return
        if isinstance(out, ExplanationResult):
            self._show_explanation(out)
        elif isinstance(out, Landing):
            self._render(scroll_to=out.node.uid)
            if out.message:
                self.statusBar().showMessage(out.message, 6000)
        elif isinstance(out, str):
            self._render(scroll_to=self.node.uid)
            self.statusBar().showMessage(out, 5000)

    def goto_node(self, uid: str) -> None:
        """Raise a node to the top of the stack."""
        if uid not in self.graph.nodes:
            return
        self._do(lambda: Landing(self.session.goto(uid), True, False))

    def raise_node(self, uid: str) -> None:
        self._do(lambda: self.session.raise_node(uid))

    def close_node(self, uid: str) -> None:
        self._do(lambda: self.session.close(uid))

    def go_back(self) -> None:
        if not self.node.parent:
            self.statusBar().showMessage("Already at the root of this path", 3000)
            return
        self._do(self.session.back)

    def run_op(self, verb: str, params: dict[str, Any], *,
               member: str | None = None, branch: bool = False,
               source: str = "ui") -> None:
        self._do(lambda: self.session.run(verb, params, member=member,
                                          branch=branch, source=source))

    def branch_here(self) -> None:
        """Open a competing hypothesis beside the current path."""
        alts = self.session.branch_options()
        if not alts:
            self.statusBar().showMessage(
                "No alternative dimension available to branch on", 4000)
            return
        menu = QMenu(self)
        menu.addSection("Branch into a competing hypothesis")
        for dim in alts:
            act = menu.addAction(f"Break down by {self.model.label_of(dim)}")
            act.triggered.connect(
                lambda _=False, d=dim: self._do(lambda: self.session.branch(d)))
        menu.exec(self.mapToGlobal(QPoint(self.width() // 2, 120)))

    def pin_here(self) -> None:
        self._do(self.session.toggle_pin)

    def do_explain(self, *, member: str | None = None) -> None:
        self._do(lambda: self.session.explain(member=member))

    def _show_explanation(self, exp: ExplanationResult) -> None:
        self.explain_panel.show_explanation(self.model, exp)
        self.right_tabs.setCurrentIndex(0)
        self.report.setMarkdown(narrate_explanation(self.model, exp))
        self.statusBar().showMessage(
            f"Explained {exp.context.id} - {len(exp.evidence)} pieces of "
            f"evidence from {len(exp.provenance)} governed queries", 6000)

    def _on_evidence(self, ev: Evidence) -> None:
        self._do(lambda: self.session.open_evidence(ev))

    # ------------------------------------------------------------------
    # chart interaction
    # ------------------------------------------------------------------

    def _on_mark_selected(self, payload: dict) -> None:
        key = payload.get("mark", {}).get("key")
        uid = str(payload.get("view_id") or self.graph.current)
        self.session.select(uid, str(key) if key is not None else None)
        self._render(rebuild_side=False)
        if key:
            summary = self.session.mark_summary(uid, str(key))
            if summary:
                self.statusBar().showMessage(
                    summary + "   (right-click for operations)")

    def _on_mark_activated(self, payload: dict) -> None:
        """Double-click = the most natural drill from here, opened below."""
        key = payload.get("mark", {}).get("key")
        if not key:
            return
        uid = str(payload.get("view_id") or self.graph.current)
        self._do(lambda: self.session.activate(uid, str(key)))

    def _on_mark_menu(self, payload: dict) -> None:
        """Show the session's menu for the mark under the cursor."""
        key = payload.get("mark", {}).get("key")
        uid = str(payload.get("view_id") or "")
        if uid not in self.graph.nodes:
            uid = self.graph.current or ""
        spec = self.session.menu(uid, str(key) if key is not None else None)

        menu = QMenu(self)
        menu.addSection(spec.heading)
        if spec.note:
            menu.addSection(spec.note)
        for i, group in enumerate(spec.groups):
            if i:
                menu.addSeparator()
            subs: dict[str, QMenu] = {}
            for item in group:
                target = menu
                if item.submenu:
                    if item.submenu not in subs:
                        subs[item.submenu] = menu.addMenu(item.submenu)
                    target = subs[item.submenu]
                act = target.addAction(item.label)
                if item.hint:
                    act.setToolTip(item.hint)
                act.triggered.connect(
                    lambda _=False, it=item: self._on_menu_item(uid, it))

        pos = self.chart.mapToGlobal(QPoint(int(payload.get("x", 0)),
                                            int(payload.get("y", 0))))
        menu.exec(pos)

    def _on_menu_item(self, uid: str, item: MenuItem) -> None:
        if item.action == "branch":
            self.branch_here()
            return
        self._do(lambda: self.session.invoke(uid, item))

    def _on_panel_action(self, payload: dict) -> None:
        uid = str(payload.get("uid") or "")
        action = payload.get("action")
        node = self.graph.nodes.get(uid)
        if node is None:
            return
        if action == "raise":
            self.raise_node(uid)
        elif action == "close":
            self.close_node(uid)
        elif action == "up" and node.parent:
            self.raise_node(node.parent)
        elif action == "explain":
            if uid != self.graph.current:
                self.raise_node(uid)
            self.do_explain(member=self.session.selected(uid))
        elif action == "pin":
            self._do(lambda: self.session.toggle_pin(uid))

    # ------------------------------------------------------------------
    # toolbar handlers
    # ------------------------------------------------------------------

    def _on_metric(self) -> None:
        if self._syncing:
            return
        name = self.metric_box.currentData()
        if name:
            self._do(lambda: self.session.set_metric(name))

    def _reload_periods(self, grain: str) -> None:
        """Repopulate the period picker for a calendar grain."""
        self._grain = grain
        self._periods = self.session.periods(grain)
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
        if key:
            self._do(lambda: self.session.set_period(key))

    def _on_grain(self) -> None:
        if self._syncing:
            return
        grain = self.grain_box.currentData()
        if grain:
            self._do(lambda: self.session.set_time_grain(grain))

    def _on_comparison(self) -> None:
        if self._syncing:
            return
        cmp_ = self.cmp_box.currentData()
        self._do(lambda: self.session.set_comparison(cmp_))

    def _on_lens(self) -> None:
        if self._syncing:
            return
        lens = self.lens_box.currentData()
        self._do(lambda: self.session.lens(lens))

    def toggle_theme(self) -> None:
        self.theme_name = "dark" if self.theme_name == "light" else "light"
        self.act_theme.setText("Light mode" if self.theme_name == "dark"
                               else "Dark mode")
        self._apply_theme(self.theme_name)
        # Panels hold token colours, so rebuild them against the new theme.
        self._build_docks_refresh()
        self._refresh_commentary()
        self._render()

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
        text = (narrate_investigation(self.model, self.engine, self.graph)
                + "\n\n" + self.commentary.markdown(
                    live=set(), heading="## Commentary", layout=TREE) + "\n")
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

    def _render(self, *, rebuild_side: bool = True,
                scroll_to: str | None = None) -> None:
        """Draw the session as it stands: the stack, crumbs, toolbar, side."""
        node = self.node
        try:
            res = self.session.result(node)
            self.chart.render_stack(
                stack(self.session, self.theme_name, scroll_to=scroll_to))
        except SemanticError as exc:              # pragma: no cover
            self._blocked(str(exc))
            return
        self.crumbs.set_path(self.model, self.graph.path_to(node.uid),
                             self.graph.siblings(node.uid))
        self._sync_toolbar(node)
        if rebuild_side:
            self._refresh_side()
            self.inspector.show_context(self.model, node, res)
            self.questions.set_questions(suggested_questions(self.model, node))
            self.report.setMarkdown(
                narrate_investigation(self.model, self.engine, self.graph, node))
        self.statusBar().showMessage(self.session.status(node))

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
        lens = self.session.lens_of(node)
        if lens:
            i = self.lens_box.findData(lens)
            if i >= 0:
                self.lens_box.setCurrentIndex(i)
        self.act_up.setEnabled(bool(ctx.grain))
        self.act_back.setEnabled(bool(node.parent))
        self._syncing = False

    # -- commentary --------------------------------------------------------

    def _build_commentary(self) -> QWidget:
        """The commentary tab: the story so far, nested like the map."""
        host = QWidget()
        box = QVBoxLayout(host)
        box.setContentsMargins(0, 6, 0, 0)
        box.setSpacing(4)
        row = QHBoxLayout()
        row.setContentsMargins(10, 0, 10, 0)
        row.addWidget(QLabel("Layout"))
        self.commentary_layout = QComboBox()
        self.commentary_layout.addItem("Tree", TREE)
        self.commentary_layout.addItem("Timeline", TIMELINE)
        self.commentary_layout.setToolTip(
            "Tree nests each move under the chart it was made from, like the "
            "map. Timeline lists every move in the order it was made.")
        self.commentary_layout.currentIndexChanged.connect(
            lambda _i: self._refresh_commentary())
        row.addWidget(self.commentary_layout)
        # Click copies the formatted version; the arrow offers the others.
        copy = QToolButton()
        copy.setText("Copy")
        copy.setToolTip("Copy the commentary - formatted for documents and "
                        "email (markdown where formatting is not accepted). "
                        "The arrow offers plain text or nested JSON.")
        copy.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        copy.clicked.connect(lambda: self.copy_commentary("formatted"))
        menu = QMenu(copy)
        for fmt_, label in (("formatted", "Formatted (docs, email, markdown)"),
                            ("plain", "Plain text"),
                            ("json", "JSON (nested)")):
            menu.addAction(label).triggered.connect(
                lambda _=False, f=fmt_: self.copy_commentary(f))
        copy.setMenu(menu)
        self.commentary_copy = copy
        row.addWidget(copy)
        # Each beat has a remove link; this brings them all back.
        restore = QToolButton()
        restore.setText("Restore all")
        restore.setToolTip("Bring back every step removed from the "
                           "commentary.")
        restore.clicked.connect(lambda: self._restore_commentary(None))
        restore.setVisible(False)
        self.commentary_restore = restore
        row.addWidget(restore)
        row.addStretch(1)
        box.addLayout(row)
        self.commentary_view = QTextBrowser()
        self.commentary_view.setOpenLinks(False)
        self.commentary_view.anchorClicked.connect(self._on_commentary_link)
        box.addWidget(self.commentary_view, 1)
        return host

    def _refresh_commentary(self, *, follow: bool = True) -> None:
        view = self.commentary_view
        # Editing keeps the reader where they were; a new move follows it.
        scroll = view.verticalScrollBar().value()
        layout = self.commentary_layout.currentData() or TREE
        view.setHtml(self.commentary.html(
            self.theme, live=set(self.graph.nodes), layout=layout,
            editable=True))
        removed = len(self.commentary.hidden)
        self.commentary_restore.setVisible(bool(removed))
        self.commentary_restore.setText(f"Restore all ({removed})")
        # The newest beat can sit mid-tree, so follow it rather than the end.
        entries = self.session.journal.entries
        if not follow:
            view.verticalScrollBar().setValue(scroll)
        elif entries:
            view.scrollToAnchor(f"s{entries[-1].seq}")

    def copy_commentary(self, fmt_: str = "formatted") -> None:
        """Put the commentary on the clipboard.

        formatted  rich text for editors that take it (docs, email), with
                   markdown alongside for plain-text targets (chat, .md)
        plain      text only, no markup; indentation carries the tree
        json       nested like the map, whatever layout is shown, with each
                   move's analytical state as data

        Chart links only work inside the app, so every copy drops them, and
        the rich copy is always in light colours whatever the app's theme.
        Steps removed in the panel are left out of every format.
        """
        if not self.commentary.beats():
            self.statusBar().showMessage("Nothing to copy yet", 3000)
            return
        layout = self.commentary_layout.currentData() or TREE
        data = QMimeData()
        if fmt_ == "json":
            data.setText(self.commentary.json())
            shape = "nested JSON"
        elif fmt_ == "plain":
            data.setText(self.commentary.plain(layout=layout))
            shape = f"plain text, {self.commentary_layout.currentText().lower()}"
        else:
            data.setText(self.commentary.markdown(live=set(), layout=layout))
            data.setHtml(self.commentary.html(LIGHT, live=set(), layout=layout))
            shape = self.commentary_layout.currentText().lower()
        QGuiApplication.clipboard().setMimeData(data)
        n = len(self.commentary.beats())
        self.statusBar().showMessage(
            f"Copied {n} step{'s' if n != 1 else ''} of commentary "
            f"({shape})", 4000)

    def _on_commentary_link(self, url: Any) -> None:
        scheme = url.scheme()
        if scheme == "node":
            self.raise_node(url.path())
        elif scheme == "hide":
            self.commentary.hide(int(url.path()))
            self._refresh_commentary(follow=False)
        elif scheme == "restore":
            self._restore_commentary(int(url.path()))

    def _restore_commentary(self, seq: int | None) -> None:
        self.commentary.restore(seq)
        self._refresh_commentary(follow=False)

    def _blocked(self, reason: str) -> None:
        """Semantic safety: say why, and change nothing."""
        self.statusBar().showMessage(f"Blocked: {reason}", 8000)
        QMessageBox.information(self, "Operation not available", reason)
