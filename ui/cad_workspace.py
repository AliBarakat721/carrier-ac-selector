from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import Qt, QPointF, QRectF, Signal
from PySide6.QtGui import QColor, QBrush, QPen, QPainter, QIcon
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsPolygonItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


PALETTE = {
    "canvas": QColor("#0d1117"),
    "grid": QColor("#1b2633"),
    "geometry": QColor("#a8b5c3"),
    "room": QColor("#24b889"),
    "room_fill": QColor(36, 184, 137, 28),
    "selected": QColor("#3288f4"),
    "text": QColor("#e6edf5"),
    "muted": QColor("#9aa8b8"),
    "panel": QColor("#252f3b"),
}


def _icon(root: Path, name: str) -> QIcon:
    path = root / "icons" / f"{name}.svg"
    return QIcon(str(path)) if path.exists() else QIcon()


class CadViewport(QGraphicsView):
    entity_selected = Signal(object)
    room_selected = Signal(object)

    def __init__(self, project_root: Path, parent=None):
        super().__init__(parent)
        self.project_root = project_root
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setRenderHint(QPainter.Antialiasing)
        self.setBackgroundBrush(PALETTE["canvas"])
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self._rooms = []
        self._room_items = {}
        self._draw_grid()

    def _draw_grid(self):
        # The grid is painted as a viewport background, not as scene items;
        # otherwise it would inflate itemsBoundingRect and make small plans
        # appear tiny after fitInView().
        return None

    def drawBackground(self, painter, rect):
        painter.fillRect(rect, PALETTE["canvas"])
        step = 100.0
        pen = QPen(PALETTE["grid"])
        pen.setWidthF(0.4)
        left = math.floor(rect.left() / step) * step
        top = math.floor(rect.top() / step) * step
        x = left
        while x <= rect.right():
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            x += step
        y = top
        while y <= rect.bottom():
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            y += step

    def clear_document(self):
        self.scene.clear()
        self._room_items.clear()
        self._draw_grid()

    def render_document(self, document, rooms: Iterable[object] = ()):
        self.clear_document()
        self._rooms = list(rooms)
        self._render_entities(document.entities if document else [])
        self._render_rooms(self._rooms)
        rect = self.scene.itemsBoundingRect()
        if rect.isValid() and rect.width() > 1 and rect.height() > 1:
            self.fitInView(rect.adjusted(-80, -80, 80, 80), Qt.KeepAspectRatio)

    def _render_entities(self, entities):
        geometry_pen = QPen(PALETTE["geometry"])
        geometry_pen.setWidthF(1.2)
        text_brush = QBrush(PALETTE["text"])
        for entity in entities:
            data = entity.data
            kind = entity.entity_type.upper()
            if kind == "LINE":
                start, end = data.get("start"), data.get("end")
                if start and end:
                    item = self.scene.addLine(start[0], -start[1], end[0], -end[1], geometry_pen)
                    item.setData(0, entity)
            elif kind in {"LWPOLYLINE", "POLYLINE", "SPLINE"}:
                points = data.get("points", [])
                if len(points) > 1:
                    polygon = [QPointF(float(x), -float(y)) for x, y in points]
                    if data.get("closed"):
                        polygon.append(polygon[0])
                    for first, second in zip(polygon, polygon[1:]):
                        item = self.scene.addLine(first.x(), first.y(), second.x(), second.y(), geometry_pen)
                        item.setData(0, entity)
            elif kind == "CIRCLE":
                center = data.get("center")
                radius = float(data.get("radius", 0))
                if center and radius > 0:
                    item = self.scene.addEllipse(center[0] - radius, -center[1] - radius, radius * 2, radius * 2, geometry_pen)
                    item.setData(0, entity)
            elif kind == "ARC":
                center = data.get("center")
                radius = float(data.get("radius", 0))
                if center and radius > 0:
                    item = self.scene.addEllipse(center[0] - radius, -center[1] - radius, radius * 2, radius * 2, geometry_pen)
                    item.setData(0, entity)
            elif kind in {"TEXT", "MTEXT", "ATTRIB"}:
                insert = data.get("insert")
                text = str(data.get("text", "")).strip()
                if insert and text:
                    item = self.scene.addSimpleText(text)
                    item.setBrush(text_brush)
                    item.setPos(float(insert[0]), -float(insert[1]))

    def _render_rooms(self, rooms):
        pen = QPen(PALETTE["room"])
        pen.setWidthF(2.0)
        for room in rooms:
            coords = list(room.polygon.exterior.coords)
            polygon = [QPointF(float(x), -float(y)) for x, y in coords]
            item = QGraphicsPolygonItem(polygon)
            item.setPen(pen)
            item.setBrush(QBrush(PALETTE["room_fill"]))
            item.setData(0, room)
            self.scene.addItem(item)
            self._room_items[room.room_id] = item
            point = room.polygon.representative_point()
            label = QGraphicsSimpleTextItem(room.label or room.room_id)
            label.setBrush(QBrush(PALETTE["text"]))
            label.setPos(float(point.x), -float(point.y))
            label.setScale(1.1)
            label.setData(0, room)
            self.scene.addItem(label)

    def mousePressEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        if item:
            value = item.data(0)
            if value is not None and hasattr(value, "room_id"):
                self.room_selected.emit(value)
                self._highlight_room(value.room_id)
            elif value is not None:
                self.entity_selected.emit(value)
        super().mousePressEvent(event)

    def _highlight_room(self, room_id):
        for key, item in self._room_items.items():
            item.setPen(QPen(PALETTE["selected"] if key == room_id else PALETTE["room"], 2.5 if key == room_id else 2.0))

    def wheelEvent(self, event):
        factor = 1.18 if event.angleDelta().y() > 0 else 1 / 1.18
        self.scale(factor, factor)


