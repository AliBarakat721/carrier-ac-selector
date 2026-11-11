"""Conservative matching between detected CAD rooms and HAP air systems.

HAP air-system coil totals are not room loads. This module only associates names
for user review; it never copies a system load into a room-level load field.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class RoomSystemMatch:
    room_id: str
    room_name: str | None
    system_name: str | None
    status: str
    warnings: tuple[str, ...] = ()


def _normalise_name(value: object) -> str:
    """Normalise labels for exact matching without fuzzy guesses."""
    text = str(value or "").strip().casefold()
    return re.sub(r"[^a-z0-9]+", "", text)


def match_rooms_to_systems(
    rooms: Iterable[object], systems: Iterable[object],
) -> list[RoomSystemMatch]:
    """Return one explicit match decision for every CAD room.

    A unique exact room-label/system-name match is reported as
    MATCHED_SYSTEM_REFERENCE_ONLY. It is deliberately not a room-load match:
    a system may serve multiple rooms and its total cannot be assigned to an
    individual room without room-level HAP data.
    """
    by_name: dict[str, list[object]] = {}
    for system in systems:
        key = _normalise_name(getattr(system, "name", ""))
        if key:
            by_name.setdefault(key, []).append(system)

    results: list[RoomSystemMatch] = []
    for room in rooms:
        room_id = str(getattr(room, "room_id", "") or "").strip()
        label = getattr(room, "label", None)
        key = _normalise_name(label)
        if not room_id:
            results.append(RoomSystemMatch("", label, None, "INVALID_ROOM_ID", ("ROOM_ID_MISSING",)))
            continue
        candidates = by_name.get(key, []) if key else []
        if len(candidates) == 1:
            name = str(getattr(candidates[0], "name", "") or "")
            results.append(RoomSystemMatch(room_id, label, name,
                "MATCHED_SYSTEM_REFERENCE_ONLY", ("SYSTEM_LEVEL_DATA_NOT_ROOM_LEVEL",)))
        elif len(candidates) > 1:
            results.append(RoomSystemMatch(room_id, label, None, "AMBIGUOUS",
                ("MULTIPLE_HAP_SYSTEMS_MATCH_ROOM_LABEL",)))
        else:
            results.append(RoomSystemMatch(room_id, label, None, "UNMATCHED",
                ("NO_EXACT_HAP_SYSTEM_NAME_MATCH",)))
    return results
