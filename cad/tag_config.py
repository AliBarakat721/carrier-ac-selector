"""CAD documentation standard for HVAC tags and exported layers.

The field names in this module are part of the DXF data contract. Keep them
stable so AutoCAD Data Extraction and downstream schedules remain compatible
when the visual layout of a tag changes.
"""

from __future__ import annotations

import math
from typing import Mapping


CAD_LAYERS: dict[str, str] = {
    "indoor_units": "HVAC-INDOOR-UNITS",
    "outdoor_units": "HVAC-OUTDOOR-UNITS",
    "refrigerant_piping": "HVAC-REFRIGERANT-PIPING",
    "drain_piping": "HVAC-DRAIN-PIPING",
    "tags": "HVAC-TAGS",
    "warnings": "HVAC-WARNINGS",
    "clearances": "HVAC-CLEARANCES",
    "room_labels": "HVAC-ROOM-LABELS",
}


TAG_CONFIG: dict[str, dict[str, object]] = {
    "indoor_unit": {
        "block_name": "HVAC_INDOOR_UNIT",
        "layer": CAD_LAYERS["tags"],
        "fields": [
            "TAG_ID",
            "ENTITY_ID",
            "ROOM_ID",
            "ROOM_NAME",
            "MODEL",
            "CAPACITY_KW",
            "CAPACITY_MBH",
            "AIRFLOW_CFM",
            "HAP_LOAD_KW",
            "CAPACITY_STATUS",
            "CAPACITY_MARGIN_PCT",
            "SYSTEM_ID",
            "SELECTION_STATUS",
            "DATA_VERIFICATION",
            "SOURCE_REFERENCE",
        ],
    },
    "outdoor_unit": {
        "block_name": "HVAC_OUTDOOR_UNIT",
        "layer": CAD_LAYERS["tags"],
        "fields": [
            "TAG_ID",
            "ENTITY_ID",
            "SYSTEM_ID",
            "MODEL",
            "SYSTEM_MODEL",
            "POWER_SUPPLY",
            "REFRIGERANT",
            "WEIGHT_KG",
            "DIMENSIONS_MM",
            "SELECTION_STATUS",
            "DATA_VERIFICATION",
            "SOURCE_REFERENCE",
        ],
    },
    "pipe_label": {
        "block_name": "HVAC_PIPE_LABEL",
        "layer": CAD_LAYERS["tags"],
        "fields": [
            "SYSTEM_ID",
            "ENTITY_ID",
            "GAS_PIPE_SIZE",
            "LIQUID_PIPE_SIZE",
            "CALCULATED_LENGTH_M",
            "MAX_LENGTH_M",
            "HEIGHT_DIFF_M",
            "MAX_HEIGHT_DIFF_M",
            "CHECK_STATUS",
            "DATA_VERIFICATION",
            "SOURCE_REFERENCE",
        ],
    },
    "room_label": {
        "block_name": "HVAC_ROOM_TAG",
        "layer": CAD_LAYERS["room_labels"],
        "fields": [
            "ROOM_ID",
            "ENTITY_ID",
            "ROOM_NAME",
            "AREA_M2",
            "PERIMETER_M",
            "DETECTION_STATUS",
            "ROOM_SOURCE",
            "GEOMETRY_VERIFICATION",
            "AREA_SOURCE",
        ],
    },
}


def tag_fields(tag_type: str) -> tuple[str, ...]:
    """Return the stable attribute names for a configured tag type."""

    try:
        fields = TAG_CONFIG[tag_type]["fields"]
    except KeyError as exc:
        raise KeyError(f"Unknown HVAC tag type: {tag_type}") from exc
    return tuple(str(field) for field in fields)  # type: ignore[arg-type]


def tag_block_name(tag_type: str) -> str:
    """Return the DXF block name for a configured tag type."""

    try:
        return str(TAG_CONFIG[tag_type]["block_name"])
    except KeyError as exc:
        raise KeyError(f"Unknown HVAC tag type: {tag_type}") from exc


def validate_tag_attributes(tag_type: str, attributes: Mapping[str, object]) -> list[str]:
    """Return missing or invalid required fields without rejecting extensions."""

    required = set(tag_fields(tag_type))
    errors = [field for field in required if field not in attributes]
    for field in required.intersection(attributes):
        value = attributes[field]
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(field)
    return sorted(set(errors))


def capacity_margin_pct(selected_capacity_kw: float | None, load_kw: float | None) -> float | None:
    """Calculate capacity margin, or return None when the load is unavailable."""

    if selected_capacity_kw is None or load_kw is None:
        return None
    try:
        selected = float(selected_capacity_kw)
        load = float(load_kw)
    except (TypeError, ValueError) as exc:
        raise ValueError("Capacity and load must be numeric") from exc
    if not math.isfinite(selected) or not math.isfinite(load):
        raise ValueError("Capacity and load must be finite")
    if selected < 0 or load < 0:
        raise ValueError("Capacity and load cannot be negative")
    if load == 0:
        return None
    return (selected - load) / load * 100.0


def pipe_check_status(
    calculated_length_m: float | None,
    max_length_m: float | None,
    height_diff_m: float | None,
    max_height_diff_m: float | None,
) -> str:
    """Evaluate pipe limits without claiming compliance when limits are unknown."""

    values = (calculated_length_m, max_length_m, height_diff_m, max_height_diff_m)
    numeric_values: list[float | None] = []
    for value in values:
        if value is None:
            numeric_values.append(None)
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return "INVALID_DATA"
        if not math.isfinite(numeric) or numeric < 0:
            return "INVALID_DATA"
        numeric_values.append(numeric)

    length, max_length, height, max_height = numeric_values
    if max_length is not None and length is not None and length > max_length:
        return "EXCEEDS_LIMIT"
    if max_height is not None and height is not None and height > max_height:
        return "EXCEEDS_LIMIT"
    if any(value is None for value in numeric_values):
        return "UNKNOWN"
    return "OK"


def validate_unique_entity_ids(entity_ids: list[str]) -> None:
    """Reject missing or duplicate internal identifiers before export."""

    if any(not isinstance(value, str) or not value.strip() for value in entity_ids):
        raise ValueError("ENTITY_ID values must be non-empty strings")
    if len(entity_ids) != len(set(entity_ids)):
        raise ValueError("Duplicate ENTITY_ID values are not allowed")