class PropertiesInspector(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("properties_inspector")
        self.setMinimumWidth(260)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        self.title = QLabel("PROPERTIES")
        self.title.setObjectName("panel_title")
        layout.addWidget(self.title)
        self.value = QLabel("Select a room or CAD entity\nto inspect its linked data.")
        self.value.setWordWrap(True)
        self.value.setAlignment(Qt.AlignTop)
        self.value.setObjectName("property_value")
        layout.addWidget(self.value)
        layout.addStretch()

    def show_room(self, room, assignment: str = "Unassigned", unit: str = "Not selected"):
        self.title.setText("ROOM PROPERTIES")
        self.value.setText(
            f"<b>{room.label or room.room_id}</b><br><br>"
            f"Room ID: {room.room_id}<br>"
            f"Area: {room.area:,.2f} m²<br>"
            f"Perimeter: {room.perimeter:,.2f}<br>"
            f"Geometry: <span style='color:#e6ac45'>{room.status}</span><br>"
            f"HAP System: {assignment or 'Unassigned'}<br>"
            f"Selected Unit: {unit}"
        )

    def show_entity(self, entity):
        self.title.setText("CAD ENTITY")
        data = entity.data
        details = [f"Type: {entity.entity_type}", f"Layer: {entity.layer}", f"Handle: {entity.handle or '—'}"]
        if entity.entity_type.upper() in {"TEXT", "MTEXT", "ATTRIB"}:
            details.append(f"Text: {data.get('text', '')}")
        self.value.setText("<br>".join(details))


class ProjectBrowser(QFrame):
    room_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("project_browser")
        self.setMinimumWidth(220)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 8, 12)
        title = QLabel("PROJECT BROWSER")
        title.setObjectName("panel_title")
        layout.addWidget(title)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.tree)
        self.set_project_state(None, [])

    def set_project_state(self, document, rooms, selected_units=None):
        selected_units = selected_units or {}
        self.tree.clear()
        root = QTreeWidgetItem(["PROJECT  ·  Carrier HVAC"])
        root.setExpanded(True)
        self.tree.addTopLevelItem(root)
        drawing = QTreeWidgetItem(["▣  Drawings"])
        root.addChild(drawing)
        drawing.addChild(QTreeWidgetItem([Path(document.path).name if document else "No drawing loaded"]))
        building = QTreeWidgetItem([f"⌂  Building  ({len(rooms)} rooms)"])
        root.addChild(building)
        for room in rooms:
            item = QTreeWidgetItem([f"○  {room.label or room.room_id}"])
            item.setData(0, Qt.UserRole, room.room_id)
            building.addChild(item)
        systems = QTreeWidgetItem([f"◈  HAP Systems  ({len(selected_units)})"])
        root.addChild(systems)
        equipment = QTreeWidgetItem(["▰  Equipment"])
        root.addChild(equipment)
        equipment.addChild(QTreeWidgetItem([f"Indoor Units  ({len(selected_units)})"]))
        equipment.addChild(QTreeWidgetItem(["Outdoor Units  (manual placement) "]))
        layers = QTreeWidgetItem([f"▤  Layers  ({len(document.layers) if document else 0})"])
        root.addChild(layers)
        if document:
            for layer in document.layers[:80]:
                layers.addChild(QTreeWidgetItem([layer]))
        reports = QTreeWidgetItem(["▤  Reports"])
        root.addChild(reports)
        reports.addChild(QTreeWidgetItem(["Equipment Schedule"]))
        reports.addChild(QTreeWidgetItem(["Design Validation"]))

    def _on_item_clicked(self, item, _column):
        room_id = item.data(0, Qt.UserRole)
        if room_id:
            self.room_clicked.emit(str(room_id))


