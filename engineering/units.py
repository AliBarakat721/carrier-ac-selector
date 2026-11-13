"""Shared unit constants and tolerant numeric parsing (no Qt, no pandas).

Moved verbatim from ``core.py`` (Phase 1). ``core`` re-exports every name.
"""
from __future__ import annotations

import math
import re
from typing import Literal


BTU_PER_TON = 12_000.0
BTU_PER_KW = 3_412.14
_NUMERIC_NA = {"", "—", "-", "N/A", "nan", "None"}


def to_float(value):
    """Return value as float, or None for blanks / em-dash / unparsable.

    Tolerates the '≈' prefix used for approximate catalogue values and
    distinguishes thousands separators from European decimal commas:
        '1,234.5' -> 1234.5    '1.234,5' -> 1234.5    '10,11' -> 10.11
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            if value != value:
                return None
        except Exception:
            pass
        result = float(value)
        return result if math.isfinite(result) else None
    s = str(value).replace("≈", "").strip()
    if s in _NUMERIC_NA:
        return None
    # Mirror parse_user_number's separator logic (decimal comma vs thousands).
    if ',' in s and '.' in s:
        if s.rfind('.') > s.rfind(','):
            s = s.replace(',', '')                     # 1,234.5 -> US
        else:
            s = s.replace('.', '').replace(',', '.')   # 1.234,5 -> EU
    elif ',' in s:
        if re.fullmatch(r'-?\d{1,3}(,\d{3})+', s):
            s = s.replace(',', '')                     # 1,234,567 -> thousands
        else:
            s = s.replace(',', '.')                    # 10,11 -> 10.11
    try:
        result = float(s)
        return result if math.isfinite(result) else None
    except ValueError:
        return None


def parse_user_number(text):
    """Parse finite user numbers with comma or dot thousands/decimal separators."""
    if text is None:
        return 0.0
    s = str(text).strip().replace(" ", "")
    if not s:
        return 0.0
    # استبعاد الكلمات غير الرقمية مثل nan, inf
    if re.search(r'[a-zA-Z]', s):
        return None

    if ',' in s and '.' in s:
        if s.rfind('.') > s.rfind(','):
            s = s.replace(',', '')
        else:
            s = s.replace('.', '').replace(',', '.')
    elif ',' in s:
        # إذا كانت الصيغة فاصل آلاف مثل 12,500 أو 1,000,000
        if re.fullmatch(r'\d{1,3}(,\d{3})+', s):
            s = s.replace(',', '')
        else:
            # فاصلة عشرية مثل 12,5
            s = s.replace(',', '.')
    elif re.fullmatch(r'\d{1,3}(?:\.\d{3})+', s):
        # A lone dot between three-digit groups is a thousands separator in
        # common European/Egyptian notation (e.g. 82.800 -> 82800).
        s = s.replace('.', '')

    try:
        val = float(s)
        if not math.isfinite(val) or val < 0:
            return None
        return val
    except ValueError:
        return None

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


__all__ = [
    "UnitConversionError", "BTU_PER_TON", "BTU_PER_KW", "to_float",
    "parse_user_number", "cooling_capacity_to_btu_h",
    "btu_h_to_cooling_capacity", "airflow_to_l_s", "l_s_to_airflow",
    "length_to_m", "m_to_length", "pressure_to_pa", "pa_to_pressure",
    "temperature_to_c", "c_to_temperature", "area_to_m2", "m2_to_area",
]
