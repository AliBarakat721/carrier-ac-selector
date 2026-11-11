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


def _scaled_metrics(polygon: Polygon, scale: float | None) -> tuple[float, float]:
    if scale is None:
        return float(polygon.area), float(polygon.length)
    return float(polygon.area * scale * scale), float(polygon.length * scale)


def _closed_polyline_spaces(document: CadDocument) -> list[RoomSpace]:
    rooms: list[RoomSpace] = []
    index = 1
    scale = document.unit_scale_to_meters
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
        area, perimeter = _scaled_metrics(polygon, scale)
        rooms.append(
            RoomSpace(
                room_id=f"ROOM-{index:03d}",
                polygon=polygon,
                area=area,
                perimeter=perimeter,
                source="closed_polyline",
                layer=entity.layer,
                boundary_handles=(entity.handle,) if entity.handle else (),
            )
        )
        index += 1
    return rooms


def _line_polygon_spaces(document: CadDocument, starting_index: int) -> list[RoomSpace]:
    lines: list[LineString] = []
    line_records: list[tuple[LineString, str | None]] = []
    for entity in document.entities_of_type("LINE"):
        start = entity.data.get("start")
        end = entity.data.get("end")
        if start and end and start != end:
            line = LineString([start, end])
            lines.append(line)
            line_records.append((line, entity.handle))
    if not lines:
        return []

    merged = unary_union(lines)
    scale = document.unit_scale_to_meters
    rooms: list[RoomSpace] = []
    for offset, polygon in enumerate(polygonize(merged), start=starting_index):
        if polygon.is_empty or polygon.area <= 0:
            continue
        handles = tuple(
            handle
            for line, handle in line_records
            if handle and line.distance(polygon.boundary) <= 1e-9
        )
        area, perimeter = _scaled_metrics(polygon, scale)
        rooms.append(
            RoomSpace(
                room_id=f"ROOM-{offset:03d}",
                polygon=polygon,
                area=area,
                perimeter=perimeter,
                source="wall_lines",
                status="NEEDS_REVIEW",
                boundary_handles=handles,
            )
        )
    return rooms


def _iou(left: Polygon, right: Polygon) -> float:
    union_area = left.union(right).area
    return left.intersection(right).area / union_area if union_area else 0.0


def _deduplicate_spaces(rooms: list[RoomSpace], threshold: float = 0.98) -> list[RoomSpace]:
    result: list[RoomSpace] = []
    for candidate in rooms:
        duplicate = next(
            (item for item in result if _iou(item.polygon, candidate.polygon) >= threshold),
            None,
        )
        if duplicate is None:
            result.append(candidate)
        elif duplicate.source != "closed_polyline" and candidate.source == "closed_polyline":
            candidate.boundary_handles = tuple(
                dict.fromkeys(duplicate.boundary_handles + candidate.boundary_handles)
            )
            result[result.index(duplicate)] = candidate
        else:
            duplicate.boundary_handles = tuple(
                dict.fromkeys(duplicate.boundary_handles + candidate.boundary_handles)
            )
    return result


def detect_spaces(
    document_or_path: CadDocument | str | Path,
    *,
    include_wall_lines: bool = True,
) -> list[RoomSpace]:
    """Detect candidate rooms and remove near-identical geometric duplicates."""

    document = (
        document_or_path
        if isinstance(document_or_path, CadDocument)
        else read_dxf(document_or_path)
    )
    rooms = _closed_polyline_spaces(document)
    if include_wall_lines:
        rooms.extend(_line_polygon_spaces(document, len(rooms) + 1))
    return _deduplicate_spaces(rooms)
