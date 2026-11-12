"""Thin adapter to the existing Carrier selection engine."""
from __future__ import annotations
import math
from pathlib import Path
from typing import Any

import core
from .models import RoomLoadInput, RoomSelectionResult


def select_for_room(database: Any, load: RoomLoadInput, *, safety_pct: float = 0.0,
                    cfm_cover_pct: float = 100.0, derate_pct: float = 0.0,
                    category: str | None = None, type_: str | None = None,
                    power_supply: str | None = None, series: str | None = None) -> RoomSelectionResult:
    """Run the existing selector for one room using verified room-level inputs."""
    if not load.room_id.strip():
        raise ValueError("room_id is required")
    for name, value in (("cooling_load_btu", load.cooling_load_btu),
                        ("airflow_cfm", load.airflow_cfm),
                        ("sensible_load_btu", load.sensible_load_btu)):
        if not math.isfinite(float(value)) or float(value) < 0:
            raise ValueError(f"{name} must be a finite non-negative number")
    if load.cooling_load_btu <= 0 or load.airflow_cfm <= 0:
        return RoomSelectionResult(load.room_id, "BLOCKED_MISSING_ROOM_LOAD", None, load,
                                   ("Positive room-level cooling load and airflow are required; system totals cannot be substituted.",))
    candidates = core.select_units(
        database, load.cooling_load_btu, load.airflow_cfm,
        safety_pct=safety_pct, cfm_cover_pct=cfm_cover_pct,
        derate_pct=derate_pct, sensible_btu=load.sensible_load_btu,
        category=category, type_=type_, power_supply=power_supply, series=series,
    )
    status = "CANDIDATES_FOUND" if not candidates.empty else "NO_MATCH"
    return RoomSelectionResult(load.room_id, status, candidates, load)


def load_carrier_database(path: str | Path):
    """Load catalogue with the same validation used by the existing app."""
    return core.load_database(str(path))
