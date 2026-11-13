"""Room/space detection from the neutral CAD document model."""

from __future__ import annotations

import math
import hashlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, unary_union
from shapely.strtree import STRtree
from shapely.validation import explain_validity

from .dxf_reader import CadDocument, unit_code_for, unit_name, unit_scale_to_meters
from .importer import read_cad_file
from .layer_analyzer import (
    CandidateStatus,
    LayerClass,
    LayerMapping,
    RoomCandidateDecision,
    build_layer_mapping,
    evaluate_room_candidate,
    summarize_decisions,
)


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
    # Populated only by :func:`detect_spaces_with_review`.  ``detect_spaces``
    # leaves these at their neutral defaults so existing callers are unaffected.
    layer_class: str = "UNCLASSIFIED"
    candidate_status: str = CandidateStatus.NEEDS_REVIEW.value
    candidate_reasons: tuple[str, ...] = ()
    #: Layers that contributed boundary segments, most frequent first.  Only
    #: populated for ``wall_lines`` rooms, which have no single source entity.
    boundary_layers: tuple[str, ...] = ()

    @property
    def effective_layer(self) -> str | None:
        """The layer to classify: the entity layer, else the dominant boundary layer."""

        if self.layer:
            return self.layer
        return self.boundary_layers[0] if self.boundary_layers else None

    @property
    def is_usable_room(self) -> bool:
        """True only for a CONFIRMED candidate with no blocking reason."""

        return self.candidate_status == CandidateStatus.CONFIRMED.value


def _scaled_metrics(polygon: Polygon, scale: float | None) -> tuple[float, float]:
    if scale is None:
        return float(polygon.area), float(polygon.length)
    return float(polygon.area * scale * scale), float(polygon.length * scale)


def _curve_points(entity, arc_angle_tolerance: float) -> tuple[list[tuple[float, float]], bool, str]:
    """Return a deterministic planar approximation for supported curve entities."""

    def clean(point: tuple[float, float]) -> tuple[float, float]:
        return tuple(0.0 if abs(value) < 1e-12 else value for value in point)  # type: ignore[return-value]

    kind = entity.entity_type.upper()
    if kind == "CIRCLE":
        center = entity.data["center"]
        radius = float(entity.data["radius"])
        count = max(32, int(math.ceil(2.0 * math.pi / arc_angle_tolerance)))
        points = [
            (
                center[0] + radius * math.cos(2.0 * math.pi * index / count),
                center[1] + radius * math.sin(2.0 * math.pi * index / count),
            )
            for index in range(count)
        ]
        return points, True, "circle"
    if kind == "ARC":
        center = entity.data["center"]
        radius = float(entity.data["radius"])
        start = math.radians(float(entity.data["start_angle"]))
        sweep = (float(entity.data["end_angle"]) - float(entity.data["start_angle"])) % 360.0
        if sweep <= 1e-12:
            sweep = 360.0
        count = max(2, int(math.ceil(math.radians(sweep) / arc_angle_tolerance)) + 1)
        points = [
            (
                center[0] + radius * math.cos(start + math.radians(sweep) * index / (count - 1)),
                center[1] + radius * math.sin(start + math.radians(sweep) * index / (count - 1)),
            )
            for index in range(count)
        ]
        points[0] = clean((
            center[0] + radius * math.cos(start),
            center[1] + radius * math.sin(start),
        ))
        points[-1] = clean((
            center[0] + radius * math.cos(start + math.radians(sweep)),
            center[1] + radius * math.sin(start + math.radians(sweep)),
        ))
        return points, False, "arc"
    if kind == "SPLINE":
        return list(entity.data.get("points", [])), bool(entity.data.get("closed")), "spline"
    return [], False, kind.lower()


def _stable_room_id(polygon: Polygon) -> str:
    """Create an order-independent identifier from the room boundary."""

    coordinates = [(round(x, 8), round(y, 8)) for x, y in polygon.exterior.coords[:-1]]
    if not coordinates:
        digest = hashlib.sha1(b"empty-room").hexdigest()[:12]
    else:
        candidates = []
        for sequence in (coordinates, list(reversed(coordinates))):
            candidates.extend(
                tuple(sequence[index:] + sequence[:index])
                for index in range(len(sequence))
            )
        digest = hashlib.sha1(repr(min(candidates)).encode("utf-8")).hexdigest()[:12]
    return f"ROOM-{digest.upper()}"


