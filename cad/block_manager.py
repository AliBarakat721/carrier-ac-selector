"""Reusable HVAC DXF block definitions and independent references."""

from __future__ import annotations

from typing import Any, Mapping

from .tag_config import TAG_CONFIG, tag_block_name, tag_fields


BLOCK_TYPES = ("indoor_unit", "outdoor_unit", "pipe_label", "room_label")
_REQUIRED_IDS = {
    "indoor_unit": ("ENTITY_ID", "TAG_ID"),
    "outdoor_unit": ("ENTITY_ID", "TAG_ID", "SYSTEM_ID"),
    "pipe_label": ("ENTITY_ID", "SYSTEM_ID"),
    "room_label": ("ENTITY_ID", "ROOM_ID"),
}


def _validate_tag_type(tag_type: str) -> None:
    if tag_type not in BLOCK_TYPES:
        raise KeyError(f"Unknown HVAC block type: {tag_type}")


def _validate_attributes(tag_type: str, attributes: Mapping[str, object]) -> None:
    _validate_tag_type(tag_type)
    allowed = set(tag_fields(tag_type))
    unknown = sorted(set(attributes) - allowed)
    if unknown:
        raise ValueError(f"Unknown attributes for {tag_type}: {', '.join(unknown)}")
    missing = [field for field in _REQUIRED_IDS[tag_type] if not str(attributes.get(field, "")).strip()]
    if missing:
        raise ValueError(f"Missing required attributes for {tag_type}: {', '.join(missing)}")


def _add_symbol_geometry(block: Any, tag_type: str) -> None:
    """Add a small neutral symbol; visual styling can evolve independently."""

    if tag_type in {"indoor_unit", "outdoor_unit"}:
        block.add_lwpolyline(
            [(-0.5, -0.25), (0.5, -0.25), (0.5, 0.25), (-0.5, 0.25)],
            close=True,
        )
    else:
        block.add_line((-0.5, 0), (0.5, 0))


def create_block_definition(document: Any, tag_type: str) -> Any:
    """Create one reusable block definition, or return the existing one."""

    _validate_tag_type(tag_type)
    name = tag_block_name(tag_type)
    if name in document.blocks:
        return document.blocks[name]

    block = document.blocks.new(name=name, base_point=(0, 0))
    _add_symbol_geometry(block, tag_type)
    for index, field in enumerate(tag_fields(tag_type)):
        attdef = block.add_attdef(
            tag=field,
            insert=(0.7, 0.3 - index * 0.18),
            dxfattribs={"height": 0.12, "layer": TAG_CONFIG[tag_type]["layer"]},
        )
        attdef.dxf.text = field
        attdef.dxf.prompt = field
    return block


def validate_block_definition(document: Any, tag_type: str) -> list[str]:
    """Return missing attribute definitions for an existing block."""

    _validate_tag_type(tag_type)
    name = tag_block_name(tag_type)
    if name not in document.blocks:
        return [name]
    block = document.blocks[name]
    actual = {entity.dxf.tag for entity in block.query("ATTDEF")}
    return [field for field in tag_fields(tag_type) if field not in actual]


def populate_attributes(block_reference: Any, tag_type: str, attributes: Mapping[str, object]) -> Any:
    """Populate one INSERT with its own ATTRIB values; missing optional data stays blank."""

    _validate_attributes(tag_type, attributes)
    values = {
        field: "" if attributes.get(field) is None else str(attributes.get(field))
        for field in tag_fields(tag_type)
    }
    block_reference.add_auto_attribs(values)
    return block_reference


def insert_block_reference(
    document: Any,
    tag_type: str,
    insert: tuple[float, float],
    attributes: Mapping[str, object],
    *,
    existing_entity_ids: set[str] | None = None,
) -> Any:
    """Insert a reusable block and populate independent attribute references."""

    _validate_attributes(tag_type, attributes)
    entity_id = str(attributes["ENTITY_ID"]).strip()
    if existing_entity_ids is not None and entity_id in existing_entity_ids:
        raise ValueError(f"Duplicate ENTITY_ID: {entity_id}")
    create_block_definition(document, tag_type)
    reference = document.modelspace().add_blockref(tag_block_name(tag_type), insert)
    populate_attributes(reference, tag_type, attributes)
    if existing_entity_ids is not None:
        existing_entity_ids.add(entity_id)
    return reference
