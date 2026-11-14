"""Qt review dialog for CAD room detection (Phase 3, increment 2).

The dialog is deliberately *thin*: every rule lives in
:class:`cad.room_review.RoomReviewSession`.  The dialog only

* shows the layer classification table and lets the reviewer change a class,
* shows every detected polygon with its rule status and reasons,
* lets the reviewer accept / reject / clear a candidate,
* lets the reviewer confirm the drawing units and the minimum room area.

Nothing here writes a file or touches project state; ``result_rooms()`` returns
the rooms the reviewer approved, and ``session.to_dict()`` is what a future
project file would store.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from cad.dxf_reader import REVIEWABLE_UNITS
from cad.dxf_reader import unit_code_for, unit_name, unit_scale_to_meters
from cad.layer_analyzer import LayerClass
from cad.room_review import ReviewStatus, RoomReviewSession
from cad.parallel_tasks import detect_rooms_task

_STATUS_COLORS = {
    ReviewStatus.AUTO_ACCEPTED.value: "#34d399",
    ReviewStatus.ACCEPTED.value: "#34d399",
    ReviewStatus.NEEDS_REVIEW.value: "#fbbf24",
    ReviewStatus.REJECTED.value: "#f87171",
    ReviewStatus.BLOCKED.value: "#f87171",
}


class ReviewRefreshWorker(QThread):
    """Re-run room classification without blocking a large review dialog."""

    completed = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self, job_id: int, session: RoomReviewSession):
        super().__init__()
        self.job_id = job_id
        self.document = session.document
        self.layer_overrides = dict(session.layer_overrides)
        self.units_override = session.units_override
        self.min_area_m2 = session.min_area_m2
        self.max_area_m2 = session.max_area_m2
        self.allow_circular_curves = session.allow_circular_curves
        self.require_label = session.require_label

    def run(self):
        try:
            report = detect_rooms_task(
                self.document,
                fast_mode=True,
                layer_overrides=self.layer_overrides,
                units_override=self.units_override,
                min_area_m2=self.min_area_m2,
                max_area_m2=self.max_area_m2,
                allow_circular_curves=self.allow_circular_curves,
                require_label=self.require_label,
            )
            self.completed.emit(self.job_id, report)
        except Exception as exc:
            self.failed.emit(self.job_id, str(exc))


class RoomReviewDialog(QDialog):
    """Review layers and room candidates before they reach the HVAC workflow."""

    def __init__(self, session: RoomReviewSession, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._loading = False
        self._refresh_job_id = 0
        self._refresh_worker = None
        self._refreshing = False
        self.setWindowTitle("CAD Room & Layer Review")
        self.resize(1180, 720)
        self._build_ui()
        self._refresh()

    # -- construction ----------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        self.lbl_header = QLabel()
        self.lbl_header.setWordWrap(True)
        layout.addWidget(self.lbl_header)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Drawing units:"))
        self.cmb_units = QComboBox()
        self.cmb_units.setObjectName("cmb_cad_units")
        self.cmb_units.addItem("Not confirmed", None)
        for name in sorted(REVIEWABLE_UNITS):
            self.cmb_units.addItem(name, name)
        self.cmb_units.currentIndexChanged.connect(self._on_units_changed)
        controls.addWidget(self.cmb_units)

        controls.addSpacing(16)
        controls.addWidget(QLabel("Minimum room area (m\u00b2):"))
        self.spin_min_area = QDoubleSpinBox()
        self.spin_min_area.setObjectName("spin_cad_min_area")
        self.spin_min_area.setDecimals(2)
        self.spin_min_area.setRange(0.0, 100000.0)
        self.spin_min_area.setSingleStep(0.1)
        self.spin_min_area.setValue(self.session.min_area_m2)
        self.spin_min_area.valueChanged.connect(self._on_min_area_changed)
        controls.addWidget(self.spin_min_area)
        controls.addStretch()
        layout.addLayout(controls)

        self.tabs = QTabWidget()

        self.tbl_layers = QTableWidget(0, 6)
        self.tbl_layers.setHorizontalHeaderLabels(
            ["Layer", "Classification", "Confidence", "Matched keywords", "Needs review", "Override"]
        )
        self._configure_table(self.tbl_layers)

        self.tbl_rooms = QTableWidget(0, 9)
        self.tbl_rooms.setHorizontalHeaderLabels(
            [
                "Room ID",
                "Label",
                "Layer",
                "Class",
                "Source",
                "Area (m\u00b2)",
                "Status",
                "Reasons",
                "Decision",
            ]
        )
        self._configure_table(self.tbl_rooms)

        self.tabs.addTab(self.tbl_layers, "Layers")
        self.tabs.addTab(self.tbl_rooms, "Room candidates")
        layout.addWidget(self.tabs, 1)

        self.lbl_status = QLabel()
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet("color: #9ca3af; padding: 4px 2px;")
        layout.addWidget(self.lbl_status)

        buttons = QHBoxLayout()
        self.btn_accept_all = QPushButton("Accept all pending")
        self.btn_accept_all.clicked.connect(self._accept_all)
        buttons.addWidget(self.btn_accept_all)
        self.btn_reject_all = QPushButton("Reject all pending")
        self.btn_reject_all.clicked.connect(self._reject_all)
        buttons.addWidget(self.btn_reject_all)
        self.btn_reset = QPushButton("Reset review")
        self.btn_reset.clicked.connect(self._reset)
        buttons.addWidget(self.btn_reset)
        buttons.addStretch()
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        buttons.addWidget(self.btn_cancel)
        self.btn_apply = QPushButton("\u2714  Apply to CAD workflow")
        self.btn_apply.setObjectName("btn_success")
        self.btn_apply.clicked.connect(self._apply)
        buttons.addWidget(self.btn_apply)
        layout.addLayout(buttons)

    @staticmethod
    def _configure_table(table: QTableWidget) -> None:
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        table.horizontalHeader().setStretchLastSection(True)

    # -- rendering -------------------------------------------------------
    def _refresh(self) -> None:
        self._loading = True
        try:
            self._render_header()
            self._render_layers()
            self._render_rooms()
        finally:
            self._loading = False
        self._render_status()

    def _render_header(self) -> None:
        document = self.session.document
        self.lbl_header.setText(
            f"<b>{document.path.name}</b> \u2014 {len(document.layers)} layer(s), "
            f"{document.entity_count} entity/entities.<br>"
            "Nothing is promoted to a room automatically. Every candidate below is "
            "either confirmed by the rules, waiting for your decision, or blocked with a reason."
        )
        index = self.cmb_units.findData(self.session.units_override)
        self.cmb_units.blockSignals(True)
        self.cmb_units.setCurrentIndex(index if index >= 0 else 0)
        self.cmb_units.blockSignals(False)
        self.spin_min_area.blockSignals(True)
        self.spin_min_area.setValue(self.session.min_area_m2)
        self.spin_min_area.blockSignals(False)

    def _render_layers(self) -> None:
        rows = self.session.layer_rows()
        self.tbl_layers.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = [
                str(row["layer"]),
                "",
                str(row["confidence"]),
                str(row["matched_keywords"]),
                "yes" if row["needs_confirmation"] else "no",
                "yes" if row["overridden"] else "",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setToolTip(str(row["layer"]))
                self.tbl_layers.setItem(row_index, column, item)

            combo = QComboBox()
            for layer_class in LayerClass:
                combo.addItem(layer_class.value, layer_class.value)
            combo.setCurrentIndex(max(0, combo.findData(str(row["layer_class"]))))
            combo.currentIndexChanged.connect(
                lambda _index, layer=str(row["layer"]), widget=combo: self._on_layer_class_changed(
                    layer, widget
                )
            )
            self.tbl_layers.setCellWidget(row_index, 1, combo)
        self.tbl_layers.resizeColumnsToContents()

    def _render_rooms(self) -> None:
        rows = self.session.rows()
        self.tbl_rooms.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            area_text = "\u2014" if row.area_m2 is None else f"{row.area_m2:,.2f}"
            values = [
                row.room_id,
                row.label or "\u2014",
                row.layer or "\u2014",
                row.layer_class,
                row.source,
                area_text,
                row.status.value,
                row.reasons_text or "\u2014",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 7:
                    item.setToolTip(row.reasons_text)
                if column == 6:
                    color = _STATUS_COLORS.get(row.status.value)
                    if color:
                        item.setForeground(QColor(color))
                self.tbl_rooms.setItem(row_index, column, item)

            if row.status is ReviewStatus.BLOCKED:
                blocked = QLabel("Blocked by rules")
                blocked.setToolTip(row.reasons_text)
                self.tbl_rooms.setCellWidget(row_index, 8, blocked)
            else:
                decision = QWidget()
                decision_layout = QHBoxLayout(decision)
                decision_layout.setContentsMargins(2, 2, 2, 2)
                decision_layout.setSpacing(4)
                accept = QPushButton("Accept")
                accept.setEnabled(row.status is ReviewStatus.NEEDS_REVIEW)
                accept.clicked.connect(
                    lambda _checked=False, room_id=row.room_id: self._accept_room(room_id)
                )
                reject = QPushButton("Reject")
                reject.clicked.connect(
                    lambda _checked=False, room_id=row.room_id: self._reject_room(room_id)
                )
                clear = QPushButton("Clear")
                clear.setEnabled(row.action != "PENDING")
                clear.clicked.connect(
                    lambda _checked=False, room_id=row.room_id: self._clear_room(room_id)
                )
                decision_layout.addWidget(accept)
                decision_layout.addWidget(reject)
                decision_layout.addWidget(clear)
                self.tbl_rooms.setCellWidget(row_index, 8, decision)
        self.tbl_rooms.resizeColumnsToContents()

    def _render_status(self) -> None:
        self.lbl_status.setText(self.session.status_line())
        blockers = self.session.blockers()
        self.btn_apply.setEnabled(bool(self.session.accepted_rooms))
        if blockers:
            self.btn_apply.setToolTip("\n".join(blockers))
        else:
            self.btn_apply.setToolTip("All candidates decided and units confirmed.")

    def _set_refresh_busy(self, busy: bool) -> None:
        self._refreshing = busy
        for widget in (
            self.cmb_units,
            self.spin_min_area,
            self.tbl_layers,
            self.btn_accept_all,
            self.btn_reject_all,
            self.btn_reset,
            self.btn_apply,
        ):
            widget.setEnabled(not busy)
        if busy:
            self.lbl_status.setText("Re-analyzing room boundaries… Please wait.")
            self.btn_cancel.setText("Cancel")

    def _schedule_refresh(self) -> None:
        """Refresh synchronously for small plans, asynchronously for large ones."""
        if self.session.document.entity_count < 20000:
            self.session.refresh()
            self._refresh()
            return

        old_worker = self._refresh_worker
        if old_worker is not None and old_worker.isRunning():
            old_worker.requestInterruption()
        self._refresh_job_id += 1
        job_id = self._refresh_job_id
        self._set_refresh_busy(True)
        worker = ReviewRefreshWorker(job_id, self.session)
        worker.completed.connect(self._on_refresh_completed)
        worker.failed.connect(self._on_refresh_failed)
        worker.finished.connect(worker.deleteLater)
        self._refresh_worker = worker
        worker.start()

    def _on_refresh_completed(self, job_id: int, report) -> None:
        if job_id != self._refresh_job_id:
            return
        self.session.report = report
        self._refresh_worker = None
        self._set_refresh_busy(False)
        self._refresh()

    def _on_refresh_failed(self, job_id: int, message: str) -> None:
        if job_id != self._refresh_job_id:
            return
        self._refresh_worker = None
        self._set_refresh_busy(False)
        QMessageBox.critical(self, "Room Analysis Error", message)

    # -- event handlers --------------------------------------------------
    def _on_layer_class_changed(self, layer: str, combo: QComboBox) -> None:
        if self._loading or self._refreshing:
            return
        value = combo.currentData()
        if not value:
            return
        self.session.layer_overrides[str(layer)] = LayerClass(str(value))
        self._schedule_refresh()

    def _on_units_changed(self) -> None:
        if self._loading or self._refreshing:
            return
        units = self.cmb_units.currentData()
        try:
            if units:
                if unit_scale_to_meters(units) is None:
                    raise ValueError(f"Unsupported drawing units: {units}")
                code = unit_code_for(units)
                self.session.units_override = (
                    unit_name(code) if code is not None else str(units)
                )
            else:
                self.session.units_override = None
        except ValueError as exc:  # pragma: no cover - defensive, combo is constrained
            QMessageBox.warning(self, "Drawing Units", str(exc))
            return
        self._schedule_refresh()

    def _on_min_area_changed(self, value: float) -> None:
        if self._loading or self._refreshing:
            return
        self.session.min_area_m2 = float(value)
        self._schedule_refresh()

    def _accept_room(self, room_id: str) -> None:
        try:
            self.session.accept(room_id)
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot accept this candidate", str(exc))
            return
        self._refresh()

    def _reject_room(self, room_id: str) -> None:
        self.session.reject(room_id)
        self._refresh()

    def _clear_room(self, room_id: str) -> None:
        self.session.clear_decision(room_id)
        self._refresh()

    def _accept_all(self) -> None:
        self.session.accept_all_reviewable()
        self._refresh()

    def _reject_all(self) -> None:
        self.session.reject_all_reviewable()
        self._refresh()

    def _reset(self) -> None:
        answer = QMessageBox.question(
            self,
            "Reset review",
            "Discard every layer override, unit confirmation and room decision for this drawing?",
        )
        if answer != QMessageBox.Yes:
            return
        self.session.reset()
        self._refresh()

    def _apply(self) -> None:
        blockers = self.session.blockers()
        if blockers:
            answer = QMessageBox.question(
                self,
                "Review not complete",
                "The review is not complete:\n\n"
                + "\n".join(f"\u2022 {message}" for message in blockers)
                + "\n\nApply anyway? Rooms that are not accepted will not reach the HVAC workflow.",
            )
            if answer != QMessageBox.Yes:
                return
        self.accept()

    # -- results ---------------------------------------------------------
    def result_rooms(self):
        """Rooms approved by the reviewer and safe to use in engineering."""

        return self.session.usable_rooms
