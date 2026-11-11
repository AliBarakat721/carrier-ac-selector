import ezdxf
import pytest

from cad.block_manager import (
    create_block_definition,
    insert_block_reference,
    validate_block_definition,
)


def indoor_attributes(entity_id="INDOOR-0001", tag_id="IDU-01", model="42QSS"):
    return {
        "ENTITY_ID": entity_id,
        "TAG_ID": tag_id,
        "ROOM_ID": "G01",
        "ROOM_NAME": "غرفة المعيشة",
        "MODEL": model,
        "CAPACITY_KW": 5.5,
        "CAPACITY_MBH": 18.8,
        "AIRFLOW_CFM": 530,
        "HAP_LOAD_KW": 5.0,
        "CAPACITY_STATUS": "SELECTED",
        "CAPACITY_MARGIN_PCT": 10.0,
        "SYSTEM_ID": "SYS-001",
        "SELECTION_STATUS": "SELECTED",
        "DATA_VERIFICATION": "NEEDS_REVIEW",
        "SOURCE_REFERENCE": "Carrier Manual",
    }


def test_same_block_definition_is_created_once():
    drawing = ezdxf.new("R2018")

    first = create_block_definition(drawing, "indoor_unit")
    second = create_block_definition(drawing, "indoor_unit")

    assert first.name == "HVAC_INDOOR_UNIT"
    assert second.name == first.name
    assert len([block for block in drawing.blocks if block.name == "HVAC_INDOOR_UNIT"]) == 1
    assert validate_block_definition(drawing, "indoor_unit") == []


def test_two_references_have_independent_attributes_and_round_trip(tmp_path):
    drawing = ezdxf.new("R2018")
    ids = set()
    first = insert_block_reference(
        drawing, "indoor_unit", (1, 2), indoor_attributes(), existing_entity_ids=ids
    )
    second_attrs = indoor_attributes("INDOOR-0002", "IDU-02", "42QSS-B")
    second = insert_block_reference(
        drawing, "indoor_unit", (10, 2), second_attrs, existing_entity_ids=ids
    )

    first_values = {attribute.dxf.tag: attribute.dxf.text for attribute in first.attribs}
    second_values = {attribute.dxf.tag: attribute.dxf.text for attribute in second.attribs}
    assert first_values["TAG_ID"] == "IDU-01"
    assert second_values["TAG_ID"] == "IDU-02"
    assert first_values["MODEL"] != second_values["MODEL"]

    output = tmp_path / "blocks.dxf"
    drawing.saveas(output)
    reopened = ezdxf.readfile(output)
    assert "HVAC_INDOOR_UNIT" in reopened.blocks
    references = list(reopened.modelspace().query("INSERT"))
    assert len(references) == 2
    assert {attribute.dxf.text for ref in references for attribute in ref.attribs if attribute.dxf.tag == "ENTITY_ID"} == {
        "INDOOR-0001",
        "INDOOR-0002",
    }


def test_missing_id_and_unknown_attribute_are_rejected():
    drawing = ezdxf.new("R2018")
    missing = indoor_attributes()
    del missing["ENTITY_ID"]
    with pytest.raises(ValueError, match="ENTITY_ID"):
        insert_block_reference(drawing, "indoor_unit", (0, 0), missing)

    unknown = indoor_attributes()
    unknown["NOT_A_TAG"] = "x"
    with pytest.raises(ValueError, match="NOT_A_TAG"):
        insert_block_reference(drawing, "indoor_unit", (0, 0), unknown)


def test_duplicate_entity_id_is_rejected():
    drawing = ezdxf.new("R2018")
    ids = set()
    insert_block_reference(
        drawing, "indoor_unit", (0, 0), indoor_attributes(), existing_entity_ids=ids
    )
    with pytest.raises(ValueError, match="Duplicate ENTITY_ID"):
        insert_block_reference(
            drawing, "indoor_unit", (1, 1), indoor_attributes(), existing_entity_ids=ids
        )
