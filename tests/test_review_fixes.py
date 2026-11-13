"""Regression tests for the review fixes: schedule warnings, summary keys, logging."""

import logging

import pytest

import electrical_engine as electrical
from cad.schedule_builder import build_equipment_schedule
from logging_setup import LOG_FILE_NAME, configure_logging, default_log_dir


INDOOR = [
    {
        "ENTITY_ID": "INDOOR-001",
        "TAG_ID": "IDU-01",
        "SYSTEM_ID": "SYS-01",
        "HAP_LOAD_KW": 5.0,
        "SELECTED_CAPACITY_KW": 5.5,
    }
]
OUTDOOR = [{"ENTITY_ID": "OUTDOOR-001", "TAG_ID": "ODU-01", "SYSTEM_ID": "SYS-01"}]
RELATIONSHIPS = [
    {"SYSTEM_ID": "SYS-01", "ENTITY_ID": "INDOOR-001", "ROLE": "INDOOR"},
    {"SYSTEM_ID": "SYS-01", "ENTITY_ID": "OUTDOOR-001", "ROLE": "OUTDOOR"},
]


# ── schedule_builder: validation warnings must not be discarded ──────────────
def test_schedule_surfaces_validation_warnings_to_the_caller():
    warnings: list[str] = []

    schedule = build_equipment_schedule(
        INDOOR,
        OUTDOOR,
        RELATIONSHIPS + [{"SYSTEM_ID": "", "ENTITY_ID": "GHOST", "ROLE": "BOGUS"}],
        warnings_out=warnings,
    )

    assert "RELATIONSHIP_MISSING_SYSTEM_ID" in warnings
    assert "RELATIONSHIP_UNKNOWN_ENTITY:GHOST" in warnings
    assert "RELATIONSHIP_INVALID_ROLE:BOGUS" in warnings
    # The valid unit is still reported as OK; unrelated junk must not poison it.
    assert schedule.iloc[0]["RELATIONSHIP_STATUS"] == "OK"


def test_schedule_marks_entities_flagged_by_the_validator_for_review():
    warnings: list[str] = []

    schedule = build_equipment_schedule(
        INDOOR,
        OUTDOOR,
        [
            {"SYSTEM_ID": "SYS-02", "ENTITY_ID": "INDOOR-001", "ROLE": "INDOOR"},
            {"SYSTEM_ID": "SYS-01", "ENTITY_ID": "OUTDOOR-001", "ROLE": "OUTDOOR"},
        ],
        warnings_out=warnings,
    )

    assert "RELATIONSHIP_SYSTEM_CONFLICT:INDOOR-001" in warnings
    assert schedule.iloc[0]["RELATIONSHIP_STATUS"] == "NEEDS_REVIEW"


def test_schedule_without_warnings_out_still_builds():
    schedule = build_equipment_schedule(INDOOR, OUTDOOR, RELATIONSHIPS)

    assert schedule.iloc[0]["RELATIONSHIP_STATUS"] == "OK"


# ── electrical_engine: empty summary must expose the same keys ───────────────
def test_empty_schedule_summary_has_same_keys_as_populated_summary():
    unit = electrical.calculate_unit_electrical(30_000, 8.79, 10.0, "220V / 1Ph")
    populated = electrical.compute_schedule_summary([unit], install_method="Conduit")
    empty = electrical.compute_schedule_summary([])

    assert set(empty) == set(populated)


def test_empty_schedule_summary_reports_requested_installation_options():
    empty = electrical.compute_schedule_summary(
        [], install_method="Buried", grouped_circuits=3
    )

    assert empty["install_method"] == "Buried"
    assert empty["grouped_circuits"] == 3
    assert empty["main_cable_ampacity_ok"] is True
    assert empty["main_mcb_ok"] is True
    assert empty["units_count"] == 0


# ── logging_setup ────────────────────────────────────────────────────────────
@pytest.fixture
def restore_root_logger():
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    for handler in saved_handlers:
        root.removeHandler(handler)
    try:
        yield root
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()
        for handler in saved_handlers:
            root.addHandler(handler)
        root.setLevel(saved_level)


def test_configure_logging_writes_a_rotating_file(tmp_path, restore_root_logger):
    log_path = configure_logging(log_dir=tmp_path)

    assert log_path == tmp_path / LOG_FILE_NAME
    logging.getLogger("regression").error("boom")
    assert "boom" in log_path.read_text(encoding="utf-8")


def test_configure_logging_is_idempotent(tmp_path, restore_root_logger):
    first = configure_logging(log_dir=tmp_path)
    second = configure_logging(log_dir=tmp_path)

    assert first == second
    marked = [
        handler
        for handler in restore_root_logger.handlers
        if getattr(handler, "_carrier_ac_selector_handler", False)
    ]
    # One file handler plus at most one console handler, never duplicated.
    assert len(marked) <= 2


def test_configure_logging_never_raises_when_directory_is_unusable(
    tmp_path, restore_root_logger
):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")

    assert configure_logging(log_dir=blocker) is None


def test_default_log_dir_honours_environment_override(tmp_path, monkeypatch):
    monkeypatch.setenv("CARRIER_LOG_DIR", str(tmp_path))

    assert default_log_dir() == tmp_path
