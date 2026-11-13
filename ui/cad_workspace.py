from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path
from typing import Iterable
from PySide6.QtCore import Qt, QPointF, QRectF, QSize, Signal
from PySide6.QtGui import QColor, QBrush, QPen, QPainter, QPainterPath, QIcon, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsPathItem,
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
from cad.dxf_reader import CadEntity


PALETTE = {
    "canvas": QColor("#e4e4e4"),
    "grid": QColor("#dcdcdc"),
    "geometry": QColor("#5a6472"),
    "room": QColor("#2f6fb5"),
    "room_fill": QColor(173, 216, 240, 70),
    "selected": QColor("#e8590c"),
    "text": QColor("#1f2933"),
    "muted": QColor("#6b7785"),
    "panel": QColor("#f4f6f8"),
}

# Layer category -> (keywords, stroke colour, fill colour or None, pen width)
# Matches the look of classic architectural CAD: navy walls, pale-yellow
# furniture, cyan windows, grey doors, orange electrical.
LAYER_STYLES = [
    ("walls", ("wall", "جدار", "حائط", "a-wall"), "#0b2a6f", None, 2.6),
    ("doors", ("door", "باب"), "#6b7280", None, 1.3),
    ("windows", ("window", "glaz", "شباك", "نافذ"), "#2a9fd6", "#cfeefc", 1.4),
    ("furniture", ("furn", "bed", "sofa", "chair", "table", "اثاث", "أثاث"), "#9a8b2f", "#f6ebab", 1.1),
    ("sanitary", ("sanit", "plumb", "bath", "toilet", "kitchen", "wet", "حمام", "مطبخ"), "#3b82c4", "#dcebf7", 1.1),
    ("electrical", ("elec", "light", "power", "كهرب"), "#d9822b", None, 1.2),
    ("hvac", ("hvac", "duct", "ac_", "air", "تكييف"), "#1b998b", None, 1.5),
    ("text", ("text", "dim", "anno", "note", "tag"), "#374151", None, 1.0),
]
DEFAULT_LAYER_STYLE = ("other", (), "#5a6472", None, 1.0)


def layer_style(layer_name: str):
    """Return the style tuple for a layer based on keywords in its name."""
    lowered = (layer_name or "").casefold()
    for style in LAYER_STYLES:
        if any(keyword in lowered for keyword in style[1]):
            return style
    return DEFAULT_LAYER_STYLE


def _swatch(color: QColor, size: int = 14) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QBrush(color))
    painter.setPen(QPen(QColor("#8a94a0"), 1))
    painter.drawRoundedRect(1, 1, size - 2, size - 2, 2, 2)
    painter.end()
    return QIcon(pixmap)


LIGHT_STYLESHEET = """
CadWorkspace, CadWorkspace QWidget { background-color: #f4f6f8; color: #1f2933;
    font-family: 'Segoe UI', Arial, sans-serif; font-size: 12px; }
QFrame#cad_toolbar { background-color: #fbfcfd; border-bottom: 1px solid #c9d0d8; }
QFrame#cad_footer { background-color: #eceff2; border-top: 1px solid #c9d0d8; }
QFrame#cad_footer QLabel { color: #4b5563; font-size: 11px; padding: 0 6px; background: transparent; }
QLabel#workspace_title { color: #1f2933; font-weight: 700; letter-spacing: 1px; }
QLabel#cad_status { color: #4b5563; padding-left: 12px; }
QFrame#project_browser, QFrame#properties_inspector { background-color: #f4f6f8; border: none;
    border-right: 1px solid #c9d0d8; }
QFrame#properties_inspector { border-right: none; border-left: 1px solid #c9d0d8; }
QLabel#panel_title { color: #374151; font-size: 11px; font-weight: 700; letter-spacing: 1px;
    padding: 2px 0 6px 0; border-bottom: 1px solid #d5dbe1; }
QLabel#property_value { color: #1f2933; }
QTreeWidget, QListWidget { background: #ffffff; color: #1f2933; border: 1px solid #d5dbe1; }
QTreeWidget::item { padding: 2px 0; }
QTreeWidget::item:hover, QListWidget::item:hover { background: #e8f0fb; }
QTreeWidget::item:selected, QListWidget::item:selected { background: #bcd7f7; color: #0b2a6f; }
QPushButton { background-color: #ffffff; color: #1f2933; border: 1px solid #b8c1cb;
    border-radius: 3px; padding: 5px 12px; font-weight: 600; }
QPushButton:hover { background-color: #e8f0fb; border-color: #2f6fb5; }
QPushButton:pressed { background-color: #d3e4f8; }
QPushButton#btn_success { background-color: #2f6fb5; color: #ffffff; border-color: #2a5f9c; }
QPushButton#btn_success:hover { background-color: #3a80cc; }
QSplitter::handle { background: #c9d0d8; }
QScrollBar:vertical { background: #eceff2; width: 10px; }
QScrollBar::handle:vertical { background: #b8c1cb; border-radius: 4px; min-height: 24px; }
QScrollBar:horizontal { background: #eceff2; height: 10px; }
QScrollBar::handle:horizontal { background: #b8c1cb; border-radius: 4px; min-width: 24px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
"""


