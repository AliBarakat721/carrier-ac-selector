"""Application-level orchestration for the CAD + HAP placement workflow.

This module deliberately contains no Qt code.  It converts the existing HAP
selection-cart records into the neutral records consumed by the CAD exporter.
Indoor units are placed at representative points inside assigned rooms. Outdoor
units are not placed automatically because a room polygon is not a safe proxy
for an outdoor-unit location.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Sequence

from .room_matching import MatchingResult, match_rooms_to_systems
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


def suggest_room_assignments(
    rooms: Sequence[RoomSpace],
    systems: Sequence[object],
) -> dict[str, str]:
    """Return the rooms that are unambiguously matched to a system by name.

    This is the legacy, dictionary-shaped view of
    :func:`cad.room_matching.match_rooms_to_systems`.  It contains **only**
    rooms whose name matches exactly one system after normalisation.  Rooms are
    never paired by drawing order or report order, and duplicate names are never
    resolved by picking the first candidate.

    Callers that need the full picture — unmatched rooms, unused systems, and
    the reason a room is blank — should use :func:`match_room_systems` instead.
    """

    return match_rooms_to_systems(rooms, systems).matched


def match_room_systems(
    rooms: Sequence[RoomSpace],
    systems: Sequence[object],
) -> MatchingResult:
    """Full, explicit room-to-system matching result (statuses and reasons)."""

    return match_rooms_to_systems(rooms, systems)


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
    room_selections: Mapping[str, Mapping[str, object]],
    room_loads: Mapping[str, Mapping[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[str]]:
    """Build room-based placements without falling back to system-level HAP data.

    Every room needs its own verified cooling load and airflow, and its own
    Carrier selection based on those values. An aggregate HAP system total is
    never copied to each assigned room.
    """

    room_by_id = {room.room_id: room for room in rooms}
    records: list[dict[str, object]] = []
    relationships: list[dict[str, object]] = []
    warnings: list[str] = []
    for assignment in assignments:
        room = room_by_id.get(assignment.room_id)
        system_id = _text(assignment.system_id)
        if room is None:
            warnings.append(f"UNKNOWN_ROOM:{assignment.room_id}")
            continue
        if not system_id:
            warnings.append(f"UNASSIGNED_HAP_SYSTEM:{room.room_id}")
            continue
        load = room_loads.get(room.room_id)
        if not load:
            warnings.append(f"MISSING_ROOM_LOAD:{room.room_id}")
            continue
        load_btu = _number(load.get("cooling_load_btu"), 0.0)
        load_cfm = _number(load.get("airflow_cfm"), 0.0)
        if load_btu <= 0 or load_cfm <= 0:
            warnings.append(f"INVALID_ROOM_LOAD:{room.room_id}")
            continue
        entry = _system_entry(room_selections, room.room_id)
        if entry is None:
            warnings.append(f"NO_ROOM_UNIT_SELECTION:{room.room_id}")
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
            per_unit_load_btu = load_btu / count
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
                "HAP_LOAD_KW": per_unit_load_btu / 3412.14,
                "CAPACITY_STATUS": "SELECTED",
                "CAPACITY_MARGIN_PCT": ((capacity_kw - per_unit_load_btu / 3412.14) / (per_unit_load_btu / 3412.14) * 100.0) if per_unit_load_btu > 0 else None,
                "SYSTEM_ID": system_id,
                "SELECTION_STATUS": "SELECTED",
                "DATA_VERIFICATION": _text(load.get("source_reference")) or "ENGINEER_CONFIRMED_ROOM_LOAD",
                "SOURCE_REFERENCE": _text(load.get("source_reference")) or "Engineer-confirmed room-level load",
                "POSITION": position,
            })
            relationships.append({"SYSTEM_ID": system_id, "ENTITY_ID": entity_id, "ROLE": "INDOOR"})
    return records, relationships, warnings
