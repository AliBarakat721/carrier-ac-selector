"""Conservative mapping between detected CAD spaces and HAP air systems."""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable, Mapping

from .models import HAPSystemRecord, RoomSystemMatch


def normalize_name(value: object) -> str:
    """Normalize labels for matching without discarding meaningful digits."""
    text = str(value or "").strip().casefold()
    return re.sub(r"[^a-z0-9]+", "", text)


def match_rooms_to_systems(
    spaces: Iterable[object],
    systems: Iterable[HAPSystemRecord],
    *,
    explicit_mapping: Mapping[str, str] | None = None,
) -> list[RoomSystemMatch]:
    """Match CAD room labels to HAP systems; multiple rooms may share a system.

    Exact normalized matches are accepted only when unique. Explicit mapping
    takes precedence. No system-level load is copied to an individual room.
    """
    system_list = list(systems)
    explicit = dict(explicit_mapping or {})
    by_name: dict[str, list[str]] = defaultdict(list)
    for system in system_list:
        key = normalize_name(system.name)
        if key:
            by_name[key].append(system.name)

    results: list[RoomSystemMatch] = []
    for space in spaces:
        room_id = str(getattr(space, "room_id", "")).strip()
        label = str(getattr(space, "label", "") or "").strip()
        if not room_id:
            results.append(RoomSystemMatch("", None, "NEEDS_REVIEW", "none", "CAD room has no stable room_id"))
            continue
        chosen = explicit.get(room_id)
        if chosen is not None:
            known = [item.name for item in system_list if item.name == chosen]
            if len(known) == 1:
                results.append(RoomSystemMatch(room_id, chosen, "MAPPED", "explicit"))
            else:
                results.append(RoomSystemMatch(room_id, None, "UNMATCHED", "explicit", f"Unknown HAP system: {chosen}"))
            continue
        key = normalize_name(label or room_id)
        candidates = by_name.get(key, [])
        if len(candidates) == 1:
            results.append(RoomSystemMatch(room_id, candidates[0], "MAPPED", "normalized_name"))
        elif len(candidates) > 1:
            results.append(RoomSystemMatch(room_id, None, "AMBIGUOUS", "normalized_name", "More than one HAP system has the same normalized name"))
        else:
            results.append(RoomSystemMatch(room_id, None, "UNMATCHED", "none", "No unique HAP system match; engineer mapping is required"))
    return results
