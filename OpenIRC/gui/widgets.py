"""Small reusable native widgets for the administration console."""
from __future__ import annotations

from datetime import datetime, timezone
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)


def value(obj: Any, name: str, default: Any = "") -> Any:
    return obj.get(name, default) if isinstance(obj, Mapping) else getattr(obj, name, default)


def timestamp(raw: Any) -> str:
    if not raw:
        return "—"
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw, timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    return str(raw)


def duration(seconds: Any) -> str:
    seconds = max(0, int(seconds or 0))
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def display(raw: Any) -> str:
    if raw is None or raw == "":
        return "—"
    if isinstance(raw, bool):
        return "Yes" if raw else "No"
    if isinstance(raw, (list, tuple, set, frozenset)):
        return ", ".join(str(item) for item in raw) or "—"
    return str(raw)


def brand_icon(state: str = "running") -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    color = {"running": "#277756", "stopped": "#536779", "error": "#b34141"}.get(state, "#536779")
    painter.setBrush(QColor(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(5, 6, 54, 43, 12, 12)
    painter.drawPolygon(*[QPoint(x, y) for x, y in [(15, 42), (15, 59), (31, 44)]])
    painter.setPen(QPen(QColor("white"), 4))
    painter.drawLine(26, 16, 22, 39)
    painter.drawLine(40, 16, 36, 39)
    painter.drawLine(17, 24, 46, 24)
    painter.drawLine(16, 33, 45, 33)
    painter.end()
    return QIcon(pix)


class SortItem(QTableWidgetItem):
    def __lt__(self, other: QTableWidgetItem) -> bool:
        left = self.data(Qt.ItemDataRole.UserRole + 1)
        right = other.data(Qt.ItemDataRole.UserRole + 1)
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            return left < right
        return self.text().casefold() < other.text().casefold()


class DataTable(QTableWidget):
    """Read-only sortable table retaining selection by stable object key."""

    def __init__(self, columns: Sequence[tuple[str, str | Callable]], key: str = "id", parent: QWidget | None = None):
        super().__init__(0, len(columns), parent)
        self.columns = columns
        self.key = key
        self.records: dict[str, Any] = {}
        self.setHorizontalHeaderLabels([label for label, _ in columns])
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setSortingEnabled(True)
        self.verticalHeader().hide()
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().setDefaultSectionSize(135)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._filter = ""

    def selected(self) -> Any | None:
        selected = self.selectionModel().selectedRows()
        if not selected:
            return None
        item = self.item(selected[0].row(), 0)
        return self.records.get(str(item.data(Qt.ItemDataRole.UserRole))) if item else None

    def set_records(self, records: Sequence[Any]) -> None:
        old = self.selected()
        old_key = str(value(old, self.key)) if old is not None else None
        scroll = self.verticalScrollBar().value()
        self.setUpdatesEnabled(False)
        self.setSortingEnabled(False)
        self.clearSelection()
        self.records.clear()
        self.setRowCount(len(records))
        for row, record in enumerate(records):
            key = str(value(record, self.key, row))
            self.records[key] = record
            for col, (_, field) in enumerate(self.columns):
                raw = field(record) if callable(field) else value(record, field)
                item = SortItem(display(raw))
                item.setData(Qt.ItemDataRole.UserRole, key)
                item.setData(Qt.ItemDataRole.UserRole + 1, raw if isinstance(raw, (int, float)) else None)
                item.setToolTip(display(raw))
                self.setItem(row, col, item)
        self.setSortingEnabled(True)
        if old_key in self.records:
            for row in range(self.rowCount()):
                if self.item(row, 0).data(Qt.ItemDataRole.UserRole) == old_key:
                    self.selectRow(row)
                    break
        else:
            self.clearSelection()
            self.setCurrentItem(None)
        self.filter_rows(self._filter)
        self.verticalScrollBar().setValue(scroll)
        self.setUpdatesEnabled(True)

    def filter_rows(self, text: str) -> None:
        self._filter = text.casefold()
        for row in range(self.rowCount()):
            self.setRowHidden(row, bool(self._filter) and not any(self._filter in self.item(row, col).text().casefold() for col in range(self.columnCount())))

    def visible_text(self) -> str:
        lines = ["\t".join(label for label, _ in self.columns)]
        for row in range(self.rowCount()):
            if not self.isRowHidden(row):
                lines.append("\t".join(self.item(row, col).text().replace("\t", " ").replace("\n", " ") for col in range(self.columnCount())))
        return "\n".join(lines)


class Page(QWidget):
    def __init__(self, title: str, subtitle: str = ""):
        super().__init__()
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(20, 18, 20, 18)
        self.layout.setSpacing(12)
        header = QLabel(title)
        font = header.font()
        font.setPointSize(font.pointSize() + 7)
        font.setBold(True)
        header.setFont(font)
        self.layout.addWidget(header)
        if subtitle:
            label = QLabel(subtitle)
            label.setWordWrap(True)
            self.layout.addWidget(label)

    def buttons(self, actions: Sequence[tuple[str, Callable]], selection_table: DataTable | None = None) -> QHBoxLayout:
        bar = QHBoxLayout()
        for label, callback in actions:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, cb=callback: cb())
            bar.addWidget(button)
            if selection_table is not None:
                button.setEnabled(selection_table.selected() is not None)
                selection_table.itemSelectionChanged.connect(lambda b=button, t=selection_table: b.setEnabled(t.selected() is not None))
        bar.addStretch()
        self.layout.addLayout(bar)
        return bar


class FormDialog(QDialog):
    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(450)
        self.layout = QVBoxLayout(self)
        self.form = QFormLayout()
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.layout.addLayout(self.form)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.layout.addWidget(self.buttons)

    def line(self, label: str, initial: Any = "", secret: bool = False) -> QLineEdit:
        field = QLineEdit(str(initial or ""))
        if secret:
            field.setEchoMode(QLineEdit.EchoMode.Password)
        self.form.addRow(label, field)
        return field


def confirm(parent: QWidget, title: str, message: str) -> bool:
    return QMessageBox.question(parent, title, message, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes


def export_text(parent: QWidget, text: str, suggested: str = "openirc-export.txt") -> None:
    path, _ = QFileDialog.getSaveFileName(parent, "Export", suggested, "Text files (*.txt *.tsv);;All files (*)")
    if path:
        try:
            from pathlib import Path
            Path(path).write_text(text, encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(parent, "Export failed", str(exc))