def _assign_room_labels(
    rooms: list[RoomSpace],
    document: CadDocument,
    label_layers: set[str] | None,
    label_tolerance: float,
) -> None:
    labels = []
    for entity in document.entities_of_type("TEXT", "MTEXT", "ATTRIB"):
        if label_layers is not None and entity.layer.casefold() not in label_layers:
            continue
        text = str(entity.data.get("text", "")).strip()
        insert = entity.data.get("insert")
        if text and insert:
            labels.append((text, Point(insert)))
    for text, point in labels:
        containing = [room for room in rooms if room.polygon.covers(point)]
        if containing:
            target = min(containing, key=lambda room: room.polygon.area)
        else:
            nearby = [room for room in rooms if room.polygon.distance(point) <= label_tolerance]
            if not nearby:
                continue
            target = min(nearby, key=lambda room: room.polygon.distance(point))
        if target.label is None:
            target.label = text


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
    for entity in document.entities:
        if entity.entity_type.upper() in {"LWPOLYLINE", "POLYLINE"}:
            points = _bulged_points(
                entity.data.get("points", []), entity.data.get("bulges", []), True, arc_angle_tolerance
            ) if entity.data.get("closed") else []
            source = "closed_polyline"
        elif entity.entity_type.upper() == "SOLID":
            # SOLID is a filled triangle or quadrilateral.  The reader has
            # already converted its OCS vertices to correctly ordered WCS
            # points, so it can enter the same polygon/metrics pipeline as a
            # closed polyline without becoming wall linework.
            points = list(entity.data.get("points", [])) if entity.data.get("closed") else []
            source = "solid_polygon"
        elif entity.entity_type.upper() in {"CIRCLE", "SPLINE"}:
            points, closed, source = _curve_points(entity, arc_angle_tolerance)
            if not closed:
                points = []
        else:
            points = []
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
                source=source,
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


def _hatch_polygons(data: dict[str, object]) -> list[Polygon]:
    """Build polygons from HATCH boundary rings, preserving inner holes."""
    rings: list[Polygon] = []
    for path in data.get("paths", []):
        if not isinstance(path, dict) or not path.get("closed"):
            continue
        points = path.get("points", [])
        if not isinstance(points, list) or len(points) < 3:
            continue
        bulges = path.get("bulges", [])
        if isinstance(bulges, list) and any(abs(float(value)) > 1e-12 for value in bulges):
            points = _bulged_points(
                points,
                [float(value) for value in bulges],
                True,
                math.radians(1.0),
            )
        ring = Polygon(points)
        if ring.is_empty or not ring.is_valid or ring.area <= 0:
            continue
        rings.append(ring)
    if not rings:
        return []

    parents: list[int | None] = [None] * len(rings)
    for index, ring in enumerate(rings):
        point = ring.representative_point()
        containing = [
            candidate
            for candidate, other in enumerate(rings)
            if candidate != index and other.area > ring.area and other.covers(point)
        ]
        if containing:
            parents[index] = min(containing, key=lambda candidate: rings[candidate].area)

    depths: list[int] = []
    for index in range(len(rings)):
        depth = 0
        parent = parents[index]
        seen: set[int] = set()
        while parent is not None and parent not in seen:
            seen.add(parent)
            depth += 1
            parent = parents[parent]
        depths.append(depth)

    polygons: list[Polygon] = []
    for index, ring in enumerate(rings):
        if depths[index] % 2:
            continue
        holes = [
            list(rings[child].exterior.coords)
            for child in range(len(rings))
            if parents[child] == index and depths[child] == depths[index] + 1
        ]
        polygons.append(Polygon(ring.exterior.coords, holes))
    return polygons


