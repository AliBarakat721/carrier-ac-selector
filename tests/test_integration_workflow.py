from types import SimpleNamespace

import ezdxf
import pandas as pd
import pytest

from integration.models import HAPSystemRecord, RoomLoadInput
from integration.project_pipeline import inspect_project
from integration.room_matching import match_rooms_to_systems
from integration.selection_adapter import select_for_room


def _space(room_id, label=None):
    return SimpleNamespace(room_id=room_id, label=label)


def test_room_mapping_normalizes_unique_names_and_allows_many_rooms_per_system():
    spaces = [_space("G01", "AHU - East"), _space("G02", "AHU East")]
    systems = [HAPSystemRecord("AHU-East", 100_000, 2_000)]
    matches = match_rooms_to_systems(spaces, systems)
    assert [(item.system_name, item.status) for item in matches] == [
        ("AHU-East", "MAPPED"), ("AHU-East", "MAPPED")
    ]


def test_explicit_mapping_overrides_label_and_unknown_mapping_is_not_silent():
    spaces = [_space("G01", "Other")]
    systems = [HAPSystemRecord("AHU-1", 50_000, 900)]
    assert match_rooms_to_systems(spaces, systems, explicit_mapping={"G01": "AHU-1"})[0].method == "explicit"
    result = match_rooms_to_systems(spaces, systems, explicit_mapping={"G01": "MISSING"})[0]
    assert result.status == "UNMATCHED"
    assert "Unknown HAP system" in result.diagnostic


def test_duplicate_normalized_system_names_are_ambiguous():
    result = match_rooms_to_systems(
        [_space("G01", "AHU East")],
        [HAPSystemRecord("AHU-East", 1, 1), HAPSystemRecord("AHU East", 2, 2)],
    )[0]
    assert result.status == "AMBIGUOUS"


def test_unmatched_room_requires_engineer_mapping():
    result = match_rooms_to_systems([_space("G01", "Office")], [HAPSystemRecord("AHU-1", 1, 1)])[0]
    assert result.status == "UNMATCHED"


def test_aggregate_only_hap_data_cannot_be_used_as_room_load(monkeypatch):
    def should_not_run(*args, **kwargs):
        raise AssertionError("Carrier selection must not run without room-level inputs")
    monkeypatch.setattr("integration.selection_adapter.core.select_units", should_not_run)
    result = select_for_room(
        pd.DataFrame(),
        RoomLoadInput("G01", 0, 0, data_scope="ROOM", source_reference="ENGINEER_CONFIRMED"),
    )
    assert result.status == "BLOCKED_MISSING_ROOM_LOAD"


def test_positive_system_total_is_blocked_from_room_selection(monkeypatch):
    def should_not_run(*args, **kwargs):
        raise AssertionError("A system total must not be passed into room selection")
    monkeypatch.setattr("integration.selection_adapter.core.select_units", should_not_run)
    result = select_for_room(
        pd.DataFrame(),
        RoomLoadInput("G01", 100_000, 2_000, data_scope="SYSTEM", source_reference="HAP_SYSTEM_TOTAL"),
    )
    assert result.status == "BLOCKED_UNVERIFIED_LOAD_SCOPE"


def test_room_selection_delegates_to_existing_core_engine(monkeypatch):
    marker = pd.DataFrame([{"System Model": "MODEL-1"}])
    captured = {}
    def fake_select(database, btu, cfm, **kwargs):
        captured.update(btu=btu, cfm=cfm, kwargs=kwargs)
        return marker
    monkeypatch.setattr("integration.selection_adapter.core.select_units", fake_select)
    load = RoomLoadInput(
        "G01", 50_000, 900, sensible_load_btu=35_000,
        data_scope="ROOM", source_reference="ENGINEER_CONFIRMED",
    )
    result = select_for_room(pd.DataFrame(), load, safety_pct=5)
    assert result.status == "CANDIDATES_FOUND"
    assert result.candidates is marker
    assert captured["btu"] == 50_000 and captured["cfm"] == 900
    assert captured["kwargs"]["sensible_btu"] == 35_000


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1])
def test_invalid_room_load_values_are_rejected(bad):
    with pytest.raises(ValueError):
        select_for_room(
            pd.DataFrame(),
            RoomLoadInput("G01", bad, 900, data_scope="ROOM", source_reference="ENGINEER_CONFIRMED"),
        )


def test_inspect_project_reads_dxf_and_hap_without_distributing_system_load(tmp_path, monkeypatch):
    cad_path = tmp_path / "plan.dxf"
    hap_path = tmp_path / "report.rtf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    drawing.modelspace().add_lwpolyline([(0, 0), (5, 0), (5, 4), (0, 4)], close=True)
    drawing.saveas(cad_path)
    hap_path.write_text("synthetic HAP", encoding="utf-8")
    monkeypatch.setattr("integration.project_pipeline.ocr_engine.extract_text", lambda _path: (
        "Air System Sizing Summary for AHU-1\nCentral Cooling Coil\n"
        "Total coil load 100 MBH 8.33 Tons\nCoil CFM at Design 2000 CFM\n"
    ))
    project = inspect_project(cad_path, hap_path)
    assert len(project.spaces) == 1
    assert len(project.hap_systems) == 1
    assert project.hap_systems[0].cooling_load_btu == 100_000
    assert project.matches[0].status == "UNMATCHED"
    assert not hasattr(project.spaces[0], "cooling_load_btu")


def test_inspect_project_rejects_missing_inputs(tmp_path):
    with pytest.raises(FileNotFoundError, match="CAD file"):
        inspect_project(tmp_path / "missing.dxf", tmp_path / "missing.rtf")
