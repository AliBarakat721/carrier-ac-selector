"""Safe DXF export orchestration for approved HVAC data."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

import ezdxf

from .block_manager import insert_block_reference, populate_attributes, synchronize_attributes
from .layer_manager import ensure_hvac_layers, validate_hvac_layers
from .schedule_builder import build_equipment_schedule, export_schedule_data
from .tag_config import TAG_CONFIG, tag_block_name, validate_unique_entity_ids


@dataclass
class ExportResult:
    output_path: Path
    schedule_path: Path | None
    indoor_count: int
    outdoor_count: int
    room_count: int


def prepare_export_document(source: str | Path) -> ezdxf.document.Drawing:
    """Load a source DXF into memory; the source file is never modified."""

    source_path = Path(source)
    if not source_path.is_file():
        raise FileNotFoundError(f"Source DXF file not found: {source_path}")
    return ezdxf.readfile(source_path)


def _attribute_values(reference: object) -> dict[str, str]:
    return {
        attribute.dxf.tag: str(attribute.dxf.text)
        for attribute in getattr(reference, "attribs", [])
    }


def _existing_references(document: object, tag_type: str) -> dict[str, object]:
    result: dict[str, object] = {}
    block_name = tag_block_name(tag_type)
    for reference in document.modelspace().query("INSERT"):
        if str(reference.dxf.name) != block_name:
            continue
        entity_id = _attribute_values(reference).get("ENTITY_ID", "").strip()
        if entity_id:
            result[entity_id] = reference
    return result


def _update_or_insert(
    document: object,
    tag_type: str,
    position: tuple[float, float] | None,
    attributes: Mapping[str, object],
    existing_ids: set[str],
) -> object:
    entity_id = str(attributes["ENTITY_ID"]).strip()
    existing = _existing_references(document, tag_type).get(entity_id)
    if existing is not None:
        if position is not None:
            existing.dxf.insert = position
        synchronize_attributes(existing, tag_type, attributes)
        return existing
    if position is None:
        raise ValueError(f"POSITION is required for new ENTITY_ID: {entity_id}")
    return insert_block_reference(
        document,
        tag_type,
        position,
        attributes,
        existing_entity_ids=existing_ids,
    )


def _room_attributes(room: object) -> dict[str, object]:
    unit_status = getattr(room, "unit_status", "VERIFIED")
    return {
        "ENTITY_ID": f"ROOM-{room.room_id}",
        "ROOM_ID": room.room_id,
        "ROOM_NAME": room.label or "",
        "AREA_M2": room.area if unit_status == "VERIFIED" else None,
        "PERIMETER_M": room.perimeter,
        "DETECTION_STATUS": room.status,
        "ROOM_SOURCE": room.source,
        "GEOMETRY_VERIFICATION": room.status,
        "AREA_SOURCE": "CAD" if unit_status == "VERIFIED" else "UNIT_UNKNOWN",
    }


def validate_export_document(document: object) -> list[str]:
    """Return validation errors for layers, blocks, attributes, and IDs."""

    errors = [f"MISSING_LAYER:{name}" for name in validate_hvac_layers(document)]
    entity_ids: list[str] = []
    for reference in document.modelspace().query("INSERT"):
        values = _attribute_values(reference)
        if "ENTITY_ID" in values:
            entity_ids.append(values["ENTITY_ID"])
        block_name = str(reference.dxf.name)
        for tag_type in TAG_CONFIG:
            if tag_block_name(tag_type) == block_name:
                expected = set(TAG_CONFIG[tag_type]["fields"])
                missing = sorted(expected - set(values))
                errors.extend(f"MISSING_ATTRIBUTE:{block_name}:{field}" for field in missing)
    try:
        validate_unique_entity_ids(entity_ids)
    except ValueError as exc:
        errors.append(str(exc))
    return errors


def reopen_and_verify(path: str | Path) -> list[str]:
    """Reopen a saved DXF and return validation errors instead of hiding them."""

    reopened = ezdxf.readfile(path)
    return validate_export_document(reopened)


def export_hvac_dxf(
    source: str | Path,
    output: str | Path,
    *,
    indoor_units: Iterable[Mapping[str, object]],
    outdoor_units: Iterable[Mapping[str, object]],
    relationships: Iterable[Mapping[str, object]],
    rooms: Iterable[object] = (),
    schedule_path: str | Path | None = None,
) -> ExportResult:
    """Export approved HVAC entities without changing the original DXF."""

    document = prepare_export_document(source)
    ensure_hvac_layers(document)
    indoor = [dict(item) for item in indoor_units]
    outdoor = [dict(item) for item in outdoor_units]
    room_list = list(rooms)
    all_ids = [str(item.get("ENTITY_ID", "")) for item in indoor + outdoor]
    all_ids.extend(f"ROOM-{room.room_id}" for room in room_list)
    validate_unique_entity_ids(all_ids)

    existing_ids = set()
    for tag_type in ("indoor_unit", "outdoor_unit", "room_label"):
        existing_ids.update(_existing_references(document, tag_type))

    for item in indoor:
        raw_position = item.pop("POSITION", None)
        position = tuple(raw_position) if raw_position is not None else None
        _update_or_insert(document, "indoor_unit", position, item, existing_ids)
    for item in outdoor:
        raw_position = item.pop("POSITION", None)
        position = tuple(raw_position) if raw_position is not None else None
        _update_or_insert(document, "outdoor_unit", position, item, existing_ids)
    for room in room_list:
        centroid = room.polygon.centroid
        _update_or_insert(
            document,
            "room_label",
            (float(centroid.x), float(centroid.y)),
            _room_attributes(room),
            existing_ids,
        )

    schedule = build_equipment_schedule(indoor, outdoor, relationships)
    errors = validate_export_document(document)
    if errors:
        raise ValueError("DXF export validation failed: " + "; ".join(errors))

    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    schedule_destination = Path(schedule_path) if schedule_path else None
    with tempfile.NamedTemporaryFile(
        prefix=f".{destination.stem}-", suffix=".dxf", dir=destination.parent, delete=False
    ) as handle:
        temporary_dxf = Path(handle.name)
    temporary_schedule: Path | None = None
    backup_dxf: Path | None = None
    backup_schedule: Path | None = None
    try:
        if schedule_destination:
            schedule_destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                prefix=f".{schedule_destination.stem}-",
                suffix=schedule_destination.suffix,
                dir=schedule_destination.parent,
                delete=False,
            ) as handle:
                temporary_schedule = Path(handle.name)
            export_schedule_data(schedule, temporary_schedule)
        document.saveas(temporary_dxf)
        post_save_errors = reopen_and_verify(temporary_dxf)
        if post_save_errors:
            raise ValueError("DXF round-trip validation failed: " + "; ".join(post_save_errors))
        if destination.exists():
            backup_dxf = destination.with_name(f".{destination.name}.backup")
            shutil.copy2(destination, backup_dxf)
        if schedule_destination and schedule_destination.exists():
            backup_schedule = schedule_destination.with_name(f".{schedule_destination.name}.backup")
            shutil.copy2(schedule_destination, backup_schedule)
        try:
            os.replace(temporary_dxf, destination)
            if schedule_destination and temporary_schedule:
                os.replace(temporary_schedule, schedule_destination)
        except Exception:
            if backup_dxf and backup_dxf.exists():
                os.replace(backup_dxf, destination)
            elif destination.exists():
                destination.unlink()
            if backup_schedule and backup_schedule.exists():
                os.replace(backup_schedule, schedule_destination)
            elif schedule_destination and schedule_destination.exists():
                schedule_destination.unlink()
            raise
    finally:
        temporary_dxf.unlink(missing_ok=True)
        if temporary_schedule:
            temporary_schedule.unlink(missing_ok=True)
        if backup_dxf:
            backup_dxf.unlink(missing_ok=True)
        if backup_schedule:
            backup_schedule.unlink(missing_ok=True)

    return ExportResult(
        output_path=destination,
        schedule_path=schedule_destination,
        indoor_count=len(indoor),
        outdoor_count=len(outdoor),
        room_count=len(room_list),
    )
