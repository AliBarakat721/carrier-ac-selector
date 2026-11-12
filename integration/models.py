"""Typed, Qt-independent records shared by the integrated project workflow."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class HAPSystemRecord:
    name: str
    cooling_load_btu: float
    airflow_cfm: float
    sensible_load_btu: float = 0.0
    warnings: tuple[str, ...] = ()

    @property
    def is_complete(self) -> bool:
        return self.cooling_load_btu > 0 and self.airflow_cfm > 0


@dataclass(frozen=True)
class RoomLoadInput:
    """Room-level design data; never populate this from a system total implicitly."""
    room_id: str
    cooling_load_btu: float
    airflow_cfm: float
    sensible_load_btu: float = 0.0
    system_id: str | None = None
    source_reference: str = "ENGINEER_INPUT"


@dataclass(frozen=True)
class RoomSystemMatch:
    room_id: str
    system_name: str | None
    status: str
    method: str
    diagnostic: str | None = None


@dataclass
class IntegratedProject:
    cad_path: Path
    hap_path: Path
    cad_document: Any
    spaces: list[Any]
    hap_systems: list[HAPSystemRecord]
    matches: list[RoomSystemMatch] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RoomSelectionResult:
    room_id: str
    status: str
    candidates: Any
    load_input: RoomLoadInput
    diagnostics: tuple[str, ...] = ()
