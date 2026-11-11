"""Equipment schedule builder independent from Carrier selection logic."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Iterable, Mapping

import pandas as pd

from .tag_config import capacity_margin_pct, validate_unique_entity_ids


SCHEDULE_COLUMNS = [
    "ENTITY_ID",
    "TAG_ID",
    "ROOM_ID",
    "ROOM_NAME",
    "INDOOR_MODEL",
    "HAP_LOAD_KW",
    "SELECTED_CAPACITY_KW",
    "CAPACITY_MARGIN_PCT",
    "AIRFLOW_CFM",
    "SYSTEM_ID",
    "ODU_TAGS",
    "SELECTION_STATUS",
    "DATA_VERIFICATION",
    "RELATIONSHIP_STATUS",
]


def _records(items: Iterable[Mapping[str, object]] | pd.DataFrame) -> list[dict[str, object]]:
    if isinstance(items, pd.DataFrame):
        return items.to_dict(orient="records")
    return [dict(item) for item in items]


def _dedupe_exact_records(records: list[dict[str, object]]) -> list[dict[str, object]]:
    """Remove only identical records; conflicting IDs remain validation errors."""

    unique: list[dict[str, object]] = []
    for record in records:
        if record not in unique:
            unique.append(record)
    return unique


def validate_schedule_data(
    indoor_equipment: Iterable[Mapping[str, object]] | pd.DataFrame,
    outdoor_equipment: Iterable[Mapping[str, object]] | pd.DataFrame,
    relationships: Iterable[Mapping[str, object]] | pd.DataFrame,
) -> list[str]:
    """Validate IDs and return non-fatal relationship/data warnings."""

    indoor = _records(indoor_equipment)
    outdoor = _records(outdoor_equipment)
    links = _records(relationships)
    validate_unique_entity_ids([str(row.get("ENTITY_ID", "")) for row in indoor + outdoor])

    warnings: list[str] = []
    known_ids = {
        str(row["ENTITY_ID"])
        for row in indoor + outdoor
        if row.get("ENTITY_ID") is not None
    }
    for link in links:
        system_id = str(link.get("SYSTEM_ID", "")).strip()
        entity_id = str(link.get("ENTITY_ID", "")).strip()
        role = str(link.get("ROLE", "")).strip().upper()
        if not system_id:
            warnings.append("RELATIONSHIP_MISSING_SYSTEM_ID")
        if not entity_id or entity_id not in known_ids:
            warnings.append(f"RELATIONSHIP_UNKNOWN_ENTITY:{entity_id}")
        if role not in {"INDOOR", "OUTDOOR"}:
            warnings.append(f"RELATIONSHIP_INVALID_ROLE:{role}")
    explicit_by_entity = {
        str(link.get("ENTITY_ID", "")).strip(): str(link.get("SYSTEM_ID", "")).strip()
        for link in links
        if str(link.get("ENTITY_ID", "")).strip()
    }
    for row in indoor + outdoor:
        entity_id = str(row.get("ENTITY_ID", "")).strip()
        source_system = str(row.get("SYSTEM_ID", "")).strip()
        explicit_system = explicit_by_entity.get(entity_id)
        if source_system and explicit_system and source_system != explicit_system:
            warnings.append(f"RELATIONSHIP_SYSTEM_CONFLICT:{entity_id}")
    return sorted(set(warnings))


def resolve_system_relationships(
    relationships: Iterable[Mapping[str, object]] | pd.DataFrame,
) -> dict[str, dict[str, list[str]]]:
    """Resolve system memberships without assuming one-to-one equipment."""

    resolved: dict[str, dict[str, list[str]]] = defaultdict(
        lambda: {"indoor": [], "outdoor": []}
    )
    for link in _records(relationships):
        system_id = str(link.get("SYSTEM_ID", "")).strip()
        entity_id = str(link.get("ENTITY_ID", "")).strip()
        role = str(link.get("ROLE", "")).strip().upper()
        if not system_id or not entity_id or role not in {"INDOOR", "OUTDOOR"}:
            continue
        key = "indoor" if role == "INDOOR" else "outdoor"
        if entity_id not in resolved[system_id][key]:
            resolved[system_id][key].append(entity_id)
    return dict(resolved)


def calculate_schedule_metrics(row: Mapping[str, object]) -> dict[str, object]:
    """Add derived metrics without replacing unknown engineering data."""

    result = dict(row)
    result["CAPACITY_MARGIN_PCT"] = capacity_margin_pct(
        result.get("SELECTED_CAPACITY_KW"), result.get("HAP_LOAD_KW")
    )
    return result


def build_equipment_schedule(
    indoor_equipment: Iterable[Mapping[str, object]] | pd.DataFrame,
    outdoor_equipment: Iterable[Mapping[str, object]] | pd.DataFrame,
    relationships: Iterable[Mapping[str, object]] | pd.DataFrame,
) -> pd.DataFrame:
    """Build one row per indoor unit and preserve many-to-many relationships."""

    indoor = _dedupe_exact_records(_records(indoor_equipment))
    outdoor = _dedupe_exact_records(_records(outdoor_equipment))
    links = _records(relationships)
    linked_entities = {str(link.get("ENTITY_ID", "")).strip() for link in links}
    for row in indoor + outdoor:
        entity_id = str(row.get("ENTITY_ID", "")).strip()
        system_id = str(row.get("SYSTEM_ID", "")).strip()
        if entity_id and system_id and entity_id not in linked_entities:
            links.append({
                "SYSTEM_ID": system_id,
                "ENTITY_ID": entity_id,
                "ROLE": "INDOOR" if row in indoor else "OUTDOOR",
            })
    warnings = validate_schedule_data(indoor, outdoor, links)
    resolved = resolve_system_relationships(links)
    outdoor_by_id = {
        str(row.get("ENTITY_ID")): row for row in outdoor if row.get("ENTITY_ID") is not None
    }

    rows: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    for source in indoor:
        entity_id = str(source.get("ENTITY_ID", "")).strip()
        if entity_id in seen_ids:
            continue
        seen_ids.add(entity_id)
        row = calculate_schedule_metrics(source)
        system_id = str(source.get("SYSTEM_ID", "")).strip()
        if not system_id:
            memberships = [system for system, members in resolved.items() if entity_id in members["indoor"]]
            system_id = memberships[0] if len(memberships) == 1 else ""
        members = resolved.get(system_id, {"indoor": [], "outdoor": []})
        outdoor_tags = [
            str(outdoor_by_id[item].get("TAG_ID", ""))
            for item in members["outdoor"]
            if item in outdoor_by_id and outdoor_by_id[item].get("TAG_ID")
        ]
        row["SYSTEM_ID"] = system_id
        row["ODU_TAGS"] = ", ".join(outdoor_tags)
        row["RELATIONSHIP_STATUS"] = "OK" if outdoor_tags and not warnings else "NEEDS_REVIEW"
        rows.append({column: row.get(column) for column in SCHEDULE_COLUMNS})

    return pd.DataFrame(rows, columns=SCHEDULE_COLUMNS)


def export_schedule_data(schedule: pd.DataFrame, path: str | Path) -> Path:
    """Export a schedule to UTF-8 CSV or Excel according to the file extension."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    suffix = destination.suffix.lower()
    if suffix == ".csv":
        schedule.to_csv(destination, index=False, encoding="utf-8-sig")
    elif suffix in {".xlsx", ".xlsm"}:
        schedule.to_excel(destination, index=False)
    else:
        raise ValueError("Schedule export path must end with .csv, .xlsx, or .xlsm")
    return destination
