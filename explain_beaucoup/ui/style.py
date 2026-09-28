"""Qt stylesheet generated from the same design tokens the charts use."""

from __future__ import annotations

from ..view.theme import Theme

FONT = 'system-ui, -apple-system, "Segoe UI", Helvetica, Arial, sans-serif'


def stylesheet(t: Theme) -> str:
    hover = "rgba(255,255,255,0.06)" if t.is_dark else "rgba(11,11,11,0.05)"
    press = "rgba(255,255,255,0.10)" if t.is_dark else "rgba(11,11,11,0.09)"
    return f"""
    QWidget {{
        background: {t.plane};
        color: {t.ink};
        font-family: {FONT};
        font-size: 12.5px;
    }}
    QMainWindow::separator {{ background: {t.border}; width: 1px; height: 1px; }}

    QToolBar {{
        background: {t.surface};
        border-bottom: 1px solid {t.border};
        padding: 6px 8px; spacing: 6px;
    }}
    QToolBar QLabel {{ color: {t.muted}; padding: 0 2px 0 8px; }}

    QDockWidget {{ titlebar-close-icon: none; titlebar-normal-icon: none; }}
    QDockWidget::title {{
        background: {t.plane};
        color: {t.muted};
        padding: 8px 10px;
        border-bottom: 1px solid {t.border};
        font-size: 11px;
        text-transform: uppercase;
    }}

    QPushButton, QToolButton {{
        background: {t.surface};
        border: 1px solid {t.border};
        border-radius: 6px;
        padding: 5px 10px;
        color: {t.ink};
    }}
    QPushButton:hover, QToolButton:hover {{ background: {hover}; }}
    QPushButton:pressed, QToolButton:pressed {{ background: {press}; }}
    QPushButton:disabled, QToolButton:disabled {{ color: {t.muted}; }}
    QToolButton::menu-indicator {{ image: none; }}

    QComboBox {{
        background: {t.surface};
        border: 1px solid {t.border};
        border-radius: 6px;
        padding: 4px 8px;
        min-height: 20px;
        color: {t.ink};
    }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    QComboBox QAbstractItemView {{
        background: {t.surface};
        border: 1px solid {t.border};
        selection-background-color: {t.series[0]};
        selection-color: #ffffff;
        outline: none;
    }}

    QMenu {{
        background: {t.surface};
        border: 1px solid {t.border};
        border-radius: 8px;
        padding: 6px;
    }}
    QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 5px; }}
    QMenu::item:selected {{ background: {t.series[0]}; color: #ffffff; }}
    QMenu::separator {{ height: 1px; background: {t.border}; margin: 5px 8px; }}
    QMenu::item:disabled {{ color: {t.muted}; }}

    QTreeWidget, QListWidget {{
        background: {t.surface};
        border: none;
        outline: none;
        alternate-background-color: {t.plane};
    }}
    QTreeWidget::item, QListWidget::item {{ padding: 4px 2px; border-radius: 5px; }}
    QTreeWidget::item:selected, QListWidget::item:selected {{
        background: {t.series[0]}; color: #ffffff;
    }}
    QTreeWidget::item:hover, QListWidget::item:hover {{ background: {hover}; }}

    QTabWidget::pane {{ border: none; border-top: 1px solid {t.border}; }}
    QTabBar::tab {{
        background: transparent; color: {t.muted};
        padding: 7px 12px; border: none; margin-right: 2px;
    }}
    QTabBar::tab:selected {{
        color: {t.ink};
        border-bottom: 2px solid {t.series[0]};
    }}
    QTabBar::tab:hover {{ color: {t.ink}; }}

    QTextBrowser, QTextEdit {{
        background: {t.surface};
        border: none;
        color: {t.ink};
        selection-background-color: {t.series[0]};
    }}
    QScrollArea {{ border: none; background: {t.surface}; }}
    QScrollBar:vertical {{
        background: transparent; width: 10px; margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {t.axis}; border-radius: 5px; min-height: 28px;
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{
        background: {t.axis}; border-radius: 5px; min-width: 28px;
    }}

    QStatusBar {{
        background: {t.surface};
        border-top: 1px solid {t.border};
        color: {t.muted};
        font-size: 11px;
    }}
    QStatusBar::item {{ border: none; }}
    QSplitter::handle {{ background: {t.border}; }}
    QLineEdit {{
        background: {t.surface}; border: 1px solid {t.border};
        border-radius: 6px; padding: 5px 8px; color: {t.ink};
    }}
    QToolTip {{
        background: {t.surface}; color: {t.ink};
        border: 1px solid {t.border}; padding: 5px 7px;
    }}
    """