def _hatch_spaces(document: CadDocument) -> list[RoomSpace]:
    rooms: list[RoomSpace] = []
    scale = document.unit_scale_to_meters
    for entity in document.entities_of_type("HATCH"):
        for polygon in _hatch_polygons(entity.data):
            if polygon.is_empty:
                continue
            geometry_status = "NEEDS_REVIEW" if polygon.is_valid else "INVALID_GEOMETRY"
            diagnostic = None if polygon.is_valid else explain_validity(polygon)
            raw_area = float(polygon.area)
            raw_perimeter = float(polygon.length)
            area, perimeter = _scaled_metrics(polygon, scale)
            rooms.append(
                RoomSpace(
                    room_id=f"ROOM-{len(rooms) + 1:03d}",
                    polygon=polygon,
                    area=area,
                    perimeter=perimeter,
                    source="hatch_polygon",
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
    return rooms


def _snap_line_endpoints(lines: list[tuple[tuple[float, float], tuple[float, float], str | None]], gap_tolerance: float):
    if gap_tolerance <= 0:
        return lines, []
    endpoints = [
        (point, line_index, side, handle)
        for line_index, (start, end, handle) in enumerate(lines)
        for side, point in (("start", start), ("end", end))
    ]
    clusters: list[list[tuple[tuple[float, float], int, str, str | None]]] = []
    for endpoint in sorted(endpoints, key=lambda item: (item[0][0], item[0][1], item[1], item[2])):
        point, line_index, _, _ = endpoint
        cluster = next(
            (candidate for candidate in clusters if all(
                math.hypot(point[0] - other[0], point[1] - other[1]) <= gap_tolerance
                and line_index != other_line
                for other, other_line, _, _ in candidate
            )),
            None,
        )
        if cluster is None:
            clusters.append([endpoint])
        else:
            cluster.append(endpoint)

    # Prefer an existing coordinate that is already shared by the most endpoints.
    # This keeps a precise corner fixed when one nearby endpoint is noisy.
    targets: dict[tuple[int, str], tuple[float, float]] = {
        (line_index, side): min(
            (point for point, _, _, _ in cluster),
            key=lambda candidate: (
                -Counter(item[0] for item in cluster)[candidate],
                candidate[0],
                candidate[1],
            ),
        )
        for cluster in clusters
        for point, line_index, side, _ in cluster
    }
    diagnostics = []
    for point, line_index, side, handle in endpoints:
        target = targets[(line_index, side)]
        distance = math.hypot(point[0] - target[0], point[1] - target[1])
        if distance > 0:
            diagnostics.append(
                {
                    "type": "snapped_endpoint",
                    "original": point,
                    "snapped_to": target,
                    "distance": distance,
                    "handle": handle,
                    "endpoint": side,
                }
            )

    points = [Point(point) for point, _, _, _ in endpoints]
    tree = STRtree(points)
    max_x = max(point[0] for point, _, _, _ in endpoints)
    min_x = min(point[0] for point, _, _, _ in endpoints)
    max_y = max(point[1] for point, _, _, _ in endpoints)
    min_y = min(point[1] for point, _, _, _ in endpoints)
    search_limit = math.hypot(max_x - min_x, max_y - min_y) + gap_tolerance
    unresolved_pairs: dict[tuple[int, int], tuple[int, int, float]] = {}
    for index, (point, line_index, _, _) in enumerate(endpoints):
        radius = gap_tolerance
        candidates: list[int] = []
        while not candidates and radius <= search_limit:
            candidates = [
                int(candidate)
                for candidate in tree.query(points[index], predicate="dwithin", distance=radius)
                if int(candidate) != index and endpoints[int(candidate)][1] != line_index
            ]
            radius = max(radius * 2.0, 1e-9)
        if not candidates:
            continue
        nearest_index = min(
            candidates,
            key=lambda candidate: points[index].distance(points[candidate]),
        )
        distance = points[index].distance(points[nearest_index])
        if distance <= gap_tolerance:
            continue
        pair = tuple(sorted((index, nearest_index)))
        unresolved_pairs[pair] = (pair[0], pair[1], distance)

    for first, second, distance in unresolved_pairs.values():
        first_point, _, _, first_handle = endpoints[first]
        second_point, _, _, second_handle = endpoints[second]
        diagnostics.append(
            {
                "type": "unresolved_gap",
                "endpoint": first_point,
                "other_endpoint": second_point,
                "distance": distance,
                "handle": first_handle,
                "other_handle": second_handle,
            }
        )

    snapped_lines = [
        (targets[(line_index, "start")], targets[(line_index, "end")], handle)
        for line_index, (_, _, handle) in enumerate(lines)
    ]
    return snapped_lines, diagnostics


def _split_t_junctions(
    lines: list[tuple[tuple[float, float], tuple[float, float], str | None]],
    tolerance: float,
) -> tuple[list[tuple[tuple[float, float], tuple[float, float], str | None]], list[dict[str, object]]]:
    """Split a host segment where another segment endpoint touches its interior."""

    geometries = [LineString([start, end]) for start, end, _ in lines]
    split_positions: list[list[float]] = [[] for _ in lines]
    diagnostics: list[dict[str, object]] = []
    for endpoint_index, (start, end, endpoint_handle) in enumerate(lines):
        for endpoint in (start, end):
            point = Point(endpoint)
            for host_index, host in enumerate(geometries):
                if endpoint_index == host_index or host.length == 0:
                    continue
                distance_along = host.project(point)
                if distance_along <= tolerance or host.length - distance_along <= tolerance:
                    continue
                if host.distance(point) > tolerance:
                    continue
                split_positions[host_index].append(distance_along)
                diagnostics.append(
                    {
                        "type": "t_junction",
                        "handle": endpoint_handle,
                        "host_handle": lines[host_index][2],
                        "point": (float(point.x), float(point.y)),
                    }
                )

    result: list[tuple[tuple[float, float], tuple[float, float], str | None]] = []
    for index, ((start, end, handle), geometry) in enumerate(zip(lines, geometries)):
        distances = [0.0, *sorted(set(split_positions[index])), geometry.length]
        for left, right in zip(distances, distances[1:]):
            if right - left <= tolerance:
                continue
            first = geometry.interpolate(left)
            last = geometry.interpolate(right)
            result.append(((float(first.x), float(first.y)), (float(last.x), float(last.y)), handle))
    return result, diagnostics


def _line_polygon_spaces(
    document: CadDocument,
    starting_index: int,
    tolerance: float,
    gap_tolerance: float,
    arc_angle_tolerance: float,
    wall_layers: set[str] | None = None,
) -> tuple[list[RoomSpace], list[dict[str, object]]]:
    lines: list[LineString] = []
    line_records: list[tuple[LineString, str | None]] = []
    handle_layers = {
        entity.handle: entity.layer
        for entity in document.entities
        if entity.handle
    }
    raw_lines = []
    for entity in document.entities:
        if wall_layers is not None and entity.layer.casefold() not in wall_layers:
            continue
        kind = entity.entity_type.upper()
        if kind == "LINE":
            start = entity.data.get("start")
            end = entity.data.get("end")
            if start and end and start != end:
                raw_lines.append((start, end, entity.handle))
        elif kind in {"LWPOLYLINE", "POLYLINE"}:
            points = _bulged_points(
                entity.data.get("points", []),
                entity.data.get("bulges", []),
                bool(entity.data.get("closed")),
                arc_angle_tolerance,
            )
            if len(points) >= 2:
                segments = list(zip(points, points[1:]))
                if entity.data.get("closed") and len(points) >= 3:
                    segments.append((points[-1], points[0]))
                raw_lines.extend(
                    (start, end, entity.handle)
                    for start, end in segments
                    if start != end
                )
        elif kind in {"ARC", "SPLINE"}:
            points, closed, _ = _curve_points(entity, arc_angle_tolerance)
            if not closed:
                raw_lines.extend((start, end, entity.handle) for start, end in zip(points, points[1:]) if start != end)
    snapped_lines, diagnostics = _snap_line_endpoints(raw_lines, gap_tolerance)
    snapped_lines, t_junction_diagnostics = _split_t_junctions(snapped_lines, tolerance)
    diagnostics.extend(t_junction_diagnostics)
    snapped_handles = {
        item["handle"]
        for item in diagnostics
        if item.get("type") == "snapped_endpoint" and item.get("handle")
    }
    for start, end, handle in snapped_lines:
            if start == end:
                diagnostics.append({"type": "collapsed_line", "start": start, "end": end, "handle": handle})
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
        handles = tuple(dict.fromkeys(
            handle
            for line, handle in line_records
            if handle and line.distance(polygon.boundary) <= tolerance
        ))
        area, perimeter = _scaled_metrics(polygon, scale)
        raw_area = float(polygon.area)
        raw_perimeter = float(polygon.length)
        # A polygon built from wall linework has no single source entity, so the
        # layer is the most common layer among its contributing boundaries.
        # Without this, every wall-line room would be reported as UNCLASSIFIED.
        layer_counts = Counter(
            handle_layers[handle] for handle in handles if handle in handle_layers
        )
        dominant_layer = layer_counts.most_common(1)[0][0] if layer_counts else None
        rooms.append(
            RoomSpace(
                room_id=f"ROOM-{offset:03d}",
                polygon=polygon,
                area=area,
                perimeter=perimeter,
                source="wall_lines",
                boundary_layers=tuple(
                    layer_name
                    for layer_name, _ in sorted(
                        layer_counts.items(), key=lambda item: (-item[1], item[0])
                    )
                ),
                status="SNAPPED" if snapped_handles.intersection(handles) else "NEEDS_REVIEW",
                boundary_handles=handles,
                unit_status="VERIFIED" if scale is not None else "UNIT_UNKNOWN",
                raw_area=raw_area,
                raw_perimeter=raw_perimeter,
                drawing_units=document.units,
                geometry_diagnostic=(
                    "SNAPPED: " + ", ".join(sorted(snapped_handles.intersection(handles)))
                    if snapped_handles.intersection(handles)
                    else None
                ),
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
    wall_layers: set[str] | list[str] | tuple[str, ...] | None = None,
    label_layers: set[str] | list[str] | tuple[str, ...] | None = None,
    label_tolerance: float = 0.25,
    tolerance: float = 1e-6,
    gap_tolerance: float = 0.0,
    arc_angle_tolerance: float = math.radians(1.0),
    diagnostics: list[dict[str, object]] | None = None,
) -> list[RoomSpace]:
    """Detect candidate rooms.

    ``tolerance`` and ``gap_tolerance`` are in drawing units. Gap snapping is
    opt-in (default 0.0); door/opening semantics are not inferred automatically.
    Endpoint-to-line T-junctions are split when the endpoint is within
    ``tolerance`` of a host segment interior. ``wall_layers`` is optional;
    when supplied, only LINE, ARC, SPLINE, and polyline entities on those
    layers participate in wall polygonization.
    """

    document = (
        document_or_path
        if isinstance(document_or_path, CadDocument)
        else read_cad_file(document_or_path)
    )
    if tolerance <= 0:
        raise ValueError("Geometry tolerance must be positive")
    if arc_angle_tolerance <= 0:
        raise ValueError("Arc angle tolerance must be positive")
    if label_tolerance < 0:
        raise ValueError("Label tolerance cannot be negative")
    normalized_wall_layers = (
        {str(layer).casefold() for layer in wall_layers}
        if wall_layers is not None
        else None
    )
    normalized_label_layers = (
        {str(layer).casefold() for layer in label_layers}
        if label_layers is not None
        else None
    )
    rooms = _closed_polyline_spaces(document, arc_angle_tolerance)
    rooms.extend(_hatch_spaces(document))
    if include_wall_lines:
        line_rooms, snap_diagnostics = _line_polygon_spaces(
            document,
            len(rooms) + 1,
            tolerance,
            gap_tolerance,
            arc_angle_tolerance,
            normalized_wall_layers,
        )
        rooms.extend(line_rooms)
        if diagnostics is not None:
            diagnostics.extend(snap_diagnostics)
    rooms = _deduplicate_spaces(rooms)
    _assign_room_labels(rooms, document, normalized_label_layers, label_tolerance)
    for room in rooms:
        room.room_id = _stable_room_id(room.polygon)
    return rooms


@dataclass
class RoomDetectionReport:
    """Result of :func:`detect_spaces_with_review`.

    The report keeps *every* detected polygon, including rejected ones, so the
    UI can show why something was ignored instead of silently hiding it.
    """

    rooms: list[RoomSpace]
    layer_mapping: LayerMapping
    diagnostics: list[dict[str, object]]
    units_status: str
    units_note: str
    min_area_m2: float
    max_area_m2: float | None = None
    #: ``"DRAWING"`` when read from ``$INSUNITS``, ``"USER_CONFIRMED"`` when the
    #: reviewer supplied the units, ``"UNVERIFIED"`` when neither happened.
    units_source: str = "DRAWING"

    def _by_status(self, status: CandidateStatus) -> list[RoomSpace]:
        return [room for room in self.rooms if room.candidate_status == status.value]

    @property
    def accepted(self) -> list[RoomSpace]:
        return self._by_status(CandidateStatus.CONFIRMED)

    @property
    def needs_review(self) -> list[RoomSpace]:
        return self._by_status(CandidateStatus.NEEDS_REVIEW)

    @property
    def rejected(self) -> list[RoomSpace]:
        return self._by_status(CandidateStatus.REJECTED)

    @property
    def blocked_for_engineering(self) -> bool:
        """True when at least one detected polygon still needs a human decision."""

        return bool(self.needs_review)

    def summary(self) -> dict[str, int]:
        counts = summarize_decisions(
            [
                RoomCandidateDecision(
                    status=CandidateStatus(room.candidate_status),
                    reasons=room.candidate_reasons,
                )
                for room in self.rooms
            ]
        )
        counts["total"] = len(self.rooms)
        return counts

    def reasons_table(self) -> list[dict[str, object]]:
        """Flat rows for a review table: one row per room with joined reasons."""

        return [
            {
                "room_id": room.room_id,
                "label": room.label or "",
                "layer": room.layer or "",
                "layer_class": room.layer_class,
                "source": room.source,
                "area_m2": round(room.area, 3),
                "status": room.candidate_status,
                "reasons": "; ".join(room.candidate_reasons),
            }
            for room in self.rooms
        ]


def _units_note(document: CadDocument) -> tuple[str, str]:
    if document.units_verified:
        return (
            "VERIFIED",
            f"$INSUNITS={document.unit_code} ({document.units}); "
            f"scale to metres = {document.unit_scale_to_meters}",
        )
    return (
        "UNVERIFIED",
        f"$INSUNITS={document.unit_code} ({document.units}). Areas and lengths are "
        "raw drawing units and must not be used in engineering calculations until "
        "the user confirms the drawing units.",
    )


def detect_spaces_with_review(
    document_or_path: CadDocument | str | Path,
    *,
    layer_mapping: LayerMapping | None = None,
    layer_overrides: dict[str, LayerClass | str] | None = None,
    min_area_m2: float = 1.0,
    max_area_m2: float | None = None,
    allow_circular_curves: bool = False,
    require_label: bool = False,
    units_override: int | str | None = None,
    include_wall_lines: bool = True,
    tolerance: float = 1e-6,
    gap_tolerance: float = 0.0,
    label_tolerance: float = 0.25,
    arc_angle_tolerance: float = math.radians(1.0),
    diagnostics: list[dict[str, object]] | None = None,
) -> RoomDetectionReport:
    """Detect rooms **and** classify every candidate for human review.

    This is the Phase 3 entry point.  It never silently promotes a polygon to a
    room: each candidate carries ``candidate_status`` and ``candidate_reasons``.

    Behaviour that differs from :func:`detect_spaces`:

    * Wall polygonization only runs on layers classified as ``WALLS`` or
      ``ROOM_BOUNDARIES``.  If no such layer exists, wall-line detection is
      skipped and a ``NO_WALL_LAYER_IDENTIFIED`` diagnostic is recorded instead
      of polygonizing the whole drawing.
    * Room labels are only read from layers classified as ``TEXT_LABELS``.
    * Polygons on furniture / door / window / column / dimension / text layers
      are rejected even when they are perfectly closed.
    * Polygons smaller than ``min_area_m2`` are rejected.
    * When ``$INSUNITS`` is missing, every candidate is flagged
      ``DRAWING_UNITS_UNVERIFIED``.  Passing ``units_override`` (a DXF unit code
      or a unit name such as ``"meters"``) records that the reviewer confirmed
      the drawing units, and areas are then scaled with that unit.
    """

    document = (
        document_or_path
        if isinstance(document_or_path, CadDocument)
        else read_cad_file(document_or_path)
    )

    mapping = layer_mapping or build_layer_mapping(document.layers, layer_overrides)
    collected: list[dict[str, object]] = []
    wall_layers = mapping.wall_layers()
    label_layers = mapping.label_layers()
    if not label_layers:
        collected.append(
            {
                "type": "no_label_layer_identified",
                "message": (
                    "No layer could be classified as TEXT_LABELS. Room labels will "
                    "be read from any layer; confirm the label layer in review."
                ),
            }
        )
    effective_wall_detection = include_wall_lines
    if include_wall_lines and not wall_layers:
        effective_wall_detection = False
        collected.append(
            {
                "type": "no_wall_layer_identified",
                "message": (
                    "No layer could be classified as WALLS or ROOM_BOUNDARIES. "
                    "Wall-line room detection was skipped; classify the wall layer "
                    "explicitly before relying on detected areas."
                ),
                "layers": list(mapping.classifications),
            }
        )

    rooms = detect_spaces(
        document,
        include_wall_lines=effective_wall_detection,
        wall_layers=wall_layers or None,
        label_layers=label_layers or None,
        label_tolerance=label_tolerance,
        tolerance=tolerance,
        gap_tolerance=gap_tolerance,
        arc_angle_tolerance=arc_angle_tolerance,
        diagnostics=collected,
    )

    units_status, units_note = _units_note(document)
    units_verified = document.units_verified
    scale = document.unit_scale_to_meters
    units_source = "DRAWING" if units_verified else "UNVERIFIED"
    resolved_units = document.units
    if units_override is not None:
        override_scale = unit_scale_to_meters(units_override)
        if override_scale is None:
            raise ValueError(
                f"Unsupported drawing units {units_override!r}. "
                f"Use a DXF unit code or one of the names: "
                f"{', '.join(sorted({'meters', 'millimeters', 'centimeters', 'inches', 'feet'}))}."
            )
        code = unit_code_for(units_override)
        resolved = unit_name(code) if code is not None else str(units_override)
        units_status = "USER_CONFIRMED"
        units_note = (
            f"Units confirmed by the reviewer as {resolved} "
            f"(scale to metres = {override_scale}); "
            f"drawing header reported $INSUNITS={document.unit_code} ({document.units})."
        )
        units_verified = True
        scale = override_scale
        units_source = "USER_CONFIRMED"
        resolved_units = resolved

    for room in rooms:
        classification = mapping.classification(room.effective_layer)
        if units_source == "USER_CONFIRMED" and scale is not None:
            # The detector scaled with the *drawing* units (possibly none).  The
            # reviewer's confirmed units win, so rescale from the raw polygon.
            room.area = float(room.polygon.area * scale * scale)
            room.perimeter = float(room.polygon.length * scale)
            room.unit_status = "VERIFIED"
            room.drawing_units = resolved_units
        area_m2 = room.area if scale is not None else None
        decision = evaluate_room_candidate(
            layer_class=classification.layer_class,
            source=room.source,
            area_m2=area_m2,
            has_label=bool(room.label),
            units_verified=units_verified,
            geometry_valid=room.polygon.is_valid,
            ambiguous=classification.is_ambiguous,
            unconfirmed=classification.needs_user_confirmation,
            min_area_m2=min_area_m2,
            max_area_m2=max_area_m2,
            allow_circular_curves=allow_circular_curves,
        )
        reasons = list(decision.reasons)
        if require_label and not room.label:
            reasons.append("LABEL_REQUIRED")
            decision = RoomCandidateDecision(
                status=CandidateStatus.NEEDS_REVIEW, reasons=tuple(reasons)
            )
        room.layer_class = classification.layer_class.value
        room.candidate_status = decision.status.value
        room.candidate_reasons = tuple(dict.fromkeys(reasons))

    if diagnostics is not None:
        diagnostics.extend(collected)

    return RoomDetectionReport(
        rooms=rooms,
        layer_mapping=mapping,
        diagnostics=collected,
        units_status=units_status,
        units_note=units_note,
        min_area_m2=min_area_m2,
        max_area_m2=max_area_m2,
        units_source=units_source,
    )
