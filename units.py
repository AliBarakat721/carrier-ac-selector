"""Explicit engineering unit conversions used by HAP and selection logic.

All public conversion functions return the canonical SI/base unit named in the
function.  Inputs are validated so missing or non-finite values cannot silently
become engineering values.
"""
from __future__ import annotations

import math
from typing import Literal


class UnitConversionError(ValueError):
    """Raised when a value or unit cannot be converted safely."""


def _number(value: float, name: str = "value") -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise UnitConversionError(f"{name} must be numeric") from exc
    if not math.isfinite(result):
        raise UnitConversionError(f"{name} must be finite")
    return result


def _unit(unit: str, allowed: set[str], name: str = "unit") -> str:
    value = str(unit).strip().lower().replace("²", "2")
    if value not in allowed:
        raise UnitConversionError(f"Unsupported {name}: {unit!r}")
    return value


def cooling_capacity_to_btu_h(value: float, unit: Literal["btu/h", "btu/hr", "kw", "tr"]) -> float:
    value = _number(value)
    unit = _unit(unit, {"btu/h", "btu/hr", "kw", "tr"}, "capacity unit")
    return {"btu/h": value, "btu/hr": value, "kw": value * 3412.142,
            "tr": value * 12000.0}[unit]


def btu_h_to_cooling_capacity(value: float, unit: Literal["btu/h", "kw", "tr"]) -> float:
    value = _number(value)
    unit = _unit(unit, {"btu/h", "kw", "tr"}, "capacity unit")
    return {"btu/h": value, "kw": value / 3412.142, "tr": value / 12000.0}[unit]


def airflow_to_l_s(value: float, unit: Literal["cfm", "l/s", "m3/h"]) -> float:
    value = _number(value)
    unit = _unit(unit, {"cfm", "l/s", "m3/h"}, "airflow unit")
    return {"cfm": value * 0.47194745, "l/s": value, "m3/h": value / 3.6}[unit]


def l_s_to_airflow(value: float, unit: Literal["cfm", "l/s", "m3/h"]) -> float:
    value = _number(value)
    unit = _unit(unit, {"cfm", "l/s", "m3/h"}, "airflow unit")
    return {"cfm": value / 0.47194745, "l/s": value, "m3/h": value * 3.6}[unit]


def length_to_m(value: float, unit: Literal["mm", "cm", "m", "inch", "ft"]) -> float:
    value = _number(value)
    unit = _unit(unit, {"mm", "cm", "m", "inch", "in", "ft"}, "length unit")
    return {"mm": value / 1000.0, "cm": value / 100.0, "m": value,
            "inch": value * 0.0254, "in": value * 0.0254, "ft": value * 0.3048}[unit]


def m_to_length(value: float, unit: Literal["mm", "cm", "m", "inch", "ft"]) -> float:
    value = _number(value)
    unit = _unit(unit, {"mm", "cm", "m", "inch", "in", "ft"}, "length unit")
    return {"mm": value * 1000.0, "cm": value * 100.0, "m": value,
            "inch": value / 0.0254, "in": value / 0.0254, "ft": value / 0.3048}[unit]


def pressure_to_pa(value: float, unit: Literal["pa", "kpa", "psi"]) -> float:
    value = _number(value)
    unit = _unit(unit, {"pa", "kpa", "psi"}, "pressure unit")
    return {"pa": value, "kpa": value * 1000.0, "psi": value * 6894.757293}[unit]


def pa_to_pressure(value: float, unit: Literal["pa", "kpa", "psi"]) -> float:
    value = _number(value)
    unit = _unit(unit, {"pa", "kpa", "psi"}, "pressure unit")
    return {"pa": value, "kpa": value / 1000.0, "psi": value / 6894.757293}[unit]


def temperature_to_c(value: float, unit: Literal["c", "°c", "f", "°f"]) -> float:
    value = _number(value)
    unit = _unit(unit, {"c", "°c", "f", "°f"}, "temperature unit")
    return value if unit in {"c", "°c"} else (value - 32.0) * 5.0 / 9.0


def c_to_temperature(value: float, unit: Literal["c", "°c", "f", "°f"]) -> float:
    value = _number(value)
    unit = _unit(unit, {"c", "°c", "f", "°f"}, "temperature unit")
    return value if unit in {"c", "°c"} else value * 9.0 / 5.0 + 32.0


def area_to_m2(value: float, unit: Literal["m2", "ft2"]) -> float:
    value = _number(value)
    unit = _unit(unit, {"m2", "ft2", "ft^2"}, "area unit")
    return value if unit == "m2" else value * 0.09290304


def m2_to_area(value: float, unit: Literal["m2", "ft2"]) -> float:
    value = _number(value)
    unit = _unit(unit, {"m2", "ft2", "ft^2"}, "area unit")
    return value if unit == "m2" else value / 0.09290304
