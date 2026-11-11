import pytest

from cad.tag_config import (
    CAD_LAYERS,
    TAG_CONFIG,
    capacity_margin_pct,
    pipe_check_status,
    tag_block_name,
    tag_fields,
    validate_tag_attributes,
    validate_unique_entity_ids,
)


def test_all_export_layers_are_stable_and_unique():
    assert len(CAD_LAYERS) == 8
    assert len(set(CAD_LAYERS.values())) == len(CAD_LAYERS)
    assert CAD_LAYERS["indoor_units"] == "HVAC-INDOOR-UNITS"
    assert CAD_LAYERS["room_labels"] == "HVAC-ROOM-LABELS"


def test_indoor_tag_contains_selection_and_capacity_fields():
    fields = tag_fields("indoor_unit")

    assert tag_block_name("indoor_unit") == "HVAC_INDOOR_UNIT"
    assert "HAP_LOAD_KW" in fields
    assert "CAPACITY_KW" in fields
    assert "CAPACITY_MARGIN_PCT" in fields
    assert "SELECTION_STATUS" in fields
    assert "DATA_VERIFICATION" in fields
    assert "ENTITY_ID" in fields


def test_outdoor_and_pipe_tags_keep_system_relationship():
    assert "SYSTEM_ID" in tag_fields("outdoor_unit")
    assert "SYSTEM_ID" in tag_fields("pipe_label")
    assert "CALCULATED_LENGTH_M" in tag_fields("pipe_label")
    assert "MAX_HEIGHT_DIFF_M" in tag_fields("pipe_label")


def test_attribute_validation_reports_missing_fields():
    missing = validate_tag_attributes("outdoor_unit", {"TAG_ID": "ODU-01"})

    assert "SYSTEM_ID" in missing
    assert "MODEL" in missing
    assert "TAG_ID" not in missing


def test_capacity_margin_is_unknown_for_missing_or_zero_load():
    assert capacity_margin_pct(5.5, 5.0) == 10.0
    assert capacity_margin_pct(5.5, 0.0) is None
    assert capacity_margin_pct(5.5, None) is None


def test_capacity_margin_rejects_invalid_numeric_values():
    with pytest.raises(ValueError):
        capacity_margin_pct(-1.0, 5.0)
    with pytest.raises(ValueError):
        capacity_margin_pct(5.0, float("nan"))
    with pytest.raises(ValueError):
        capacity_margin_pct("not-a-number", 5.0)


def test_pipe_limits_do_not_claim_ok_when_manufacturer_limits_are_unknown():
    assert pipe_check_status(12.5, None, 4.0, 15.0) == "UNKNOWN"
    assert pipe_check_status(12.5, 25.0, 4.0, 15.0) == "OK"
    assert pipe_check_status(30.0, 25.0, 4.0, 15.0) == "EXCEEDS_LIMIT"
    assert pipe_check_status(-1.0, 25.0, 4.0, 15.0) == "INVALID_DATA"
    assert pipe_check_status(30.0, 25.0, 4.0, None) == "EXCEEDS_LIMIT"
    assert pipe_check_status(float("inf"), 25.0, 4.0, 15.0) == "INVALID_DATA"


def test_entity_ids_must_be_unique_and_non_empty():
    validate_unique_entity_ids(["INDOOR-0001", "OUTDOOR-0001"])
    with pytest.raises(ValueError):
        validate_unique_entity_ids(["ID-1", "ID-1"])
    with pytest.raises(ValueError):
        validate_unique_entity_ids(["ID-1", ""])


def test_unknown_tag_type_is_rejected():
    with pytest.raises(KeyError):
        tag_fields("unknown")


def test_every_configured_tag_has_unique_fields():
    for config in TAG_CONFIG.values():
        fields = config["fields"]
        assert len(fields) == len(set(fields))
