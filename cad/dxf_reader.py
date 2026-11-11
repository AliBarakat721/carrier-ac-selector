"""DXF reader used by the CAD analysis layer.

The reader intentionally returns a small, serialisable model instead of leaking
 ezdxf entities into the rest of the application.  DWG conversion is kept out
of this module and will be added as a separate adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import ezdxf
from ezdxf.entities import DXFGraphic


_UNITS = {
    0: "unitless",
    1: "inches",
    2: "feet",
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


@dataclass(frozen=True)
class CadEntity:
    """Minimal representation of a DXF entity for downstream analysis."""

    entity_type: str
    layer: str
    handle: str | None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class CadDocument:
    """Unified document model produced from a DXF file."""

    path: Path
    units: str
    unit_code: int
    layers: list[str]
    entities: list[CadEntity]

    @property
    def entity_count(self) -> int:
        return len(self.entities)

    def entities_of_type(self, *entity_types: str) -> list[CadEntity]:
        wanted = {item.upper() for item in entity_types}
        return [item for item in self.entities if item.entity_type.upper() in wanted]


def _point(value: Any) -> tuple[float, float]:
    return (float(value[0]), float(value[1]))


def _entity_data(entity: DXFGraphic) -> dict[str, Any]:
    kind = entity.dxftype()
    if kind == "LINE":
        return {"start": _point(entity.dxf.start), "end": _point(entity.dxf.end)}
    if kind == "LWPOLYLINE":
        return {
            "points": [(float(x), float(y)) for x, y, *_ in entity.get_points("xyseb")],
            "closed": bool(entity.closed),
        }
    if kind == "POLYLINE":
        return {
            "points": [_point(vertex.dxf.location) for vertex in entity.vertices],
            "closed": bool(entity.is_closed),
        }
    if kind in {"TEXT", "MTEXT"}:
        return {
            "text": entity.plain_text() if kind == "MTEXT" else str(entity.dxf.text),
            "insert": _point(entity.dxf.insert),
        }
    if kind == "INSERT":
        return {"block_name": str(entity.dxf.name), "insert": _point(entity.dxf.insert)}
    return {}


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

    for entity in drawing.modelspace():
        if not isinstance(entity, DXFGraphic):
            continue
        entities.append(
            CadEntity(
                entity_type=entity.dxftype(),
                layer=str(entity.dxf.get("layer", "0")),
                handle=getattr(entity.dxf, "handle", None),
                data=_entity_data(entity),
            )
        )

    return CadDocument(
        path=source,
        units=_UNITS.get(unit_code, f"code:{unit_code}"),
        unit_code=unit_code,
        layers=layers,
        entities=entities,
    )
