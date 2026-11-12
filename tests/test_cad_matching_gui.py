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

        assert window.table_cad.columnCount() == 7
        assert window.table_cad.item(0, 6).text() == "MATCHED / EXACT_NAME_MATCH"
        assert window.table_cad.item(1, 6).text() == "UNASSIGNED / NO_ROOM_NAME"
        # The unlabelled room must not have been given "Store" by position.
        assert "R2" not in window.cad_room_systems
        assert window.cad_room_systems == {"R1": "Office"}
    finally:
        window.close()
