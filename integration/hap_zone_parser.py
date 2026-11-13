"""Parse room/space-level data from native-text HAP Zone Sizing summaries."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass


_ZONE_HEADING = re.compile(r"^\s*Zone Sizing Summary for\s+(.+?)\s*$", re.IGNORECASE)
_NUMBER = re.compile(r"^[+-]?(?:\d[\d,]*)(?:\.\d+)?$")
_PEAK_TIME = re.compile(r"^[A-Za-z]{3}\s+\d{3,4}$")


@dataclass(frozen=True)
class HAPZoneSpaceData:
    system_name: str
    space_name: str
    zone_count: int
    space_count: int
    multiplier: float
    sensible_load_btu: float
    airflow_cfm: float
    source_reference: str = "HAP_ZONE_SIZING_SUMMARY"


def _number(value: str) -> float | None:
    text = value.strip().replace(",", "")
    if not _NUMBER.fullmatch(text):
        return None
    number = float(text)
    return number if math.isfinite(number) else None


def _following_value(lines: list[str], label: str) -> str | None:
    wanted = label.casefold()
    for index, line in enumerate(lines):
        if line.strip().casefold() == wanted:
            for candidate in lines[index + 1:]:
                if candidate.strip():
                    return candidate.strip()
    return None


def _space_rows(block: list[str], system_name: str, zone_count: int) -> list[HAPZoneSpaceData]:
    start = next((i for i, line in enumerate(block)
                  if line.strip().casefold() == "space loads and airflows"), None)
    if start is None:
        return []
    end = next((i for i in range(start + 1, len(block))
                if "ventilation sizing summary" in block[i].casefold()), len(block))
    values = [line.strip() for line in block[start + 1:end] if line.strip()]
    rows: list[HAPZoneSpaceData] = []
    # HAP's table rows are: space, multiplier, sensible MBH, peak time,
    # airflow CFM, heating MBH, floor area, CFM/ft².
    for index in range(len(values) - 7):
        multiplier = _number(values[index + 1])
        sensible_mbh = _number(values[index + 2])
        airflow = _number(values[index + 4])
        heating_mbh = _number(values[index + 5])
        area = _number(values[index + 6])
        cfm_per_area = _number(values[index + 7])
        if (
            not values[index]
            or multiplier is None or multiplier <= 0
            or sensible_mbh is None or sensible_mbh < 0
            or not _PEAK_TIME.fullmatch(values[index + 3])
            or airflow is None or airflow < 0
            or heating_mbh is None or heating_mbh < 0
            or area is None or area < 0
            or cfm_per_area is None or cfm_per_area < 0
        ):
            continue
        space_name = values[index]
        if space_name.casefold().startswith("zone "):
            continue
        rows.append(HAPZoneSpaceData(
            system_name=system_name,
            space_name=space_name,
            zone_count=zone_count,
            space_count=0,  # populated after the full table is scanned
            multiplier=multiplier,
            sensible_load_btu=sensible_mbh * 1000.0,
            airflow_cfm=airflow,
        ))
    count = len(rows)
    return [HAPZoneSpaceData(
        system_name=row.system_name,
        space_name=row.space_name,
        zone_count=row.zone_count,
        space_count=count,
        multiplier=row.multiplier,
        sensible_load_btu=row.sensible_load_btu,
        airflow_cfm=row.airflow_cfm,
        source_reference=row.source_reference,
    ) for row in rows]


def parse_hap_zone_summaries(text: str) -> list[HAPZoneSpaceData]:
    """Return validated space-level sensible load and airflow rows.

    This intentionally does not infer total cooling load. That value is only
    eligible for a one-zone/one-space proxy after the caller verifies the
    system-to-space relationship and reviews the imported values.
    """
    lines = text.splitlines()
    headings = [(index, match.group(1).strip())
                for index, line in enumerate(lines)
                if (match := _ZONE_HEADING.match(line))]
    result: list[HAPZoneSpaceData] = []
    for position, (start, heading_name) in enumerate(headings):
        end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        block = lines[start:end]
        system_name = _following_value(block, "Air System Name") or heading_name
        zones_raw = _following_value(block, "Number of zones")
        zones_value = _number(zones_raw or "")
        zone_count = int(zones_value) if zones_value and zones_value.is_integer() else 0
        result.extend(_space_rows(block, system_name, zone_count))
    return result
