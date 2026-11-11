"""Application-level orchestration for the CAD + HAP placement workflow.

This module deliberately contains no Qt code.  It converts the existing HAP
selection-cart records into the neutral records consumed by the CAD exporter.
Indoor units are placed at representative points inside assigned rooms. Outdoor
units are not placed automatically because a room polygon is not a safe proxy
for an outdoor-unit location.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Mapping, Sequence

from .space_detector import RoomSpace


@dataclass(frozen=True)
class RoomAssignment:
    """A user-approved relationship between one detected room and one HAP system."""

    room_id: str
    system_id: str


def _text(value: object) -> str:
    if value is None:
        return ""
    value = str(value).strip()
    return "" if value.casefold() in {"none", "nan", "null"} else value


def _key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", _text(value).casefold())


def suggest_room_assignments(
    rooms: Sequence[RoomSpace],
    systems: Sequence[object],
) -> dict[str, str]:
    """Suggest, but do not force, room-to-system assignments.

    Exact label/name matches win. If the counts are equal, the remaining rooms
    and systems are paired in stable drawing/report order. With unequal counts,
    unmatched rooms remain blank for explicit user mapping in the UI.
    """

    system_names = [_text(getattr(system, "name", system)) for system in systems]
    result: dict[str, str] = {}
    unused = list(system_names)
    for room in rooms:
        label = _key(room.label)
        if not label:
            continue
        match = next((name for name in unused if _key(name) == label), None)
        if match:
            result[room.room_id] = match
            unused.remove(match)
    remaining_rooms = [room for room in rooms if room.room_id not in result]
    if len(remaining_rooms) == len(unused):
        for room, system_name in zip(remaining_rooms, unused):
            result[room.room_id] = system_name
    return result


def _row_value(row: object, *names: str, default: object = "") -> object:
    for name in names:
        try:
            value = row.get(name, default)  # pandas.Series and dict
        except AttributeError:
            value = getattr(row, name, default)
        if value is not None:
            try:
                if not (isinstance(value, float) and math.isnan(value)):
                    return value
            except TypeError:
                return value
    return default


def _number(value: object, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _capacity_kw(row: object, entry: Mapping[str, object]) -> float:
    value = _row_value(row, "Cooling Capacity (kW)", default=None)
    if value is not None:
        return _number(value)
    return _number(entry.get("effective_btu", 0.0)) / 3412.14


def _system_entry(selected_units: Mapping[str, Mapping[str, object]], system_id: str):
    return selected_units.get(system_id) or selected_units.get(_text(system_id))


def build_indoor_placements(
    rooms: Sequence[RoomSpace],
    assignments: Sequence[RoomAssignment],
    selected_units: Mapping[str, Mapping[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[str]]:
    """Build indoor-unit records, relationships, and human-readable warnings."""

    room_by_id = {room.room_id: room for room in rooms}
    records: list[dict[str, object]] = []
    relationships: list[dict[str, object]] = []
    warnings: list[str] = []
    for assignment in assignments:
        room = room_by_id.get(assignment.room_id)
        system_id = _text(assignment.system_id)
        entry = _system_entry(selected_units, system_id)
        if room is None:
            warnings.append(f"UNKNOWN_ROOM:{assignment.room_id}")
            continue
        if not system_id or entry is None:
            warnings.append(f"NO_SELECTED_UNIT:{room.room_id}:{system_id or 'UNASSIGNED'}")
            continue
        row = entry.get("row", {})
        count = max(1, int(_number(entry.get("units", 1), 1)))
        point = room.polygon.representative_point()
        # Spread multi-unit placements deterministically around the safe point.
        radius = 0.25 if count > 1 else 0.0
        for index in range(count):
            angle = 2.0 * math.pi * index / count if count > 1 else 0.0
            entity_id = f"IDU-{system_id}-{room.room_id}-{index + 1:02d}"
            tag_id = f"IDU-{room.room_id}-{index + 1:02d}"
            load_btu = _number(entry.get("load_btu"), 0.0) / count
            capacity_kw = _capacity_kw(row, entry)
            capacity_mbh = capacity_kw * 3.41214
            airflow = _number(_row_value(row, "High CFM (numeric)"), 0.0)
            position = (float(point.x + radius * math.cos(angle)), float(point.y + radius * math.sin(angle)))
            records.append({
                "ENTITY_ID": entity_id,
                "TAG_ID": tag_id,
                "ROOM_ID": room.room_id,
                "ROOM_NAME": _text(room.label),
                "MODEL": _text(_row_value(row, "Indoor Unit Model")),
                "CAPACITY_KW": capacity_kw,
                "CAPACITY_MBH": capacity_mbh,
                "AIRFLOW_CFM": airflow,
                "HAP_LOAD_KW": load_btu / 3412.14,
                "CAPACITY_STATUS": "SELECTED",
                "CAPACITY_MARGIN_PCT": ((capacity_kw - load_btu / 3412.14) / (load_btu / 3412.14) * 100.0) if load_btu > 0 else None,
                "SYSTEM_ID": system_id,
                "SELECTION_STATUS": "SELECTED",
                "DATA_VERIFICATION": "HAP_AND_CARRIER_SELECTED",
                "SOURCE_REFERENCE": "Carrier AC Selector CAD/HAP workflow",
                "POSITION": position,
            })
            relationships.append({"SYSTEM_ID": system_id, "ENTITY_ID": entity_id, "ROLE": "INDOOR"})
    return records, relationships, warnings