class CadWorkspace(QWidget):
    assignment_changed = Signal(str, str)
    room_selected = Signal(object)
    request_open = Signal()
    request_export = Signal()

    def __init__(self, project_root: Path, parent=None):
        super().__init__(parent)
        self.project_root = project_root
        self.document = None
        self.rooms = []
        self.assignments = {}
        self.selected_units = {}
        self.browser = ProjectBrowser()
        self.viewport = CadViewport(project_root)
        self.properties = PropertiesInspector()
        self.status = QLabel("No drawing loaded · Import a DXF or DWG floor plan to begin")
        self.status.setObjectName("cad_status")
        self.room_list = QListWidget()
        self.room_list.setMaximumHeight(112)
        self.room_list.itemClicked.connect(self._select_room_from_list)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        toolbar = QFrame()
        toolbar.setObjectName("cad_toolbar")
        row = QHBoxLayout(toolbar)
        row.setContentsMargins(12, 8, 12, 8)
        title = QLabel("CAD WORKSPACE")
        title.setObjectName("workspace_title")
        row.addWidget(title)
        row.addWidget(self.status, 1)
        open_button = QPushButton("Import DWG / DXF")
        open_button.setIcon(_icon(self.project_root, "fileopen"))
        open_button.clicked.connect(self.request_open.emit)
        row.addWidget(open_button)
        export_button = QPushButton("Export HVAC DXF")
        export_button.setIcon(_icon(self.project_root, "export"))
        export_button.setObjectName("btn_success")
        export_button.clicked.connect(self.request_export.emit)
        row.addWidget(export_button)
        root.addWidget(toolbar)
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.browser)
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.addWidget(self.viewport, 1)
        center_layout.addWidget(self.room_list)
        splitter.addWidget(center)
        splitter.addWidget(self.properties)
        splitter.setSizes([230, 900, 290])
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)
        footer = QFrame()
        footer.setObjectName("cad_footer")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(12, 6, 12, 6)
        footer_layout.addWidget(QLabel("MODEL SPACE"))
        footer_layout.addStretch()
        footer_layout.addWidget(QLabel("SNAP  ON"))
        footer_layout.addWidget(QLabel("GRID  ON"))
        footer_layout.addWidget(QLabel("ORTHO  OFF"))
        root.addWidget(footer)
        self.viewport.room_selected.connect(self._select_room)
        self.viewport.entity_selected.connect(self.properties.show_entity)
        self.browser.room_clicked.connect(self._select_room_by_id)

    def set_project_data(self, document, rooms, assignments=None, selected_units=None):
        self.document = document
        self.rooms = list(rooms or [])
        self.assignments = dict(assignments or {})
        self.selected_units = selected_units or {}
        self.viewport.render_document(document, self.rooms)
        self.browser.set_project_state(document, self.rooms, self.selected_units)
        self.room_list.clear()
        for room in self.rooms:
            item = QListWidgetItem(f"{room.label or room.room_id}   ·   {room.status}")
            item.setData(Qt.UserRole, room.room_id)
            self.room_list.addItem(item)
        filename = Path(document.path).name if document else "No drawing"
        diagnostics = f" · {len(self.rooms)} rooms · {len(getattr(document, 'entities', []))} entities"
        self.status.setText(f"{filename}{diagnostics}")
        if not self.rooms:
            self.properties.value.setText("No room boundaries detected.\nImport a drawing or review geometry diagnostics.")

    def _select_room_from_list(self, item):
        self._select_room_by_id(item.data(Qt.UserRole))

    def _select_room_by_id(self, room_id):
        room = next((candidate for candidate in self.rooms if candidate.room_id == room_id), None)
        if room:
            self._select_room(room)
            self.viewport._highlight_room(room_id)

    def _select_room(self, room):
        assignment = self.assignments.get(room.room_id, "Unassigned")
        entry = self.selected_units.get(assignment, {})
        row = entry.get("row", {}) if isinstance(entry, dict) else {}
        unit = row.get("Indoor Unit Model", "Not selected") if row is not None else "Not selected"
        self.properties.show_room(room, assignment, str(unit))
        self.room_selected.emit(room)

    def refresh_assignments(self, assignments, selected_units=None):
        self.assignments = dict(assignments or {})
        if selected_units is not None:
            self.selected_units = selected_units
        for index in range(self.room_list.count()):
            item = self.room_list.item(index)
            room_id = item.data(Qt.UserRole)
            if room_id in self.assignments:
                item.setText(f"{room_id}   ·   {self.assignments[room_id]}")
