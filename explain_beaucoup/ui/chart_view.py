"""The Qt <-> Vega seam.

QWebEngineView hosts the Vega renderer; a QWebChannel carries structured mark
selections back into Python. Vega renders and emits; it never decides what an
interaction means.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PyQt5.QtCore import QFile, QIODevice, QObject, QUrl, pyqtSignal, pyqtSlot
from PyQt5.QtWebChannel import QWebChannel
from PyQt5.QtWebEngineWidgets import (QWebEngineScript, QWebEngineSettings,
                                      QWebEngineView)
from PyQt5.QtWidgets import QVBoxLayout, QWidget

WEB_DIR = Path(__file__).parent / "web"
PAGE = WEB_DIR / "chart.html"


class Bridge(QObject):
    """Receives the structured selection payload from the chart."""

    selected = pyqtSignal(dict)
    menu_requested = pyqtSignal(dict)
    activated = pyqtSignal(dict)
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
    """The chart surface. Renders a Vega-Lite spec and reports selections."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.bridge = Bridge(self)
        self._pending: tuple[dict[str, Any], str, str, str] | None = None
        self._ready = False

        self.web = QWebEngineView(self)
        # The analytical framework owns the context menu, not the browser.
        from PyQt5.QtCore import Qt
        self.web.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)

        page = self.web.page()
        s = page.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, False)
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
        if self._pending:
            self.render_spec(*self._pending)
            self._pending = None

    def render_spec(self, spec: dict[str, Any], view_id: str,
                    context_id: str, theme: str) -> None:
        if not self._ready:
            self._pending = (spec, view_id, context_id, theme)
            return
        js = (f"window.renderSpec({json.dumps(json.dumps(spec))}, "
              f"{json.dumps(view_id)}, {json.dumps(context_id)}, "
              f"{json.dumps(theme)});")
        self.web.page().runJavaScript(js)

    def set_page_theme(self, theme: str) -> None:
        self.web.page().runJavaScript(
            f"document.body.dataset.theme = {json.dumps(theme)};")
