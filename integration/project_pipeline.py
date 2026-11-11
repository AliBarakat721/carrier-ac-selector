"""Qt-independent orchestration for importing a CAD + HAP project."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

import core
import ocr_engine
from cad.dxf_reader import read_dxf
from cad.space_detector import detect_spaces

from .models import HAPSystemRecord, IntegratedProject
from .room_matching import match_rooms_to_systems


def inspect_project(cad_path: str | Path, hap_path: str | Path, *,
                    explicit_mapping: Mapping[str, str] | None = None,
                    include_wall_lines: bool = True,
                    gap_tolerance: float = 0.0) -> IntegratedProject:
    """Read both source files and return a reviewable project snapshot.

    This function deliberately does not allocate aggregate HAP system loads
    to rooms. Room-level selection is a separate, explicitly gated operation.
    """
    cad_source, hap_source = Path(cad_path), Path(hap_path)
    if not cad_source.is_file():
        raise FileNotFoundError(f"CAD file not found: {cad_source}")
    if not hap_source.is_file():
        raise FileNotFoundError(f"HAP report not found: {hap_source}")

    document = read_dxf(cad_source)
    geometry_diagnostics: list[dict[str, object]] = []
    spaces = detect_spaces(document, include_wall_lines=include_wall_lines,
                           gap_tolerance=gap_tolerance,
                           diagnostics=geometry_diagnostics)
    hap_text = ocr_engine.extract_text(str(hap_source))
    parsed = core.parse_all_systems(hap_text)
    diagnostics: list[str] = []
    systems = [HAPSystemRecord(
        name=str(item.name), cooling_load_btu=float(item.btu),
        airflow_cfm=float(item.cfm), sensible_load_btu=float(item.sensible_btu),
        warnings=tuple(str(value) for value in item.warnings),
    ) for item in parsed]

    if not systems:
        single = core.parse_hap_text(hap_text)
        if single.btu > 0 or single.cfm > 0:
            systems.append(HAPSystemRecord(
                name="UNNAMED_SYSTEM", cooling_load_btu=float(single.btu),
                airflow_cfm=float(single.cfm), sensible_load_btu=float(single.sensible_btu),
                warnings=tuple(str(value) for value in single.warnings),
            ))
            diagnostics.append("HAP report has no identifiable air-system name; manual mapping is required.")
        else:
            diagnostics.append("No usable HAP air-system load/airflow records were parsed.")

    if not spaces:
        diagnostics.append("No candidate spaces were detected in the DXF; review drawing layers and geometry.")
    if not document.units_verified:
        diagnostics.append("DXF drawing units are unknown; geometry metrics require engineer confirmation.")
    for item in geometry_diagnostics:
        diagnostics.append(f"CAD_GEOMETRY:{item}")

    matches = match_rooms_to_systems(spaces, systems, explicit_mapping=explicit_mapping)
    if any(item.status != "MAPPED" for item in matches):
        diagnostics.append("One or more CAD spaces need explicit HAP system mapping.")
    return IntegratedProject(cad_source, hap_source, document, spaces, systems,
                             matches=matches, diagnostics=diagnostics)