def _icon(root: Path, name: str) -> QIcon:
    path = root / "icons" / f"{name}.svg"
    return QIcon(str(path)) if path.exists() else QIcon()


class CadViewport(QGraphicsView):
    entity_selected = Signal(object)
    room_selected = Signal(object)
    entity_created = Signal(object)
    entity_deleted = Signal(object)
    entity_moved = Signal(object, object, object)
    action_undone = Signal()
    prompt_changed = Signal(str)

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
        self._layer_items = {}
        self._mode = "select"
        self._pending_point = None
        self._move_entity = None
        self._draw_grid()

    def set_mode(self, mode: str):
        self._mode = mode
        self._pending_point = None
        self._move_entity = None
        prompts = {
            "select": "Select · click a CAD entity or room",
            "line": "Draw line · click start, then end",
            "rectangle": "Draw rectangle · click opposite corners",
            "circle": "Draw circle · click center, then radius point",
            "move": "Move · click an entity, then its destination",
            "delete": "Delete · click an entity (rooms are protected)",
            "undo": "Undo last CAD edit",
        }
        self.prompt_changed.emit(prompts.get(mode, "Select mode"))

    def set_layer_visible(self, layer: str, visible: bool):
        for item in self._layer_items.get(layer, []):
            item.setVisible(visible)

    def _register(self, item, layer: str, entity):
        item.setData(0, entity)
        item.setData(1, layer)
        self._layer_items.setdefault(layer, []).append(item)

    def _draw_grid(self):
        # The grid is painted as a viewport background, not as scene items;
        # otherwise it would inflate itemsBoundingRect and make small plans
        # appear tiny after fitInView().
        return None

    def drawBackground(self, painter, rect):
        painter.fillRect(rect, PALETTE["canvas"])
        step = 100.0
        pen = QPen(PALETTE["grid"])
        pen.setWidthF(0.6)
        pen.setCosmetic(True)
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
        self._layer_items.clear()
        self._draw_grid()

    def render_document(self, document, rooms: Iterable[object] = ()):
        self.clear_document()
        self._rooms = list(rooms)
        self._render_entities(document.entities if document else [])
        self._render_rooms(self._rooms)
        rect = self.scene.itemsBoundingRect()
        if rect.isValid() and rect.width() > 1 and rect.height() > 1:
            self.fitInView(rect.adjusted(-80, -80, 80, 80), Qt.KeepAspectRatio)

    def _pen_brush(self, layer: str):
        _name, _keys, stroke, fill, width = layer_style(layer)
        pen = QPen(QColor(stroke))
        pen.setWidthF(width)
        pen.setCosmetic(True)  # constant line width at any zoom level
        pen.setJoinStyle(Qt.MiterJoin)
        brush = QBrush(QColor(fill)) if fill else QBrush(Qt.NoBrush)
        return pen, brush

    def _render_entities(self, entities):
        for entity in entities:
            data = entity.data
            kind = entity.entity_type.upper()
            layer = entity.layer
            pen, brush = self._pen_brush(layer)
            if kind == "LINE":
                start, end = data.get("start"), data.get("end")
                if start and end:
                    item = self.scene.addLine(start[0], -start[1], end[0], -end[1], pen)
                    self._register(item, layer, entity)
            elif kind in {"LWPOLYLINE", "POLYLINE", "SPLINE"}:
                points = data.get("points", [])
                if len(points) > 1:
                    path = QPainterPath()
                    path.moveTo(float(points[0][0]), -float(points[0][1]))
                    for x, y in points[1:]:
                        path.lineTo(float(x), -float(y))
                    if data.get("closed"):
                        path.closeSubpath()
                    item = self.scene.addPath(path, pen, brush if data.get("closed") else QBrush(Qt.NoBrush))
                    self._register(item, layer, entity)
            elif kind == "CIRCLE":
                center = data.get("center")
                radius = float(data.get("radius", 0))
                if center and radius > 0:
                    item = self.scene.addEllipse(
                        center[0] - radius, -center[1] - radius, radius * 2, radius * 2, pen, brush
                    )
                    self._register(item, layer, entity)
            elif kind == "ARC":
                center = data.get("center")
                radius = float(data.get("radius", 0))
                if center and radius > 0:
                    start_angle = float(data.get("start_angle", 0.0))
                    end_angle = float(data.get("end_angle", 360.0))
                    sweep = (end_angle - start_angle) % 360.0 or 360.0
                    rect = QRectF(center[0] - radius, -center[1] - radius, radius * 2, radius * 2)
                    path = QPainterPath()
                    path.arcMoveTo(rect, start_angle)
                    path.arcTo(rect, start_angle, sweep)
                    item = self.scene.addPath(path, pen, QBrush(Qt.NoBrush))
                    self._register(item, layer, entity)
            elif kind in {"TEXT", "MTEXT", "ATTRIB"}:
                insert = data.get("insert")
                text = str(data.get("text", "")).strip()
                if insert and text:
                    item = self.scene.addSimpleText(text)
                    item.setBrush(QBrush(PALETTE["text"]))
                    item.setPos(float(insert[0]), -float(insert[1]))
                    self._register(item, layer, entity)

    def _render_rooms(self, rooms):
        pen = QPen(PALETTE["room"])
        pen.setWidthF(1.6)
        pen.setCosmetic(True)
        for room in rooms:
            coords = list(room.polygon.exterior.coords)
            polygon = [QPointF(float(x), -float(y)) for x, y in coords]
            item = QGraphicsPolygonItem(polygon)
            item.setPen(pen)
            item.setBrush(QBrush(PALETTE["room_fill"]))
            item.setData(0, room)
            item.setZValue(-1)  # tint sits behind walls/furniture
            self.scene.addItem(item)
            self._room_items[room.room_id] = item
            point = room.polygon.representative_point()
            label = QGraphicsSimpleTextItem(room.label or room.room_id)
            label.setBrush(QBrush(PALETTE["text"]))
            label.setPos(float(point.x), -float(point.y))
            label.setScale(1.1)
            label.setData(0, room)
            label.setZValue(10)
            self.scene.addItem(label)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._mode in {"line", "rectangle", "circle"}:
            point = self.mapToScene(event.position().toPoint())
            world_point = (float(point.x()), -float(point.y()))
            if self._pending_point is None:
                self._pending_point = world_point
                self.prompt_changed.emit("First point set · click the second point to finish")
            else:
                first, self._pending_point = self._pending_point, None
                x1, y1 = first
                x2, y2 = world_point
                if math.isclose(x1, x2) and math.isclose(y1, y2):
                    self.prompt_changed.emit("Choose a different second point")
                    self._pending_point = first
                    return
                if self._mode == "line":
                    entity = CadEntity("LINE", "MANUS_DRAWN", None, {"start": first, "end": world_point})
                elif self._mode == "circle":
                    entity = CadEntity("CIRCLE", "MANUS_DRAWN", None, {"center": first, "radius": math.hypot(x2 - x1, y2 - y1)})
                else:
                    entity = CadEntity("LWPOLYLINE", "MANUS_DRAWN", None, {"points": [(x1, y1), (x2, y1), (x2, y2), (x1, y2)], "closed": True})
                self.entity_created.emit(entity)
                self.prompt_changed.emit(f"{self._mode.title()} added · select another tool")
            event.accept()
            return
        if event.button() == Qt.LeftButton and self._mode in {"delete", "move"}:
            item = self.itemAt(event.position().toPoint())
            entity = item.data(0) if item else None
            if entity is None or not hasattr(entity, "entity_type"):
                self.prompt_changed.emit("Select a CAD entity; room geometry is protected")
                event.accept()
                return
            point = self.mapToScene(event.position().toPoint())
            world_point = (float(point.x()), -float(point.y()))
            if self._mode == "delete":
                self.entity_deleted.emit(entity)
            elif self._move_entity is None:
                self._move_entity = (entity, world_point)
                self.prompt_changed.emit("Entity selected · click its destination")
            else:
                original, origin = self._move_entity
                self._move_entity = None
                self.entity_moved.emit(original, origin, world_point)
            event.accept()
            return
        if event.button() == Qt.LeftButton and self._mode == "undo":
            self.action_undone.emit()
            event.accept()
            return
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
            pen = QPen(PALETTE["selected"] if key == room_id else PALETTE["room"], 2.4 if key == room_id else 1.6)
            pen.setCosmetic(True)
            item.setPen(pen)

    def fit_all(self):
        rect = self.scene.itemsBoundingRect()
        if rect.isValid() and rect.width() > 1 and rect.height() > 1:
            self.fitInView(rect.adjusted(-80, -80, 80, 80), Qt.KeepAspectRatio)

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
    """Left panel: checkable layer list (colour swatch + visibility) on top,
    project/room tree below."""

    room_clicked = Signal(str)
    layer_toggled = Signal(str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("project_browser")
        self.setMinimumWidth(220)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 8, 10)
        outer.setSpacing(6)

        layers_title = QLabel("LAYERS")
        layers_title.setObjectName("panel_title")
        outer.addWidget(layers_title)

        buttons = QHBoxLayout()
        self.btn_show_all = QPushButton("Show all")
        self.btn_hide_all = QPushButton("Hide all")
        self.btn_show_all.clicked.connect(lambda: self._set_all_layers(True))
        self.btn_hide_all.clicked.connect(lambda: self._set_all_layers(False))
        buttons.addWidget(self.btn_show_all)
        buttons.addWidget(self.btn_hide_all)
        outer.addLayout(buttons)

        self.layer_tree = QTreeWidget()
        self.layer_tree.setHeaderHidden(True)
        self.layer_tree.setRootIsDecorated(False)
        self.layer_tree.itemChanged.connect(self._on_layer_changed)
        outer.addWidget(self.layer_tree, 3)

        project_title = QLabel("PROJECT")
        project_title.setObjectName("panel_title")
        outer.addWidget(project_title)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemClicked.connect(self._on_item_clicked)
        outer.addWidget(self.tree, 2)
        self.set_project_state(None, [])

    # -- layers ------------------------------------------------------------
    def set_layers(self, layers):
        self.layer_tree.blockSignals(True)
        self.layer_tree.clear()
        for name in layers:
            item = QTreeWidgetItem([str(name)])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Checked)
            item.setIcon(0, _swatch(QColor(layer_style(str(name))[2])))
            item.setData(0, Qt.UserRole, str(name))
            self.layer_tree.addTopLevelItem(item)
        self.layer_tree.blockSignals(False)

    def _on_layer_changed(self, item, _column):
        name = item.data(0, Qt.UserRole)
        if name is not None:
            self.layer_toggled.emit(str(name), item.checkState(0) == Qt.Checked)

    def _set_all_layers(self, visible: bool):
        state = Qt.Checked if visible else Qt.Unchecked
        for index in range(self.layer_tree.topLevelItemCount()):
            item = self.layer_tree.topLevelItem(index)
            if item.checkState(0) != state:
                item.setCheckState(0, state)  # emits itemChanged -> layer_toggled

    # -- project tree -------------------------------------------------------
    def set_project_state(self, document, rooms, selected_units=None):
        selected_units = selected_units or {}
        self.tree.clear()
        root = QTreeWidgetItem(["Carrier HVAC project"])
        self.tree.addTopLevelItem(root)
        root.setExpanded(True)
        drawing = QTreeWidgetItem(["Drawing"])
        root.addChild(drawing)
        drawing.addChild(QTreeWidgetItem([Path(document.path).name if document else "No drawing loaded"]))
        building = QTreeWidgetItem([f"Rooms  ({len(rooms)})"])
        root.addChild(building)
        for room in rooms:
            item = QTreeWidgetItem([room.label or room.room_id])
            item.setData(0, Qt.UserRole, room.room_id)
            building.addChild(item)
        root.addChild(QTreeWidgetItem([f"HAP Systems  ({len(selected_units)})"]))
        equipment = QTreeWidgetItem(["Equipment"])
        root.addChild(equipment)
        equipment.addChild(QTreeWidgetItem([f"Indoor Units  ({len(selected_units)})"]))
        equipment.addChild(QTreeWidgetItem(["Outdoor Units  (manual placement)"]))
        reports = QTreeWidgetItem(["Reports"])
        root.addChild(reports)
        reports.addChild(QTreeWidgetItem(["Equipment Schedule"]))
        reports.addChild(QTreeWidgetItem(["Design Validation"]))
        if document:
            self.set_layers(document.layers[:200])
        else:
            self.set_layers([])

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
        self.setStyleSheet(LIGHT_STYLESHEET)  # light CAD look, scoped to this page only
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
        fit_button = QPushButton("Fit view")
        fit_button.setIcon(_icon(self.project_root, "fit_to_page"))
        fit_button.clicked.connect(self.viewport.fit_all)
        row.addWidget(fit_button)
        open_button = QPushButton("Import DWG / DXF")
        open_button.setIcon(_icon(self.project_root, "fileopen"))
        open_button.clicked.connect(self.request_open.emit)
        row.addWidget(open_button)
        export_button = QPushButton("Export HVAC DXF")
        export_button.setIcon(_icon(self.project_root, "export"))
        export_button.setObjectName("btn_success")
        export_button.clicked.connect(self.request_export.emit)
        row.addWidget(export_button)
        self.tool_buttons = {}
        for mode, label, icon_name in (
            ("line", "Draw line", "line_2p"),
            ("rectangle", "Rectangle", "line_rectangle"),
            ("circle", "Circle", "circle_center_point"),
            ("move", "Move", "move_copy"),
            ("delete", "Delete", "delete"),
            ("undo", "Undo", "back"),
            ("select", "Select", "cursor"),
        ):
            tool = QPushButton(label)
            tool.setIcon(_icon(self.project_root, icon_name))
            tool.setIconSize(QSize(16, 16))
            tool.setToolTip(label)
            tool.clicked.connect(lambda _checked=False, value=mode: self.viewport.set_mode(value))
            self.tool_buttons[mode] = tool
            row.addWidget(tool)
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
        self.viewport.entity_created.connect(self._add_entity)
        self.viewport.entity_deleted.connect(self._delete_entity)
        self.viewport.entity_moved.connect(self._move_entity)
        self.viewport.action_undone.connect(self._undo_edit)
        self.viewport.prompt_changed.connect(self.status.setText)
        self.browser.room_clicked.connect(self._select_room_by_id)
        self.browser.layer_toggled.connect(self.viewport.set_layer_visible)
        self._edit_history = []

    def _remember_entities(self):
        if self.document is not None:
            self._edit_history.append(list(self.document.entities))

    def _refresh_after_edit(self):
        self.viewport.render_document(self.document, self.rooms)
        self.browser.set_project_state(self.document, self.rooms, self.selected_units)

    def _add_entity(self, entity):
        if self.document is None:
            self.status.setText("Import a drawing before adding geometry")
            return
        self._remember_entities()
        self.document.entities.append(entity)
        if entity.layer not in self.document.layers:
            self.document.layers.append(entity.layer)
        self._refresh_after_edit()

    def _delete_entity(self, entity):
        if self.document is None or entity not in self.document.entities:
            self.status.setText("Only entities in the current drawing can be deleted")
            return
        self._remember_entities()
        self.document.entities.remove(entity)
        self._refresh_after_edit()
        self.status.setText("Entity deleted · Undo restores it")

    def _move_entity(self, entity, origin, destination):
        if self.document is None or entity not in self.document.entities:
            self.status.setText("Entity is not in the current drawing")
            return
        dx, dy = destination[0] - origin[0], destination[1] - origin[1]
        data = dict(entity.data)
        for key in ("start", "end", "center", "insert"):
            point = data.get(key)
            if point is not None:
                data[key] = (point[0] + dx, point[1] + dy)
        if "points" in data:
            data["points"] = [(x + dx, y + dy) for x, y in data["points"]]
        self._remember_entities()
        self.document.entities[self.document.entities.index(entity)] = replace(entity, data=data)
        self._refresh_after_edit()
        self.status.setText("Entity moved · Undo restores its previous position")

    def _undo_edit(self):
        if self.document is None or not self._edit_history:
            self.status.setText("Nothing to undo")
            return
        self.document.entities[:] = self._edit_history.pop()
        self._refresh_after_edit()
        self.status.setText("Last CAD edit undone")

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
