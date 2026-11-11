"""Room/space detection from the neutral CAD document model."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from shapely.geometry import LineString, Polygon
from shapely.ops import polygonize, unary_union

from .dxf_reader import CadDocument, read_dxf


@dataclass
class RoomSpace:
    """A candidate room found in a CAD drawing."""

    room_id: str
    polygon: Polygon
    area: float
    perimeter: float
    source: str
    layer: str | None = None
    label: str | None = None
    status: str = "NEEDS_REVIEW"
    boundary_handles: tuple[str, ...] = ()


def _closed_polyline_spaces(document: CadDocument) -> list[RoomSpace]:
    rooms: list[RoomSpace] = []
    index = 1
    for entity in document.entities_of_type("LWPOLYLINE", "POLYLINE"):
        if not entity.data.get("closed"):
            continue
        points = entity.data.get("points", [])
        if len(points) < 3:
            continue
        polygon = Polygon(points)
        if polygon.is_empty or polygon.area <= 0:
            continue
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        if polygon.is_empty or polygon.area <= 0:
            continue
        rooms.append(
            RoomSpace(
                room_id=f"ROOM-{index:03d}",
                polygon=polygon,
                area=float(polygon.area),
                perimeter=float(polygon.length),
                source="closed_polyline",
                layer=entity.layer,
                boundary_handles=(entity.handle,) if entity.handle else (),
            )
        )
        index += 1
    return rooms


def _line_polygon_spaces(document: CadDocument, starting_index: int) -> list[RoomSpace]:
    lines = []
    line_handles: list[str] = []
    for entity in document.entities_of_type("LINE"):
        start = entity.data.get("start")
        end = entity.data.get("end")
        if start and end and start != end:
            lines.append(LineString([start, end]))
            if entity.handle:
                line_handles.append(entity.handle)
    if not lines:
        return []

    merged = unary_union(lines)
    rooms: list[RoomSpace] = []
    for offset, polygon in enumerate(polygonize(merged), start=starting_index):
        if polygon.is_empty or polygon.area <= 0:
            continue
        rooms.append(
            RoomSpace(
                room_id=f"ROOM-{offset:03d}",
                polygon=polygon,
                area=float(polygon.area),
                perimeter=float(polygon.length),
                source="wall_lines",
                status="NEEDS_REVIEW",
                boundary_handles=tuple(line_handles),
            )
        )
    return rooms


def detect_spaces(document_or_path: CadDocument | str | Path, *, include_wall_lines: bool = True) -> list[RoomSpace]:
    """Detect candidate rooms from closed polylines and optionally wall lines.

    Results are candidates, not engineering approval.  Callers should expose
    ``status`` to the user and require confirmation before HVAC assignment.
    """

    document = (
        document_or_path
        if isinstance(document_or_path, CadDocument)
        else read_dxf(document_or_path)
    )
    rooms = _closed_polyline_spaces(document)
    if include_wall_lines:
        rooms.extend(_line_polygon_spaces(document, len(rooms) + 1))
    return rooms
