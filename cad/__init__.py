"""CAD import and geometry analysis for Carrier AC Selector."""

from .dxf_reader import CadDocument, CadEntity, read_dxf
from .importer import DwgImportError, read_cad_file
from .space_detector import RoomSpace, detect_spaces
from .layer_manager import ensure_hvac_layers, validate_hvac_layers
from .block_manager import (
    create_block_definition,
    insert_block_reference,
    populate_attributes,
    synchronize_attributes,
    validate_block_definition,
)
from .schedule_builder import (
    build_equipment_schedule,
    calculate_schedule_metrics,
    export_schedule_data,
    resolve_system_relationships,
    validate_schedule_data,
)
from .dxf_exporter import (
    ExportResult,
    export_hvac_dxf,
    prepare_export_document,
    reopen_and_verify,
    validate_export_document,
)
from .tag_config import (
    CAD_LAYERS,
    TAG_CONFIG,
    capacity_margin_pct,
    pipe_check_status,
    tag_block_name,
    tag_fields,
    validate_tag_attributes,
    validate_unique_entity_ids,
)
from .workflow import RoomAssignment, build_indoor_placements, suggest_room_assignments

__all__ = [
    "CadDocument",
    "CadEntity",
    "RoomSpace",
    "detect_spaces",
    "ensure_hvac_layers",
    "validate_hvac_layers",
    "create_block_definition",
    "insert_block_reference",
    "populate_attributes",
    "synchronize_attributes",
    "validate_block_definition",
    "build_equipment_schedule",
    "calculate_schedule_metrics",
    "export_schedule_data",
    "resolve_system_relationships",
    "validate_schedule_data",
    "ExportResult",
    "export_hvac_dxf",
    "prepare_export_document",
    "reopen_and_verify",
    "validate_export_document",
    "read_dxf",
    "read_cad_file",
    "DwgImportError",
    "CAD_LAYERS",
    "TAG_CONFIG",
    "capacity_margin_pct",
    "pipe_check_status",
    "tag_block_name",
    "tag_fields",
    "validate_tag_attributes",
    "validate_unique_entity_ids",
    "RoomAssignment",
    "build_indoor_placements",
    "suggest_room_assignments",
]
