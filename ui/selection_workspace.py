from __future__ import annotations

from pathlib import Path

import pandas as pd
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class SelectionWorkspace(QWidget):
    """Catalog-first equipment selection workspace.

    The workspace owns only the presentation/filter state. The catalogue and
    selected_units mapping are supplied by the main window, so the existing
    HAP/CAD workflows remain the single source of truth.
    """

    model_requested = Signal(object, str)
    request_analyzer = Signal()

    def __init__(self, project_root: Path | None = None, parent=None):
        super().__init__(parent)
        self.project_root = project_root
        self.catalog = pd.DataFrame()
        self.selected_units: dict = {}
        self._visible_rows: list[int] = []
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        toolbar = QFrame()
        toolbar.setObjectName("selection_toolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(14, 9, 14, 9)
        title = QLabel("SELECTION WORKSPACE")
        title.setObjectName("workspace_title")
        toolbar_layout.addWidget(title)
        self.context_label = QLabel("No HAP system active · browse the Carrier catalogue")
        self.context_label.setStyleSheet("color: #9aa8b8; padding-left: 14px;")
        toolbar_layout.addWidget(self.context_label, 1)
        self.analyzer_button = QPushButton("Open HAP Analyzer")
        self.analyzer_button.clicked.connect(self.request_analyzer.emit)
        toolbar_layout.addWidget(self.analyzer_button)
        root.addWidget(toolbar)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        summary = QFrame()
        summary_layout = QGridLayout(summary)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        self.lbl_models = self._metric(summary_layout, 0, "CATALOG MODELS", "0")
        self.lbl_categories = self._metric(summary_layout, 1, "CATEGORIES", "0")
        self.lbl_series = self._metric(summary_layout, 2, "SERIES", "0")
        self.lbl_selected = self._metric(summary_layout, 3, "SELECTED SYSTEMS", "0")
        layout.addWidget(summary)

        filters = QFrame()
        filters.setObjectName("selection_filters")
        filter_layout = QGridLayout(filters)
        filter_layout.setContentsMargins(10, 8, 10, 8)
        filter_layout.setHorizontalSpacing(8)
        filter_layout.setVerticalSpacing(6)
        filter_layout.addWidget(QLabel("Search"), 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Model, indoor unit, series, notes…")
        self.search.textChanged.connect(self.refresh_table)
        filter_layout.addWidget(self.search, 0, 1, 1, 3)
        filter_layout.addWidget(QLabel("Category"), 1, 0)
        self.category = QComboBox()
        self.category.currentTextChanged.connect(self._filter_changed)
        filter_layout.addWidget(self.category, 1, 1)
        filter_layout.addWidget(QLabel("Series"), 1, 2)
        self.series = QComboBox()
        self.series.currentTextChanged.connect(self._filter_changed)
        filter_layout.addWidget(self.series, 1, 3)
        filter_layout.addWidget(QLabel("Type"), 1, 4)
        self.unit_type = QComboBox()
        self.unit_type.currentTextChanged.connect(self._filter_changed)
        filter_layout.addWidget(self.unit_type, 1, 5)
        filter_layout.addWidget(QLabel("Min capacity"), 1, 6)
        self.min_capacity = QSpinBox()
        self.min_capacity.setRange(0, 200000)
        self.min_capacity.setSingleStep(1000)
        self.min_capacity.setSuffix(" Btu/h")
        self.min_capacity.valueChanged.connect(self.refresh_table)
        filter_layout.addWidget(self.min_capacity, 1, 7)
        layout.addWidget(filters)

        context = QFrame()
        context_layout = QHBoxLayout(context)
        context_layout.setContentsMargins(0, 0, 0, 0)
        context_layout.addWidget(QLabel("Assign selected model to"))
        self.system_name = QLineEdit()
        self.system_name.setPlaceholderText("HAP system / room name (e.g. AHU-01)")
        context_layout.addWidget(self.system_name, 1)
        self.add_button = QPushButton("Add Model to Selection")
        self.add_button.setObjectName("btn_success")
        self.add_button.setEnabled(False)
        self.add_button.clicked.connect(self._emit_selected_model)
        context_layout.addWidget(self.add_button)
        layout.addWidget(context)

        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.itemSelectionChanged.connect(self._on_table_selection)
        self.table.itemDoubleClicked.connect(lambda _item: self._emit_selected_model())
        layout.addWidget(self.table, 1)

        self.status = QLabel("Load the Carrier catalogue to begin.")
        self.status.setStyleSheet("color: #9aa8b8; padding: 2px 4px;")
        layout.addWidget(self.status)
        root.addWidget(content, 1)

    @staticmethod
    def _metric(parent_layout, column: int, title: str, value: str):
        box = QFrame()
        box.setObjectName("selection_metric")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(12, 8, 12, 8)
        label = QLabel(title)
        label.setStyleSheet("color: #9aa8b8; font-size: 10px; font-weight: 700;")
        value_label = QLabel(value)
        value_label.setStyleSheet("color: #e6edf5; font-size: 20px; font-weight: 800;")
        box_layout.addWidget(label)
        box_layout.addWidget(value_label)
        parent_layout.addWidget(box, 0, column)
        return value_label

    def set_catalog(self, catalog: pd.DataFrame, selected_units: dict | None = None):
        self.catalog = catalog.copy() if catalog is not None else pd.DataFrame()
        if selected_units is not None:
            self.selected_units = selected_units
        self._rebuild_filters()
        self.refresh_table()

    def set_context(self, system_name: str = "", load_btu: float = 0.0, load_cfm: float = 0.0):
        name = str(system_name or "").strip()
        if name and not self.system_name.text().strip():
            self.system_name.setText(name)
        if name and (load_btu or load_cfm):
            self.context_label.setText(
                f"Active HAP system: {name} · {load_btu:,.0f} Btu/h · {load_cfm:,.0f} CFM"
            )
        elif name:
            self.context_label.setText(f"Active HAP system: {name} · choose a catalogue model")
        else:
            self.context_label.setText("No HAP system active · browse the Carrier catalogue")

    def set_selected_units(self, selected_units: dict):
        self.selected_units = selected_units or {}
        self.lbl_selected.setText(str(len(self.selected_units)))

    def _rebuild_filters(self):
        self._fill_combo(self.category, self._values("Category"))
        self._fill_combo(self.series, self._values("Series"))
        self._fill_combo(self.unit_type, self._values("Type"))
        if not self.catalog.empty and "Cooling Capacity (Btu/hr)" in self.catalog:
            max_capacity = self.catalog["Cooling Capacity (Btu/hr)"].max()
            self.min_capacity.setMaximum(max(200000, int(max_capacity or 0)))

    def _values(self, column: str):
        if self.catalog.empty or column not in self.catalog:
            return []
        return sorted({str(value).strip() for value in self.catalog[column].dropna() if str(value).strip()})

    @staticmethod
    def _fill_combo(combo: QComboBox, values):
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("All")
        combo.addItems(values)
        combo.blockSignals(False)

    def _filter_changed(self, _value: str):
        if self.sender() is self.category:
            self._fill_combo(self.series, self._values_for_category())
        self.refresh_table()

    def _values_for_category(self):
        if self.catalog.empty or "Category" not in self.catalog:
            return []
        selected = self.category.currentText()
        frame = self.catalog if selected in ("", "All") else self.catalog[self.catalog["Category"].astype(str) == selected]
        return sorted({str(value).strip() for value in frame["Series"].dropna() if str(value).strip()})

    def refresh_table(self, *_args):
        frame = self.catalog
        if frame.empty:
            self.table.clear()
            self.table.setRowCount(0)
            self.status.setText("No catalogue rows are available.")
            return
        if self.category.currentText() not in ("", "All"):
            frame = frame[frame["Category"].astype(str) == self.category.currentText()]
        if self.series.currentText() not in ("", "All"):
            frame = frame[frame["Series"].astype(str) == self.series.currentText()]
        if self.unit_type.currentText() not in ("", "All"):
            frame = frame[frame["Type"].astype(str) == self.unit_type.currentText()]
        minimum = self.min_capacity.value()
        if minimum and "Cooling Capacity (Btu/hr)" in frame:
            frame = frame[frame["Cooling Capacity (Btu/hr)"].fillna(0) >= minimum]
        query = self.search.text().strip().casefold()
        if query:
            searchable = frame.fillna("").astype(str).agg(" ".join, axis=1).str.casefold()
            frame = frame[searchable.str.contains(query, regex=False)]
        self._visible_rows = list(frame.index)
        columns = [
            ("Category", "Category"), ("Series", "Series"),
            ("System Model", "System Model"), ("Indoor Unit", "Indoor Unit Model"),
            ("Outdoor Unit", "Outdoor Unit Model"), ("Capacity (Btu/h)", "Cooling Capacity (Btu/hr)"),
            ("High CFM", "High CFM (numeric)"), ("EER", "EER (Btu/Wh)"), ("Type", "Type"),
        ]
        self.table.setSortingEnabled(False)
        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels([label for label, _ in columns])
        self.table.setRowCount(len(frame))
        for row_index, (source_index, row) in enumerate(frame.iterrows()):
            for col_index, (_label, column) in enumerate(columns):
                value = row.get(column, "")
                if pd.isna(value):
                    text = "—"
                elif column in {"Cooling Capacity (Btu/hr)", "High CFM (numeric)"}:
                    text = f"{float(value):,.0f}"
                elif column == "EER":
                    text = f"{float(value):g}" if str(value) not in ("nan", "None") else "—"
                else:
                    text = str(value)
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole, int(source_index))
                self.table.setItem(row_index, col_index, item)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.resizeColumnsToContents()
        self.table.setSortingEnabled(True)
        self.lbl_models.setText(str(len(self.catalog)))
        self.lbl_categories.setText(str(self.catalog["Category"].nunique() if "Category" in self.catalog else 0))
        self.lbl_series.setText(str(self.catalog["Series"].nunique() if "Series" in self.catalog else 0))
        self.lbl_selected.setText(str(len(self.selected_units)))
        self.status.setText(f"Showing {len(frame)} of {len(self.catalog)} catalogue model(s).")

    def _on_table_selection(self):
        self.add_button.setEnabled(self.table.currentRow() >= 0)

    def _emit_selected_model(self):
        row_index = self.table.currentRow()
        if row_index < 0 or not self._visible_rows:
            return
        item = self.table.item(row_index, 0)
        if item is None:
            return
        source_index = item.data(Qt.UserRole)
        if source_index not in self.catalog.index:
            return
        system_name = self.system_name.text().strip() or f"System {len(self.selected_units) + 1}"
        self.model_requested.emit(self.catalog.loc[source_index].copy(), system_name)
