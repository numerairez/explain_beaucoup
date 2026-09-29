"""The Qt <-> Vega seam.

One QWebEngineView hosts a *stack* of charts: the node in focus on top, the
views drilled out of it underneath. A QWebChannel carries structured mark
selections, and the panel-chrome actions, back into Python. Vega renders and
emits; it never decides what an interaction means.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QFile, QIODevice, QObject, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import QWebEngineScript, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QVBoxLayout, QWidget

WEB_DIR = Path(__file__).parent / "web"
PAGE = WEB_DIR / "chart.html"


class Bridge(QObject):
    """Receives the structured selection payload from the chart."""

    selected = pyqtSignal(dict)
    menu_requested = pyqtSignal(dict)
    activated = pyqtSignal(dict)
    panel_action = pyqtSignal(dict)
    page_ready = pyqtSignal()
    error = pyqtSignal(str)

    @pyqtSlot()
    def ready(self) -> None:
        self.page_ready.emit()

    @pyqtSlot(str)
    def mark_selected(self, payload: str) -> None:
        self.selected.emit(json.loads(payload))

    @pyqtSlot(str)
    def mark_menu(self, payload: str) -> None:
        self.menu_requested.emit(json.loads(payload))

    @pyqtSlot(str)
    def mark_activated(self, payload: str) -> None:
        self.activated.emit(json.loads(payload))

    @pyqtSlot(str)
    def panel_action_requested(self, payload: str) -> None:
        self.panel_action.emit(json.loads(payload))

    @pyqtSlot(str)
    def report_error(self, message: str) -> None:
        self.error.emit(message)


def _qwebchannel_js() -> str:
    f = QFile(":/qtwebchannel/qwebchannel.js")
    if not f.open(QIODevice.OpenModeFlag.ReadOnly):
        return ""
    try:
        return bytes(f.readAll()).decode("utf-8")
    finally:
        f.close()


class ChartView(QWidget):
    """The chart surface: a vertical stack of chart panels.

    The node in focus renders full size on top; the views drilled out of it
    render compact underneath, so a drill-down *adds* a chart rather than
    replacing the one it came from.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.bridge = Bridge(self)
        self._pending: dict[str, Any] | None = None
        self._ready = False

        self.web = QWebEngineView(self)
        # The analytical framework owns the context menu, not the browser.
        from PyQt6.QtCore import Qt
        self.web.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)

        page = self.web.page()
        s = page.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        # The stack genuinely scrolls once a few charts are open, so the
        # scrollbar is a needed affordance rather than chrome.
        s.setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.FocusOnNavigationEnabled, False)

        src = _qwebchannel_js()
        if src:
            script = QWebEngineScript()
            script.setName("qwebchannel")
            script.setSourceCode(src)
            script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
            script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
            script.setRunsOnSubFrames(False)
            page.scripts().insert(script)

        channel = QWebChannel(page)
        channel.registerObject("bridge", self.bridge)
        page.setWebChannel(channel)

        self.bridge.page_ready.connect(self._on_ready)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.web)

        self.web.load(QUrl.fromLocalFile(str(PAGE)))

    # -- rendering ---------------------------------------------------------

    def _on_ready(self) -> None:
        self._ready = True
        if self._pending is not None:
            payload, self._pending = self._pending, None
            self.render_stack(payload)

    def render_stack(self, payload: dict[str, Any]) -> None:
        """Render the whole stack.

        `payload` is {"theme": str, "scroll_to": uid|None, "panels": [panel]},
        where a panel is {"uid", "context_id", "role", "title", "subtitle",
        "badge", "active", "actions": [{"action", "label", "hint", "primary"}],
        "spec"}.  The page reuses the Vega view of any panel whose spec has
        not changed, so re-rendering the stack does not rebuild every chart.
        """
        if not self._ready:
            self._pending = payload
            return
        js = f"window.renderStack({json.dumps(json.dumps(payload))});"
        self.web.page().runJavaScript(js)

    def set_page_theme(self, theme: str) -> None:
        self.web.page().runJavaScript(
            f"document.body.dataset.theme = {json.dumps(theme)};")
