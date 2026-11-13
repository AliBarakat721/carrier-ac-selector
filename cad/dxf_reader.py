"""DXF reader used by the CAD analysis layer.

The reader returns a small, serialisable model instead of leaking ezdxf
entities into the rest of the application. DWG conversion is handled by the
separate importer adapter. Geometry inside INSERT references is expanded into
its transformed model-space representation while retaining the source INSERT.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

import ezdxf
from ezdxf.entities import DXFGraphic
from ezdxf.math import OCS


_UNITS = {
    0: "unitless",
    1: "inches",
    2: "feet",
    3: "miles",
    4: "millimeters",
    5: "centimeters",
    6: "meters",
    7: "kilometers",
    8: "microinches",
    9: "mils",
    10: "yards",
    11: "angstroms",
    12: "nanometers",
    13: "microns",
    14: "decimeters",
    15: "decameters",
    16: "hectometers",
    17: "gigameters",
    18: "astronomical_units",
    19: "light_years",
    20: "parsecs",
}

_UNIT_SCALE_TO_METERS = {
    0: None,
    1: 0.0254,
    2: 0.3048,
    3: 1609.344,
    4: 0.001,
    5: 0.01,
    6: 1.0,
    7: 1000.0,
    8: 0.0000000254,
    9: 0.0000254,
    10: 0.9144,
    11: 1e-10,
    12: 1e-9,
    13: 1e-6,
    14: 0.1,
    15: 10.0,
    16: 100.0,
    17: 1_000_000_000.0,
    18: 149_597_870_700.0,
    19: 9_460_730_472_580_800.0,
    20: 3.085_677_581_491_367e16,
}


#: Unit names a user may pick in the review UI, mapped to their DXF unit code.
#: Only length units that make sense for a building drawing are offered.
REVIEWABLE_UNITS: dict[str, int] = {
    "millimeters": 4,
    "centimeters": 5,
    "meters": 6,
    "inches": 1,
    "feet": 2,
}


def unit_name(unit_code: int) -> str:
    """Human-readable name for a DXF ``$INSUNITS`` code."""

    return _UNITS.get(int(unit_code), f"code:{unit_code}")


def unit_scale_to_meters(unit: int | str | None) -> float | None:
    """Metres per drawing unit for a unit code **or** a unit name.

    Returns ``None`` for unitless/unknown input so callers keep treating the
    drawing as unverified instead of silently assuming metres.
    """

    if unit is None:
        return None
    if isinstance(unit, int):
        return _UNIT_SCALE_TO_METERS.get(unit)
    name = str(unit).strip().casefold()
    for code, label in _UNITS.items():
        if label.casefold() == name:
            return _UNIT_SCALE_TO_METERS.get(code)
    return None


def unit_code_for(unit: int | str | None) -> int | None:
    """Resolve a unit code from a code or a unit name (``None`` if unknown)."""

    if unit is None:
        return None
    if isinstance(unit, int):
        return unit if unit in _UNIT_SCALE_TO_METERS else None
    name = str(unit).strip().casefold()
    for code, label in _UNITS.items():
        if label.casefold() == name:
            return code
    return None


@dataclass(frozen=True)
class CadEntity:
    """Minimal representation of a DXF entity for downstream analysis."""

    entity_type: str
    layer: str
    handle: str | None
    data: dict[str, Any] = field(default_factory=dict)
    source_insert_handles: tuple[str, ...] = ()


@dataclass
class CadDocument:
    """Unified document model produced from a DXF file."""

    path: Path
    units: str
    unit_code: int
    layers: list[str]
    entities: list[CadEntity]

    @property
    def unit_scale_to_meters(self) -> float | None:
        return _UNIT_SCALE_TO_METERS.get(self.unit_code)

    @property
    def units_verified(self) -> bool:
        return self.unit_scale_to_meters is not None

    @property
    def entity_count(self) -> int:
        return len(self.entities)

    def entities_of_type(self, *entity_types: str) -> list[CadEntity]:
        wanted = {item.upper() for item in entity_types}
        return [item for item in self.entities if item.entity_type.upper() in wanted]


def _point(value: Any) -> tuple[float, float]:
    return (float(value[0]), float(value[1]))


def _hatch_data(entity: DXFGraphic) -> dict[str, Any]:
    """Return serialisable closed boundary rings from a HATCH entity.

    HATCH points are stored in the entity OCS.  Converting every supported
    boundary path to line edges gives downstream code one stable 2-D format,
    while ezdxf performs the arc/ellipse/spline approximation.
    """
    try:
        entity.paths.all_to_line_edges(num=64, spline_factor=8)
    except (AttributeError, ValueError, TypeError):
        return {"paths": [], "solid_fill": bool(entity.dxf.get("solid_fill", 0))}

    extrusion = entity.dxf.get("extrusion", (0.0, 0.0, 1.0))
    elevation = entity.dxf.get("elevation", (0.0, 0.0, 0.0))
    ocs = OCS(extrusion)
    rings: list[dict[str, Any]] = []
    for path in entity.paths:
        vertices = getattr(path, "vertices", None)
        if vertices is not None:
            if not getattr(path, "is_closed", False) or len(vertices) < 3:
                continue
            points = []
            bulges = []
            for vertex in vertices:
                point = ocs.to_wcs((float(vertex[0]), float(vertex[1]), float(elevation[2])))
                points.append((float(point.x), float(point.y)))
                bulges.append(float(vertex[2]) if len(vertex) > 2 else 0.0)
            rings.append(
                {
                    "points": points,
                    "bulges": bulges,
                    "closed": True,
                    "path_type_flags": int(getattr(path, "path_type_flags", 0)),
                }
            )
            continue
        edges = getattr(path, "edges", None)
        if not edges:
            continue
        points: list[tuple[float, float]] = []
        for index, edge in enumerate(edges):
            start = getattr(edge, "start", None)
            end = getattr(edge, "end", None)
            if start is None or end is None:
                continue
            if not points:
                point = ocs.to_wcs((float(start[0]), float(start[1]), float(elevation[2])))
                points.append((float(point.x), float(point.y)))
            point = ocs.to_wcs((float(end[0]), float(end[1]), float(elevation[2])))
            points.append((float(point.x), float(point.y)))
        if len(points) < 4:
            continue
        if points[0] != points[-1]:
            continue
        rings.append(
            {
                "points": points[:-1],
                "bulges": [0.0] * (len(points) - 1),
                "closed": True,
                "path_type_flags": int(getattr(path, "path_type_flags", 0)),
            }
        )
    return {
        "paths": rings,
        "solid_fill": bool(entity.dxf.get("solid_fill", 0)),
    }


def _entity_data(entity: DXFGraphic) -> dict[str, Any]:
    kind = entity.dxftype()
    if kind == "LINE":
        return {"start": _point(entity.dxf.start), "end": _point(entity.dxf.end)}
    if kind == "SOLID":
        # A 2D SOLID is a filled triangular or quadrilateral boundary. Use WCS
        # vertices so SOLIDs expanded from INSERT blocks retain transforms.
        # ezdxf versions differ in whether the final two vertices are already
        # restored to perimeter order; only repair the unambiguously crossed
        # zero-area ordering.
        vertices = list(entity.wcs_vertices(close=False))
        points = [(float(vertex.x), float(vertex.y)) for vertex in vertices]
        if len(points) == 4:
            if points[2] == points[3]:
                points = points[:3]
            elif abs(sum(
                points[index][0] * points[(index + 1) % 4][1]
                - points[(index + 1) % 4][0] * points[index][1]
                for index in range(4)
            )) <= 1e-12:
                points = [points[0], points[1], points[3], points[2]]
        return {
            "points": points,
            "z_values": [float(vertex.z) for vertex in vertices],
            "closed": len(points) >= 3,
        }
    if kind == "LWPOLYLINE":
        points = list(entity.get_points("xyseb"))
        return {
            "points": [(float(point[0]), float(point[1])) for point in points],
            "bulges": [float(point[4]) for point in points],
            "closed": bool(entity.closed),
        }
    if kind == "POLYLINE":
        return {
            "points": [_point(vertex.dxf.location) for vertex in entity.vertices],
            "bulges": [float(vertex.dxf.get("bulge", 0.0) or 0.0) for vertex in entity.vertices],
            "closed": bool(entity.is_closed),
        }
    if kind == "ARC":
        return {
            "center": _point(entity.dxf.center),
            "radius": float(entity.dxf.radius),
            "start_angle": float(entity.dxf.start_angle),
            "end_angle": float(entity.dxf.end_angle),
        }
    if kind == "CIRCLE":
        return {
            "center": _point(entity.dxf.center),
            "radius": float(entity.dxf.radius),
        }
    if kind == "HATCH":
        return _hatch_data(entity)
    if kind == "SPLINE":
        # Keep the reader independent of ezdxf entity classes by storing a
        # deterministic polyline approximation for downstream polygonizing.
        points = list(entity.flattening(0.01))
        return {
            "points": [_point(point) for point in points],
            "closed": bool(entity.dxf.get("flags", 0) & 1),
        }
    if kind in {"TEXT", "MTEXT", "ATTRIB"}:
        height = (
            entity.dxf.get("char_height", 0.0)
            if kind == "MTEXT"
            else entity.dxf.get("height", 0.0)
        )
        return {
            "text": entity.plain_text() if kind == "MTEXT" else str(entity.dxf.text),
            "insert": _point(entity.dxf.insert),
            "height": float(height or 0.0),
            "rotation": float(entity.dxf.get("rotation", 0.0) or 0.0),
        }
    if kind == "INSERT":
        return {"block_name": str(entity.dxf.name), "insert": _point(entity.dxf.insert)}
    return {}


def _iter_modelspace_entities(
    entities: Iterable[DXFGraphic], *, max_block_depth: int = 32
) -> Iterator[tuple[DXFGraphic, str, tuple[str, ...]]]:
    """Yield model-space entities and transformed contents of nested blocks.

    ``virtual_entities()`` applies an INSERT's location, rotation and scale.
    Layer-0 entities inherit the containing INSERT layer, matching AutoCAD's
    block-layer convention. ``source_insert_handles`` points back to the
    physical model-space INSERT for geometry that ezdxf materializes virtually.
    """

    def walk(
        items: Iterable[DXFGraphic],
        inherited_layer: str | None,
        insert_handles: tuple[str, ...],
        depth: int,
    ) -> Iterator[tuple[DXFGraphic, str, tuple[str, ...]]]:
        for entity in items:
            if not isinstance(entity, DXFGraphic):
                continue
            layer = str(entity.dxf.get("layer", "0"))
            effective_layer = inherited_layer if layer == "0" and inherited_layer else layer
            if entity.dxftype() != "INSERT":
                yield entity, effective_layer, insert_handles
                continue

            # Keep INSERT records in the neutral model for diagnostics and block
            # counts, then add each rendered instance's transformed contents.
            yield entity, effective_layer, insert_handles
            if depth >= max_block_depth:
                raise ValueError(
                    f"Block nesting exceeds {max_block_depth} levels at "
                    f"INSERT {entity.dxf.get('handle', 'unknown')} "
                    f"({entity.dxf.get('name', 'unnamed')})."
                )

            handle = entity.dxf.get("handle")
            next_handles = insert_handles
            if handle and (not next_handles or next_handles[-1] != str(handle)):
                next_handles = (*next_handles, str(handle))
            try:
                instances = entity.multi_insert() if entity.mcount > 1 else (entity,)
                for instance in instances:
                    for child in instance.virtual_entities():
                        child_layer = str(child.dxf.get("layer", "0"))
                        if child_layer == "0" and effective_layer != "0":
                            # virtual entities are detached copies; resolving the
                            # inherited layer here does not alter the source DXF.
                            child.dxf.layer = effective_layer
                        yield from walk((child,), effective_layer, next_handles, depth + 1)
                    for attribute in getattr(instance, "attribs", ()):
                        attribute_layer = str(attribute.dxf.get("layer", "0"))
                        if attribute_layer == "0" and effective_layer != "0":
                            attribute.dxf.layer = effective_layer
                        yield attribute, (
                            effective_layer if attribute_layer == "0" else attribute_layer
                        ), next_handles
            except Exception as exc:
                name = str(entity.dxf.get("name", "unnamed"))
                insert_handle = str(handle) if handle else "unknown"
                raise ValueError(
                    f"Could not expand DXF block '{name}' at INSERT "
                    f"{insert_handle}: {exc}"
                ) from exc

    yield from walk(entities, None, (), 0)


def read_dxf(path: str | Path) -> CadDocument:
    """Read a DXF file and return the application-neutral CAD model."""

    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"DXF file not found: {source}")

    try:
        drawing = ezdxf.readfile(source)
    except ezdxf.DXFStructureError as exc:
        raise ValueError(f"Invalid or corrupted DXF file: {source}") from exc

    unit_code = int(drawing.header.get("$INSUNITS", 0) or 0)
    layers = sorted(str(layer.dxf.name) for layer in drawing.layers)
    entities: list[CadEntity] = []

    for entity, layer, source_insert_handles in _iter_modelspace_entities(
        drawing.modelspace()
    ):
        entity_handle = entity.dxf.get("handle") or (
            source_insert_handles[-1] if source_insert_handles else None
        )
        entities.append(
            CadEntity(
                entity_type=entity.dxftype(),
                layer=layer,
                handle=str(entity_handle) if entity_handle else None,
                data=_entity_data(entity),
                source_insert_handles=source_insert_handles,
            )
        )

    return CadDocument(
        path=source,
        units=_UNITS.get(unit_code, f"code:{unit_code}"),
        unit_code=unit_code,
        layers=layers,
        entities=entities,
    )
