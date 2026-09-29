"""The opening dialog: choose a dataset, narrow it, then lock it in.

Two decisions are made here, and neither belongs in the workspace:

* **Which model.** The choices are the YAML models in a folder, listed straight
  from `semantic/catalog.py` - including the ones that will not load, with the
  reason, because a picker that silently hides a broken model is worse than one
  that explains it.
* **What population.** Filters chosen here narrow the *governed frame*, not a
  Context: every total, share and baseline in the session that follows is
  computed against the subset. That is a different act from the `focus` verb,
  so it happens before the window exists, and the scope is reported back in the
  title afterwards.

Member lists cross-filter as filters are added - picking Luzon stops the next
row offering provinces outside it - and the dialog will not open a scope that
leaves no rows or no dimension to break down by. The engine would refuse it
later; saying so here costs nothing.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QApplication, QComboBox, QDialog,
                             QDialogButtonBox, QFileDialog, QFrame,
                             QHBoxLayout, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QMenu, QMessageBox, QPushButton,
                             QScrollArea, QSizePolicy, QToolButton,
                             QVBoxLayout, QWidget, QWidgetAction)

from ..core import timegrain as tg
from ..data.source import DataSourceError
from ..semantic import catalog
from ..semantic.catalog import CatalogEntry, Scope, ScopeError
from ..semantic.loader import LoadedModel, ModelError, load_model_file
from ..view.theme import Theme
from .style import stylesheet

# A dimension with more members than this is a filter nobody scrolls through.
MAX_MEMBERS = 1000


def _label(text: str, *, color: str, size: float = 12.0, bold: bool = False,
           wrap: bool = True) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(wrap)
    f = lab.font()
    f.setPointSizeF(size)
    f.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    lab.setFont(f)
    lab.setStyleSheet(f"color: {color}; background: transparent; border: none;")
    return lab


def _section(text: str, theme: Theme) -> QLabel:
    lab = _label(text.upper(), color=theme.muted, size=10.5, bold=True)
    lab.setStyleSheet(f"color: {theme.muted}; background: transparent; "
                      f"border: none; letter-spacing: 0.06em;")
    return lab


# --------------------------------------------------------------------------

class MemberPicker(QPushButton):
    """A multi-select over one dimension's members.

    Every member checked - or none - means "no restriction", which is why the
    button reads `All (8)` in both cases: a filter that selects nothing is a
    mistake, not a question, so it is read as the absence of a filter.
    """

    changed = pyqtSignal()

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self._all: list[str] = []
        self._syncing = False
        self._truncated = 0
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(200)
        # Read as a field beside the dimension combo, not as a centred button.
        self.setStyleSheet("text-align: left; padding-left: 9px;")

        menu = QMenu(self)
        holder = QWidget()
        box = QVBoxLayout(holder)
        box.setContentsMargins(8, 8, 8, 8)
        box.setSpacing(6)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Find a value…")
        self._search.textChanged.connect(self._show_matching)
        box.addWidget(self._search)
        self._list = QListWidget()
        self._list.setFixedSize(252, 228)
        self._list.itemChanged.connect(self._on_item)
        box.addWidget(self._list)
        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        for text, state in (("All", True), ("None", False)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, s=state: self._check_visible(s))
            buttons.addWidget(b)
        buttons.addStretch(1)
        box.addLayout(buttons)
        act = QWidgetAction(menu)
        act.setDefaultWidget(holder)
        menu.addAction(act)
        self.setMenu(menu)
        self._sync_text()

    # -- contents ----------------------------------------------------------

    def set_members(self, values: list[str], *,
                    keep: set[str] | None = None) -> None:
        """Repopulate, preserving a selection where the values still exist."""
        self._syncing = True
        self._truncated = max(0, len(values) - MAX_MEMBERS)
        self._all = list(values[:MAX_MEMBERS])
        self._list.clear()
        for v in self._all:
            item = QListWidgetItem(v)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            on = keep is None or v in keep
            item.setCheckState(Qt.CheckState.Checked if on
                               else Qt.CheckState.Unchecked)
            self._list.addItem(item)
        self._syncing = False
        self._show_matching(self._search.text())
        self._sync_text()

    def restriction(self) -> list[str] | None:
        """The members to keep, or None when this dimension is unrestricted."""
        picked = [self._list.item(i).text() for i in range(self._list.count())
                  if self._list.item(i).checkState() == Qt.CheckState.Checked]
        if not picked or len(picked) == len(self._all):
            return None
        return picked

    # -- internals ---------------------------------------------------------

    def _on_item(self, _item: QListWidgetItem) -> None:
        if self._syncing:
            return
        self._sync_text()
        self.changed.emit()

    def _check_visible(self, on: bool) -> None:
        """All / None act on what the search box is showing, so a search plus
        All is how you pick "every region starting with M"."""
        self._syncing = True
        for i in range(self._list.count()):
            item = self._list.item(i)
            if not item.isHidden():
                item.setCheckState(Qt.CheckState.Checked if on
                                   else Qt.CheckState.Unchecked)
        self._syncing = False
        self._sync_text()
        self.changed.emit()

    def _show_matching(self, text: str) -> None:
        needle = text.strip().casefold()
        for i in range(self._list.count()):
            item = self._list.item(i)
            item.setHidden(bool(needle) and needle not in item.text().casefold())

    def _sync_text(self) -> None:
        picked = self.restriction()
        if not self._all:
            self.setText("no values")
            self.setEnabled(False)
            return
        self.setEnabled(True)
        if picked is None:
            self.setText(f"All ({len(self._all)})")
        elif len(picked) <= 2:
            self.setText(", ".join(picked))
        else:
            self.setText(f"{len(picked)} of {len(self._all)}")
        tip = ("every value" if picked is None else ", ".join(picked))
        if self._truncated:
            tip += (f"\n\n{self._truncated:,} further values are not listed — "
                    f"this dimension is too wide to pick through.")
        self.setToolTip(tip)


class FilterRow(QFrame):
    """One dimension, one set of members, and a way to drop it again."""

    changed = pyqtSignal()
    removed = pyqtSignal(object)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.dim_box = QComboBox()
        self.dim_box.setMinimumWidth(148)
        self.dim_box.currentIndexChanged.connect(self._on_dim)
        self.members = MemberPicker(theme)
        self.members.changed.connect(self.changed)
        drop = QToolButton()
        drop.setText("✕")
        drop.setToolTip("Remove this filter")
        drop.clicked.connect(lambda: self.removed.emit(self))
        row.addWidget(self.dim_box)
        row.addWidget(self.members, 1)
        row.addWidget(drop)

    @property
    def dimension(self) -> str | None:
        return self.dim_box.currentData()

    def set_dimension_choices(self, choices: list[tuple[str, str]], *,
                              select: str | None = None) -> None:
        """Choices are `(name, label)`; a dimension another row already holds
        is not offered, so two rows can never disagree about one column."""
        want = select or self.dimension
        self.dim_box.blockSignals(True)
        self.dim_box.clear()
        for name, label in choices:
            self.dim_box.addItem(label, name)
        i = self.dim_box.findData(want)
        self.dim_box.setCurrentIndex(max(i, 0))
        self.dim_box.blockSignals(False)

    def _on_dim(self) -> None:
        # A different dimension has different members; nothing carries over.
        self.members.set_members([])
        self.changed.emit()


# --------------------------------------------------------------------------

class DatasetDialog(QDialog):
    """Pick a model, scope it, and hand back a loaded, scoped model."""

    def __init__(self, entries: list[CatalogEntry], *, models_dir: Path,
                 theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.models_dir = models_dir
        self.entries = list(entries)
        self.chosen: LoadedModel | None = None
        self._cache: dict[Path, LoadedModel] = {}
        self._current: LoadedModel | None = None
        self._rows: list[FilterRow] = []
        self._syncing = False

        self.setWindowTitle("Open a dataset — Explain Beaucoup")
        self.setStyleSheet(stylesheet(theme))
        self.setMinimumSize(1000, 660)
        self._build()
        self._reload_list(select=0)

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        t = self.theme
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(14)

        outer.addWidget(_label("Open a dataset", color=t.ink, size=17,
                               bold=True))
        self.where = _label("", color=t.muted, size=11.5)
        outer.addWidget(self.where)

        body = QHBoxLayout()
        body.setSpacing(18)
        outer.addLayout(body, 1)

        self.list = QListWidget()
        self.list.setFixedWidth(312)
        self.list.currentRowChanged.connect(lambda _: self._on_entry())
        self.list.itemDoubleClicked.connect(lambda _: self.accept())
        body.addWidget(self.list)

        right = QScrollArea()
        right.setWidgetResizable(True)
        pane = QWidget()
        self.pane = QVBoxLayout(pane)
        self.pane.setContentsMargins(0, 0, 4, 0)
        self.pane.setSpacing(10)
        right.setWidget(pane)
        body.addWidget(right, 1)
        self._build_pane()

        footer = QHBoxLayout()
        browse = QPushButton("Browse…")
        browse.setToolTip("Open a model file from somewhere other than "
                          f"{self.models_dir}")
        browse.clicked.connect(self._browse)
        footer.addWidget(browse)
        footer.addStretch(1)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel)
        self.open_btn = self.buttons.addButton(
            "Open workspace", QDialogButtonBox.ButtonRole.AcceptRole)
        # The label grows to "Open scoped workspace" once a filter is set, and
        # the box will not re-widen a button it has already laid out.
        self.open_btn.setMinimumWidth(
            self.open_btn.fontMetrics().horizontalAdvance(
                "Open scoped workspace") + 36)
        self.open_btn.setDefault(True)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        footer.addWidget(self.buttons)
        outer.addLayout(footer)

    def _build_pane(self) -> None:
        t = self.theme
        self.head = _label("", color=t.ink, size=14.5, bold=True)
        self.path_lab = _label("", color=t.muted, size=11)
        self.meta = _label("", color=t.ink_2, size=12)
        self.shape = _label("", color=t.ink_2, size=12)
        for w in (self.head, self.path_lab, self.meta, self.shape):
            self.pane.addWidget(w)

        self.scope_head = _section("Scope — applied before the session opens", t)
        self.pane.addWidget(self.scope_head)
        self.scope_note = _label(
            "Everything the workspace computes — totals, shares, baselines, "
            "explanations — is computed against what you leave here. Leave it "
            "alone to analyse the whole table.", color=t.muted, size=11)
        self.pane.addWidget(self.scope_note)

        period = QHBoxLayout()
        period.setSpacing(6)
        period.addWidget(_label("Period", color=t.muted, size=11.5, wrap=False))
        self.start_box = QComboBox()
        self.end_box = QComboBox()
        for box in (self.start_box, self.end_box):
            box.setMinimumWidth(126)
            box.currentIndexChanged.connect(self._on_period)
        period.addWidget(self.start_box)
        period.addWidget(_label("to", color=t.muted, size=11.5, wrap=False))
        period.addWidget(self.end_box)
        period.addStretch(1)
        self.period_row = QWidget()
        self.period_row.setLayout(period)
        self.pane.addWidget(self.period_row)

        self.filters_box = QVBoxLayout()
        self.filters_box.setSpacing(6)
        holder = QWidget()
        holder.setLayout(self.filters_box)
        self.pane.addWidget(holder)

        controls = QHBoxLayout()
        controls.setSpacing(6)
        self.add_btn = QPushButton("+ Add filter")
        self.add_btn.clicked.connect(lambda: self._add_row())
        self.reset_btn = QPushButton("Reset scope")
        self.reset_btn.clicked.connect(self._reset_scope)
        controls.addWidget(self.add_btn)
        controls.addWidget(self.reset_btn)
        controls.addStretch(1)
        row = QWidget()
        row.setLayout(controls)
        self.pane.addWidget(row)

        self.status = _label("", color=t.ink, size=12, bold=True)
        self.issues = _label("", color=t.muted, size=11.5)
        self.pane.addWidget(self.status)
        self.pane.addWidget(self.issues)
        self.pane.addStretch(1)

    # -- the model list ----------------------------------------------------

    def _reload_list(self, *, select: int = 0) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for entry in self.entries:
            item = QListWidgetItem(f"{entry.name}\n{entry.summary()}")
            item.setData(Qt.ItemDataRole.UserRole, entry)
            if not entry.ok:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                item.setToolTip(entry.problem)
            else:
                item.setToolTip(str(entry.path))
            self.list.addItem(item)
        self.list.blockSignals(False)
        n = sum(1 for e in self.entries if e.ok)
        broken = len(self.entries) - n
        where = f"{n} model{'' if n == 1 else 's'} in {self.models_dir}"
        if broken:
            where += f"   ·   {broken} that will not load"
        self.where.setText(where)
        if not self.entries:
            self.where.setText(
                f"No models in {self.models_dir}. Scaffold one with "
                f"`explain-beaucoup init <your data file>`, or Browse…")
        # `discover` sorts usable models first, so row 0 is a working model
        # whenever one exists. A row asked for explicitly is honoured even when
        # it is broken - that is how Browse… reports why a file will not open.
        in_range = 0 <= select < self.list.count()
        self.list.setCurrentRow(
            select if in_range
            else next((i for i, e in enumerate(self.entries) if e.ok), -1))
        self._on_entry()

    @property
    def entry(self) -> CatalogEntry | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _load(self, entry: CatalogEntry) -> LoadedModel | None:
        cached = self._cache.get(entry.path)
        if cached is not None:
            return cached
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            loaded = load_model_file(entry.path)
        except (ModelError, DataSourceError) as exc:
            self._fail("Cannot open this model",
                       f"{entry.path.name} could not be opened: {exc}")
            return None
        finally:
            QApplication.restoreOverrideCursor()
        self._cache[entry.path] = loaded
        return loaded

    def _on_entry(self) -> None:
        entry = self.entry
        self._clear_rows()
        if entry is None:
            self._current = None
            self._fail("No dataset selected", "Choose a model on the left, or "
                       "Browse… for one outside this folder.")
            return
        if not entry.ok:
            self._current = None
            self._fail("Cannot open this model",
                       f"{entry.path.name} — {entry.problem}")
            return
        loaded = self._load(entry)
        if loaded is None:
            self._current = None
            return
        self._current = loaded
        model, source = loaded.model, loaded.source

        self.head.setText(model.title or model.dataset)
        self.path_lab.setText(str(entry.path))
        span = source.span(model.time_grain)
        periods = source.periods(model.time_grain)
        self.meta.setText(
            f"{source.row_count:,} rows   ·   {len(periods)} "
            f"{model.time_grain}s, {tg.label_of(span[0], model.time_grain)} to "
            f"{tg.label_of(span[1], model.time_grain)}   ·   grains: "
            f"{', '.join(model.grains)}")
        hier = "; ".join(f"{label} ({' → '.join(levels)})"
                         for label, levels in entry.hierarchies) or "none declared"
        self.shape.setText(
            f"Metrics: {', '.join(label for _, label in entry.metrics)}\n"
            f"Dimensions: {', '.join(label for _, label in entry.dimensions)}\n"
            f"Drill paths: {hier}")

        self._syncing = True
        for box in (self.start_box, self.end_box):
            box.clear()
            for key in periods:
                box.addItem(tg.label_of(key, model.time_grain), key)
        self.start_box.setCurrentIndex(0)
        self.end_box.setCurrentIndex(len(periods) - 1)
        self._syncing = False
        self._set_scope_enabled(True)
        self._refresh()

    def _fail(self, headline: str, message: str) -> None:
        self.head.setText(headline)
        self.path_lab.setText("")
        self.meta.setText(message)
        self.shape.setText("")
        self.status.setText("")
        self.issues.setText("")
        self._set_scope_enabled(False)
        self.open_btn.setEnabled(False)

    def _set_scope_enabled(self, on: bool) -> None:
        for w in (self.scope_head, self.scope_note, self.period_row,
                  self.add_btn, self.reset_btn):
            w.setVisible(on)

    # -- scope -------------------------------------------------------------

    def _breakdown_dimensions(self) -> list[tuple[str, str]]:
        """Dimensions worth filtering on: the ones with something to choose
        between in the unscoped data."""
        model = self._current.model if self._current else None
        if model is None:
            return []
        return [(name, d.label) for name, d in model.dimensions.items()
                if model.member_counts.get(name, 0) > 1]

    def _choices_for(self, row: FilterRow | None) -> list[tuple[str, str]]:
        taken = {r.dimension for r in self._rows if r is not row}
        return [(n, label) for n, label in self._breakdown_dimensions()
                if n not in taken]

    def _add_row(self) -> None:
        choices = self._choices_for(None)
        if not choices:
            self.issues.setText("Every dimension already has a filter.")
            return
        row = FilterRow(self.theme)
        row.changed.connect(self._refresh)
        row.removed.connect(self._drop_row)
        self._rows.append(row)
        self.filters_box.addWidget(row)
        row.set_dimension_choices(choices, select=choices[0][0])
        self._refresh()

    def _drop_row(self, row: FilterRow) -> None:
        self._rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        self._refresh()

    def _clear_rows(self) -> None:
        for row in list(self._rows):
            self._rows.remove(row)
            row.setParent(None)
            row.deleteLater()

    def _reset_scope(self) -> None:
        self._clear_rows()
        self._syncing = True
        self.start_box.setCurrentIndex(0)
        self.end_box.setCurrentIndex(self.end_box.count() - 1)
        self._syncing = False
        self._refresh()

    def _on_period(self) -> None:
        if self._syncing:
            return
        # A range that runs backwards is a slip, not a question: push the other
        # end along rather than reporting an empty scope.
        self._syncing = True
        if self.start_box.currentIndex() > self.end_box.currentIndex():
            sender = self.sender()
            if sender is self.start_box:
                self.end_box.setCurrentIndex(self.start_box.currentIndex())
            else:
                self.start_box.setCurrentIndex(self.end_box.currentIndex())
        self._syncing = False
        self._refresh()

    def _scope(self, *, excluding: FilterRow | None = None) -> Scope:
        filters: dict[str, list[str]] = {}
        for row in self._rows:
            if row is excluding:
                continue
            dim, picked = row.dimension, row.members.restriction()
            if dim and picked:
                filters[dim] = picked
        start = self.start_box.currentData()
        end = self.end_box.currentData()
        # Only say "from the first period" when it is not the whole span.
        if self.start_box.currentIndex() <= 0:
            start = None
        if self.end_box.currentIndex() >= self.end_box.count() - 1:
            end = None
        return Scope.of(filters, start=start, end=end)

    def _refresh(self) -> None:
        if self._syncing or self._current is None:
            return
        self._syncing = True
        try:
            for row in self._rows:
                row.set_dimension_choices(self._choices_for(row))
                dim = row.dimension
                if not dim:
                    continue
                keep = row.members.restriction()
                frame = catalog.frame_under(self._current,
                                            self._scope(excluding=row))
                column = self._current.model.dim(dim).column
                row.members.set_members(catalog.members_of(frame, column),
                                        keep=set(keep) if keep else None)
        finally:
            self._syncing = False
        self._show_preview()

    def _show_preview(self) -> None:
        loaded = self._current
        if loaded is None:
            return
        model = loaded.model
        scope = self._scope()
        p = catalog.preview(loaded, scope)
        if p.rows:
            share = p.rows / max(p.total_rows, 1)
            span = (f"{tg.label_of(p.span[0], model.time_grain)} to "
                    f"{tg.label_of(p.span[1], model.time_grain)}"
                    if p.span else "")
            n = len(p.breakdowns)
            self.status.setText(
                f"{p.rows:,} of {p.total_rows:,} rows ({share:.0%})   ·   "
                f"{p.periods} {model.time_grain}s, {span}   ·   {n} dimension"
                f"{'' if n == 1 else 's'} to break down by")
        else:
            self.status.setText(f"0 of {p.total_rows:,} rows")
        lines = [f"⚠  {m}" for m in p.problems] + [f"·  {m}" for m in p.notes]
        self.issues.setText("\n".join(lines))
        self.issues.setStyleSheet(
            f"color: {self.theme.critical if p.problems else self.theme.muted}; "
            f"background: transparent; border: none;")
        self.open_btn.setEnabled(p.ok)
        self.open_btn.setText("Open workspace" if scope.is_empty
                              else "Open scoped workspace")

    # -- finishing ---------------------------------------------------------

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open a semantic model", str(self.models_dir),
            "Semantic model (*.yaml *.yml);;All files (*)")
        if not path:
            return
        entry = catalog.describe(Path(path))
        known = next((i for i, e in enumerate(self.entries)
                      if e.path == entry.path), None)
        if known is None:
            self.entries.insert(0, entry)
            self._cache.pop(entry.path, None)
            self._reload_list(select=0)
        else:
            self.list.setCurrentRow(known)

    def accept(self) -> None:
        if self._current is None:
            return
        try:
            self.chosen = catalog.apply_scope(self._current, self._scope())
        except ScopeError as exc:
            QMessageBox.information(self, "That scope leaves nothing to "
                                          "analyse", str(exc))
            return
        super().accept()


def choose_dataset(models_dir: Path, theme: Theme) -> LoadedModel | None:
    """Run the picker. None means the person closed it without choosing."""
    dialog = DatasetDialog(catalog.discover(models_dir), models_dir=models_dir,
                           theme=theme)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.chosen
