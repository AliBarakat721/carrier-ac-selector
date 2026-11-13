"""Qt smoke test: the CAD table surfaces the Phase 4 matching status.

The matching rules themselves live in ``cad/room_matching.py`` and are covered
by ``tests/test_room_system_matching.py``.  This test only checks that the
window renders the status/reason and never invents an assignment.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402
from shapely.geometry import Polygon  # noqa: E402

import main  # noqa: E402
from integration.hap_zone_parser import HAPZoneSpaceData  # noqa: E402


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


def _room(room_id, label, x):
    return SimpleNamespace(
        room_id=room_id,
        label=label,
        polygon=Polygon([(x, 0), (x + 5, 0), (x + 5, 4), (x, 4)]),
        area=20.0,
        perimeter=18.0,
        raw_area=20.0,
        raw_perimeter=18.0,
        source="closed_polyline",
        status="ACCEPTED",
        unit_status="VERIFIED",
        drawing_units="meters",
    )


def test_cad_table_shows_match_status_and_never_guesses(app):
    window = main.HAPToCarrierApp()
    try:
        # The CAD table is created with the file-specific workspace tab.
        window._open_cad_workspace("plan.dxf")
        window.cad_rooms = [
            _room("R1", "Office", 0),
            _room("R2", None, 10),
        ]
        window.systems = [SimpleNamespace(name="Office"), SimpleNamespace(name="Store")]

        window._refresh_cad_workflow()

        assert window.table_cad.columnCount() == 11
        assert window.table_cad.item(0, 9).text() == "MATCHED / EXACT_NAME_MATCH"
        assert window.table_cad.item(1, 9).text() == "UNASSIGNED / NO_ROOM_NAME"
        # The unlabelled room must not have been given "Store" by position.
        assert "R2" not in window.cad_room_systems
        assert window.cad_room_systems == {"R1": "Office"}
    finally:
        window.close()


def test_unique_single_zone_hap_space_prefills_only_its_matching_room(app):
    window = main.HAPToCarrierApp()
    try:
        window.cad_rooms = [_room("R1", "G01", 0), _room("R2", "Other", 10)]
        window.systems = [SimpleNamespace(name="G01", btu=25_300, cfm=995, sensible_btu=22_800, warnings=[])]
        window.cad_room_systems = {"R1": "G01"}
        window.hap_zone_data = [HAPZoneSpaceData(
            system_name="G01", space_name="G01", zone_count=1, space_count=1,
            multiplier=1, sensible_load_btu=21_300, airflow_cfm=995,
        )]

        window._apply_hap_zone_inputs()

        assert window.cad_room_loads["R1"]["cooling_load_btu"] == 25_300
        assert window.cad_room_loads["R1"]["source_reference"] == "HAP_AUTO_SINGLE_ZONE_COIL_PROXY"
        assert "R2" not in window.cad_room_loads
    finally:
        window.close()


def test_hap_total_is_not_copied_to_room_in_multi_zone_system(app):
    window = main.HAPToCarrierApp()
    try:
        window.cad_rooms = [_room("R1", "G01", 0)]
        window.systems = [SimpleNamespace(name="G01", btu=80_000, cfm=2_000, sensible_btu=60_000, warnings=[])]
        window.cad_room_systems = {"R1": "G01"}
        window.hap_zone_data = [HAPZoneSpaceData(
            system_name="G01", space_name="G01", zone_count=2, space_count=1,
            multiplier=1, sensible_load_btu=21_300, airflow_cfm=995,
        )]

        window._apply_hap_zone_inputs()

        assert window.cad_room_loads["R1"]["cooling_load_btu"] == 0
        assert window.cad_room_loads["R1"]["airflow_cfm"] == 995
    finally:
        window.close()
