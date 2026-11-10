import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import electrical_engine as electrical


def test_parse_power_supply_detects_single_and_three_phase():
    assert electrical.parse_power_supply("220V / 1Ph / 50Hz") == (220.0, 1)
    assert electrical.parse_power_supply("380V / 3Ph") == (380.0, 3)
    assert electrical.parse_power_supply("415V / 3Ph") == (415.0, 3)


def test_unit_calculation_sizes_breaker_cable_and_voltage_drop():
    result = electrical.calculate_unit_electrical(
        cooling_btu=30_000,
        cooling_kw=8.79,
        eer=10.0,
        power_supply_str="220V / 1Ph",
        distance_m=20.0,
        power_factor=0.85,
        ambient_temp_c=40.0,
    )

    assert result["kw_elec"] == 3.0
    assert result["phase"] == 1
    assert result["mcb_size"] == 25
    assert result["cable_size"] >= 2.5
    assert result["vdrop_pct"] <= 3.0
    assert result["vdrop_ok"] is True


def test_grouping_derating_is_applied():
    one = electrical.calculate_unit_electrical(
        36_000, 10.55, 10.0, "220V / 1Ph", grouped_circuits=1
    )
    grouped = electrical.calculate_unit_electrical(
        36_000, 10.55, 10.0, "220V / 1Ph", grouped_circuits=4
    )

    assert electrical.get_grouping_derating(4) == 0.65
    assert grouped["cable_size"] >= one["cable_size"]


def test_schedule_summary_handles_mixed_phases_and_counts_quantities():
    single = electrical.calculate_unit_electrical(
        30_000, 8.79, 10.0, "220V / 1Ph"
    )
    three_phase = electrical.calculate_unit_electrical(
        60_000, 17.58, 10.0, "380V / 3Ph"
    )
    single["qty"] = 2
    three_phase["qty"] = 1

    summary = electrical.compute_schedule_summary(
        [single, three_phase], diversity_factor=0.9
    )

    assert summary["units_count"] == 3
    assert summary["has_mixed_phases"] is True
    assert summary["is_three_phase_panel"] is True
    assert summary["main_mcb"] > 0
    assert summary["main_cable"] > 0
    assert summary["warnings"]


def test_nan_and_unsupported_conductor_are_rejected():
    with pytest.raises(ValueError, match="NaN"):
        electrical.calculate_unit_electrical(
            math.nan, 8.0, 10.0, "220V / 1Ph"
        )

    with pytest.raises(NotImplementedError, match="Only copper"):
        electrical.calculate_unit_electrical(
            30_000, 8.79, 10.0, "220V / 1Ph", conductor_material="Al"
        )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"power_factor": 0.0}, "power_factor must be in"),
        ({"power_factor": 1.5}, "power_factor must be in"),
        ({"distance_m": -1.0}, "distance_m cannot be negative"),
    ],
)
def test_unit_calculation_rejects_invalid_pf_and_negative_distance(kwargs, message):
    with pytest.raises(ValueError, match=message):
        electrical.calculate_unit_electrical(
            30_000, 8.79, 10.0, "220V / 1Ph", **kwargs
        )


def test_main_feeder_earth_is_not_larger_than_small_phase_and_vdrop_upsizes():
    unit = electrical.calculate_unit_electrical(
        30_000, 8.79, 10.0, "220V / 1Ph"
    )
    short = electrical.compute_schedule_summary([unit], distance_to_main_m=25.0)
    long = electrical.compute_schedule_summary([unit], distance_to_main_m=50.0)

    assert short["main_cable"] == 4.0
    assert short["main_cable_desc"] == "2×4 mm² + 4 mm² E"
    assert long["main_cable"] > short["main_cable"]
    assert long["main_vdrop_pct"] <= 2.0


def test_main_feeder_warns_if_largest_cable_cannot_meet_voltage_drop_target():
    unit = electrical.calculate_unit_electrical(
        30_000, 8.79, 10.0, "220V / 1Ph"
    )
    summary = electrical.compute_schedule_summary([unit], distance_to_main_m=2500.0)

    assert summary["main_cable"] == electrical.STANDARD_CABLE_SIZES[-1]
    assert summary["main_vdrop_pct"] > 2.0
    assert any("largest listed cable" in warning for warning in summary["warnings"])
