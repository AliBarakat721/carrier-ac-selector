import math

import pytest

import units


def test_capacity_airflow_and_area_round_trips():
    assert units.cooling_capacity_to_btu_h(10, "kw") == pytest.approx(34121.42, rel=1e-6)
    assert units.btu_h_to_cooling_capacity(34121.42, "kw") == pytest.approx(10, rel=1e-6)
    assert units.l_s_to_airflow(units.airflow_to_l_s(1000, "cfm"), "cfm") == pytest.approx(1000)
    assert units.m2_to_area(units.area_to_m2(100, "ft2"), "ft2") == pytest.approx(100)


def test_unit_families_are_explicit_and_reject_invalid_values():
    assert units.length_to_m(1000, "mm") == pytest.approx(1)
    assert units.pressure_to_pa(1, "psi") == pytest.approx(6894.757293)
    assert units.temperature_to_c(32, "°f") == pytest.approx(0)
    with pytest.raises(units.UnitConversionError):
        units.length_to_m(math.nan, "m")
    with pytest.raises(units.UnitConversionError):
        units.pressure_to_pa(1, "bar")
