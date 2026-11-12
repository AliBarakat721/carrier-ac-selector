import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import core
import units


def test_capacity_airflow_and_area_round_trips():
    assert units.cooling_capacity_to_btu_h(10, "kw") == pytest.approx(34121.42, rel=1e-6)
    assert units.btu_h_to_cooling_capacity(34121.42, "kw") == pytest.approx(10, rel=1e-6)
    assert units.l_s_to_airflow(units.airflow_to_l_s(1000, "cfm"), "cfm") == pytest.approx(1000)
    assert units.m2_to_area(units.area_to_m2(100, "ft2"), "ft2") == pytest.approx(100)


def test_all_unit_families_are_explicit_and_reject_invalid_values():
    assert units.length_to_m(1000, "mm") == pytest.approx(1)
    assert units.pressure_to_pa(1, "psi") == pytest.approx(6894.757293)
    assert units.temperature_to_c(32, "°f") == pytest.approx(0)
    with pytest.raises(units.UnitConversionError):
        units.length_to_m(math.nan, "m")
    with pytest.raises(units.UnitConversionError):
        units.pressure_to_pa(1, "bar")


def _room_report(total="100 MBH", sensible="60 MBH", latent="40 MBH"):
    return f"""--- page 3 of 8 (text layer) ---
Room Number: 101
Room Name: Office 101
Zone Name: East Zone
Room Area: 32.5 m2
Design Conditions: Indoor 24°C DB / 50% RH; Outdoor 35°C DB
Total cooling load: {total}
Sensible cooling load: {sensible}
Latent cooling load: {latent}
Heating load: 12 MBH
Supply Air 1,200 CFM
"""


def test_parse_hap_room_extracts_required_values_and_provenance():
    room = core.parse_hap_rooms(_room_report(), source_file="design.hap.pdf")[0]
    assert room.room_number.value == "101"
    assert room.room_name.value == "Office 101"
    assert room.zone.value == "East Zone"
    assert room.total_cooling_load.value == pytest.approx(100000)
    assert room.sensible_load.value == pytest.approx(60000)
    assert room.latent_load.value == pytest.approx(40000)
    assert room.heating_load.value == pytest.approx(12000)
    assert room.supply_airflow.value == pytest.approx(1200)
    assert room.room_area.value == pytest.approx(32.5)
    assert room.thermal_validation.status == "VALID"
    assert room.total_cooling_load.source.source_file == "design.hap.pdf"
    assert room.total_cooling_load.source.page == 3
    assert room.total_cooling_load.source.line_start is not None


def test_parse_hap_room_converts_square_feet_to_square_metres():
    room = core.parse_hap_rooms(
        "Room Number: 102\nRoom Name: Store\nRoom Area: 100 ft2\n"
    )[0]
    assert room.room_area.value == pytest.approx(9.290304)
    assert room.room_area.unit == "m2"


def test_thermal_validation_exposes_difference_and_warning():
    room = core.parse_hap_rooms(_room_report(latent="30 MBH"), source_file="x.rtf")[0]
    assert room.thermal_validation.status == "WARNING"
    assert room.thermal_validation.difference_btu == pytest.approx(10000)
    assert "10000" in room.thermal_validation.message


def test_thermal_warning_preserves_required_loads_but_marks_legacy_result_not_ok():
    result = core.parse_hap_text(
        "Room Number: 1\nRoom Name: Office\n"
        "Total cooling load: 100 MBH\n"
        "Sensible cooling load: 60 MBH\n"
        "Latent cooling load: 30 MBH\n"
        "Supply Air 100 CFM\n",
        source_file="warning.pdf",
    )
    assert result.has_required_loads
    assert result.btu == pytest.approx(100000)
    assert result.cfm == pytest.approx(100)
    assert result.warnings
    assert not result.ok
    assert result.source.source_file == "warning.pdf"


def test_multiple_rooms_remain_independent_and_legacy_result_is_not_a_sum():
    text = (
        _room_report().replace("101", "1", 1)
        + "\n"
        + _room_report(total="200 MBH", sensible="120 MBH", latent="80 MBH")
        .replace("101", "2", 1)
    )
    rooms = core.parse_hap_rooms(text, source_file="rooms.pdf")
    legacy = core.parse_hap_text(text, source_file="rooms.pdf")
    assert [room.total_cooling_load.value for room in rooms] == [100000, 200000]
    assert legacy.btu == 100000
    assert legacy.btu != sum(room.total_cooling_load.value for room in rooms)


def test_system_results_keep_source_file():
    text = (
        "Air System Sizing Summary for G01\n"
        "Central Cooling Coil\n"
        "Total coil load 100 MBH 8.33 Tons\n"
        "Coil CFM at Design 2000 CFM\n"
    )
    systems = core.parse_all_systems(text, source_file="systems.pdf")
    assert len(systems) == 1
    assert systems[0].source.source_file == "systems.pdf"


def test_missing_values_are_explicit_not_verified():
    room = core.parse_hap_rooms(
        "Room Number: 7\nRoom Name: Store\nTotal cooling load: 20 MBH\n",
        source_file="partial.pdf",
    )[0]
    assert room.total_cooling_load.status == "EXTRACTED"
    assert room.sensible_load.status == "MISSING"
    assert room.latent_load.status == "MISSING"
    assert room.supply_airflow.status == "MISSING"
    assert room.status == "NOT_VERIFIED"


def test_legacy_parse_result_keeps_selection_fields_and_thermal_warning():
    result = core.parse_hap_text(
        "Central Cooling Coil\n"
        "Total coil load 100 MBH 8.33 Tons\n"
        "Sensible coil load 60 MBH\n"
        "Latent coil load 40 MBH\n"
        "Coil CFM at Design 2000 CFM\n"
    )
    assert result.btu == pytest.approx(100000)
    assert result.sensible_btu == pytest.approx(60000)
    assert result.cfm == pytest.approx(2000)
    assert result.thermal_validation.status == "VALID"
