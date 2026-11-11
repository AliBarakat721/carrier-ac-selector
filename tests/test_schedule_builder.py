import pandas as pd
import pytest

from cad.schedule_builder import (
    build_equipment_schedule,
    export_schedule_data,
    resolve_system_relationships,
    validate_schedule_data,
)


INDOOR = [
    {
        "ENTITY_ID": "INDOOR-001",
        "TAG_ID": "IDU-01",
        "ROOM_ID": "G01",
        "ROOM_NAME": "غرفة المعيشة",
        "INDOOR_MODEL": "42QSS",
        "HAP_LOAD_KW": 5.0,
        "SELECTED_CAPACITY_KW": 5.5,
        "AIRFLOW_CFM": 530,
        "SYSTEM_ID": "SYS-01",
        "SELECTION_STATUS": "SELECTED",
        "DATA_VERIFICATION": "NEEDS_REVIEW",
    },
    {
        "ENTITY_ID": "INDOOR-002",
        "TAG_ID": "IDU-02",
        "ROOM_ID": "G02",
        "ROOM_NAME": "غرفة النوم",
        "INDOOR_MODEL": "42QSS-B",
        "HAP_LOAD_KW": None,
        "SELECTED_CAPACITY_KW": 3.5,
        "AIRFLOW_CFM": 350,
        "SYSTEM_ID": "SYS-01",
        "SELECTION_STATUS": "SELECTED",
        "DATA_VERIFICATION": "NEEDS_REVIEW",
    },
]

OUTDOOR = [
    {"ENTITY_ID": "OUTDOOR-001", "TAG_ID": "ODU-01", "MODEL": "38MAR"},
    {"ENTITY_ID": "OUTDOOR-002", "TAG_ID": "ODU-02", "MODEL": "38MAR-B"},
]

RELATIONSHIPS = [
    {"SYSTEM_ID": "SYS-01", "ENTITY_ID": "INDOOR-001", "ROLE": "INDOOR"},
    {"SYSTEM_ID": "SYS-01", "ENTITY_ID": "INDOOR-002", "ROLE": "INDOOR"},
    {"SYSTEM_ID": "SYS-01", "ENTITY_ID": "OUTDOOR-001", "ROLE": "OUTDOOR"},
    {"SYSTEM_ID": "SYS-01", "ENTITY_ID": "OUTDOOR-002", "ROLE": "OUTDOOR"},
]


def test_multiple_indoor_and_outdoor_units_keep_relationships():
    schedule = build_equipment_schedule(INDOOR, OUTDOOR, RELATIONSHIPS)

    assert len(schedule) == 2
    assert set(schedule["ODU_TAGS"]) == {"ODU-01, ODU-02"}
    assert schedule.loc[schedule["TAG_ID"] == "IDU-01", "CAPACITY_MARGIN_PCT"].iloc[0] == 10.0
    assert pd.isna(schedule.loc[schedule["TAG_ID"] == "IDU-02", "CAPACITY_MARGIN_PCT"].iloc[0])
    assert "غرفة المعيشة" in set(schedule["ROOM_NAME"])


def test_missing_outdoor_does_not_crash_and_requires_review():
    schedule = build_equipment_schedule(INDOOR[:1], [], [])

    assert schedule.iloc[0]["ODU_TAGS"] == ""
    assert schedule.iloc[0]["RELATIONSHIP_STATUS"] == "NEEDS_REVIEW"


def test_relationship_resolver_supports_many_to_one_and_many_to_many():
    resolved = resolve_system_relationships(RELATIONSHIPS)

    assert resolved["SYS-01"]["indoor"] == ["INDOOR-001", "INDOOR-002"]
    assert resolved["SYS-01"]["outdoor"] == ["OUTDOOR-001", "OUTDOOR-002"]


def test_invalid_relationship_is_warning_not_crash():
    warnings = validate_schedule_data(
        INDOOR,
        OUTDOOR,
        [{"SYSTEM_ID": "", "ENTITY_ID": "MISSING", "ROLE": "UNKNOWN"}],
    )

    assert "RELATIONSHIP_MISSING_SYSTEM_ID" in warnings
    assert "RELATIONSHIP_UNKNOWN_ENTITY:MISSING" in warnings
    assert "RELATIONSHIP_INVALID_ROLE:UNKNOWN" in warnings


def test_duplicate_schedule_generation_does_not_duplicate_indoor_rows():
    schedule = build_equipment_schedule(INDOOR + [INDOOR[0]], OUTDOOR, RELATIONSHIPS)

    assert list(schedule["ENTITY_ID"]).count("INDOOR-001") == 1


def test_schedule_exports_csv_and_excel_with_unicode(tmp_path):
    schedule = build_equipment_schedule(INDOOR, OUTDOOR, RELATIONSHIPS)
    csv_path = export_schedule_data(schedule, tmp_path / "schedule.csv")
    xlsx_path = export_schedule_data(schedule, tmp_path / "schedule.xlsx")

    assert csv_path.exists()
    assert xlsx_path.exists()
    assert "غرفة المعيشة" in csv_path.read_text(encoding="utf-8-sig")


def test_duplicate_entity_ids_are_rejected():
    duplicate = dict(OUTDOOR[0])
    duplicate["ENTITY_ID"] = INDOOR[0]["ENTITY_ID"]
    with pytest.raises(ValueError, match="Duplicate ENTITY_ID"):
        build_equipment_schedule(INDOOR, OUTDOOR + [duplicate], RELATIONSHIPS)
