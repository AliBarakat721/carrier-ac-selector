"""Room/space detection from the neutral CAD document model."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from shapely.geometry import LineString, Polygon
from shapely.ops import polygonize, unary_union
from shapely.validation import explain_validity

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
    unit_status: str = "VERIFIED"
    raw_area: float | None = None
    raw_perimeter: float | None = None
    drawing_units: str = "unitless"
    geometry_diagnostic: str | None = None


def _scaled_metrics(polygon: Polygon, scale: float | None) -> tuple[float, float]:
    if scale is None:
        return float(polygon.area), float(polygon.length)
    return float(polygon.area * scale * scale), float(polygon.length * scale)


def _bulged_points(points: list[tuple[float, float]], bulges: list[float], closed: bool, arc_angle_tolerance: float) -> list[tuple[float, float]]:
    """Approximate LWPOLYLINE bulge arcs with a documented angular tolerance."""

    if not any(abs(value) > 1e-12 for value in bulges):
        return points
    result: list[tuple[float, float]] = []
    count = len(points) if closed else max(0, len(points) - 1)
    for index in range(count):
        start = points[index]
        end = points[(index + 1) % len(points)]
        result.append(start)
        bulge = bulges[index] if index < len(bulges) else 0.0
        if abs(bulge) <= 1e-12:
            continue
        dx, dy = end[0] - start[0], end[1] - start[1]
        chord = math.hypot(dx, dy)
        theta = 4.0 * math.atan(bulge)
        if chord == 0 or abs(theta) < 1e-12:
            continue
        radius = chord * (1.0 + bulge * bulge) / (4.0 * abs(bulge))
        midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
        offset = chord * (1.0 - bulge * bulge) / (4.0 * bulge)
        normal = (-dy / chord, dx / chord)
        center = (midpoint[0] + normal[0] * offset, midpoint[1] + normal[1] * offset)
        start_angle = math.atan2(start[1] - center[1], start[0] - center[0])
        segments = max(2, int(math.ceil(abs(theta) / arc_angle_tolerance)))
        for step in range(1, segments):
            angle = start_angle + theta * step / segments
            result.append((center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle)))
    if not closed and points:
        result.append(points[-1])
    return result


def _closed_polyline_spaces(document: CadDocument, arc_angle_tolerance: float) -> list[RoomSpace]:
    rooms: list[RoomSpace] = []
    index = 1
    scale = document.unit_scale_to_meters
    for entity in document.entities_of_type("LWPOLYLINE", "POLYLINE"):
        if not entity.data.get("closed"):
            continue
        points = _bulged_points(
            entity.data.get("points", []), entity.data.get("bulges", []), True, arc_angle_tolerance
        )
        if len(points) < 3:
            continue
        polygon = Polygon(points)
        if polygon.is_empty:
            continue
        if polygon.is_valid and polygon.area <= 0:
            continue
        geometry_status = "NEEDS_REVIEW" if polygon.is_valid else "INVALID_GEOMETRY"
        diagnostic = None if polygon.is_valid else explain_validity(polygon)
        raw_area = float(polygon.area)
        raw_perimeter = float(polygon.length)
        area, perimeter = _scaled_metrics(polygon, scale)
        rooms.append(
            RoomSpace(
                room_id=f"ROOM-{index:03d}",
                polygon=polygon,
                area=area,
                perimeter=perimeter,
                source="closed_polyline",
                layer=entity.layer,
                status=geometry_status,
                boundary_handles=(entity.handle,) if entity.handle else (),
                unit_status="VERIFIED" if scale is not None else "UNIT_UNKNOWN",
                raw_area=raw_area,
                raw_perimeter=raw_perimeter,
                drawing_units=document.units,
                geometry_diagnostic=diagnostic,
            )
        )
        index += 1
    return rooms


def _snap_line_endpoints(lines: list[tuple[tuple[float, float], tuple[float, float], str | None]], gap_tolerance: float):
    if gap_tolerance <= 0:
        return lines, []
    endpoints = [(point, index) for index, (start, end, _) in enumerate(lines) for point in (start, end)]
    clusters: list[list[tuple[float, float]]] = []
    for point, _ in sorted(endpoints, key=lambda item: (item[0][0], item[0][1], item[1])):
        cluster = next(
            (candidate for candidate in clusters if all(
                math.hypot(point[0] - other[0], point[1] - other[1]) <= gap_tolerance
                for other in candidate
            )),
            None,
        )
        if cluster is None:
            clusters.append([point])
        else:
            cluster.append(point)
    snapped: dict[tuple[float, float], tuple[float, float]] = {
        point: (
            sum(item[0] for item in cluster) / len(cluster),
            sum(item[1] for item in cluster) / len(cluster),
        )
        for cluster in clusters
        for point in cluster
    }
    diagnostics = [
        {"original": point, "snapped_to": target, "distance": math.hypot(point[0] - target[0], point[1] - target[1])}
        for point, target in snapped.items()
        if point != target
    ]
    if gap_tolerance > 0:
        for index, (point, line_index) in enumerate(endpoints):
            nearest = min(
                (math.hypot(point[0] - other[0], point[1] - other[1]) for j, (other, other_line) in enumerate(endpoints) if j != index and other_line != line_index),
                default=None,
            )
            if nearest is not None and gap_tolerance < nearest <= gap_tolerance * 10:
                diagnostics.append({"type": "unresolved_gap", "endpoint": point, "distance": nearest})
    return [(snapped[start], snapped[end], handle) for start, end, handle in lines], diagnostics


def _line_polygon_spaces(document: CadDocument, starting_index: int, tolerance: float, gap_tolerance: float) -> list[RoomSpace]:
    lines: list[LineString] = []
    line_records: list[tuple[LineString, str | None]] = []
    raw_lines = []
    for entity in document.entities_of_type("LINE"):
        start = entity.data.get("start")
        end = entity.data.get("end")
        if start and end and start != end:
            raw_lines.append((start, end, entity.handle))
    snapped_lines, diagnostics = _snap_line_endpoints(raw_lines, gap_tolerance)
    for start, end, handle in snapped_lines:
            if start == end:
                continue
            line = LineString([start, end])
            lines.append(line)
            line_records.append((line, handle))
    if not lines:
        return [], diagnostics

    merged = unary_union(lines)
    scale = document.unit_scale_to_meters
    rooms: list[RoomSpace] = []
    for offset, polygon in enumerate(polygonize(merged), start=starting_index):
        if polygon.is_empty or polygon.area <= 0:
            continue
        handles = tuple(
            handle
            for line, handle in line_records
            if handle and line.distance(polygon.boundary) <= tolerance
        )
        area, perimeter = _scaled_metrics(polygon, scale)
        raw_area = float(polygon.area)
        raw_perimeter = float(polygon.length)
        rooms.append(
            RoomSpace(
                room_id=f"ROOM-{offset:03d}",
                polygon=polygon,
                area=area,
                perimeter=perimeter,
                source="wall_lines",
                status="NEEDS_REVIEW",
                boundary_handles=handles,
                unit_status="VERIFIED" if scale is not None else "UNIT_UNKNOWN",
                raw_area=raw_area,
                raw_perimeter=raw_perimeter,
                drawing_units=document.units,
                geometry_diagnostic=None,
            )
        )
    return rooms, diagnostics


def _iou(left: Polygon, right: Polygon) -> float:
    if not left.is_valid or not right.is_valid:
        return 0.0
    union_area = left.union(right).area
    return left.intersection(right).area / union_area if union_area else 0.0


def _deduplicate_spaces(rooms: list[RoomSpace], threshold: float = 0.98) -> list[RoomSpace]:
    result: list[RoomSpace] = []
    for candidate in rooms:
        if not candidate.polygon.is_valid:
            result.append(candidate)
            continue
        duplicate = next(
            (item for item in result if item.polygon.is_valid and _iou(item.polygon, candidate.polygon) >= threshold),
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
    tolerance: float = 1e-6,
    gap_tolerance: float = 0.0,
    arc_angle_tolerance: float = math.radians(1.0),
    diagnostics: list[dict[str, object]] | None = None,
) -> list[RoomSpace]:
    """Detect candidate rooms.

    ``tolerance`` and ``gap_tolerance`` are in drawing units. Gap snapping is
    opt-in (default 0.0); door/opening semantics are not inferred automatically.
    Endpoint-to-line T-junction snapping is not supported yet.
    """

    document = (
        document_or_path
        if isinstance(document_or_path, CadDocument)
        else read_dxf(document_or_path)
    )
    if tolerance <= 0:
        raise ValueError("Geometry tolerance must be positive")
    if arc_angle_tolerance <= 0:
        raise ValueError("Arc angle tolerance must be positive")
    rooms = _closed_polyline_spaces(document, arc_angle_tolerance)
    if include_wall_lines:
        line_rooms, snap_diagnostics = _line_polygon_spaces(document, len(rooms) + 1, tolerance, gap_tolerance)
        rooms.extend(line_rooms)
        if diagnostics is not None:
            diagnostics.extend(snap_diagnostics)
    return _deduplicate_spaces(rooms)
