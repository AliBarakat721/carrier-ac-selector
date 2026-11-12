"""Qt smoke tests for the room-review dialog (Phase 3, increment 2).

These run with ``QT_QPA_PLATFORM=offscreen`` and only assert the *wiring*:
the dialog must reflect the session, and every button must call the session
instead of re-implementing a rule.  The rules themselves are covered by
``tests/test_room_review_session.py``.
"""

from __future__ import annotations

import math
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

import ezdxf  # noqa: E402

from cad.layer_analyzer import LayerClass  # noqa: E402
from cad.room_review import ReviewStatus, RoomReviewSession  # noqa: E402
from room_review_dialog import RoomReviewDialog  # noqa: E402


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


def _plan(path, *, units: int = 6):
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = units
    for layer in ("A-WALL", "A-FURN", "A-ANNO-TEXT"):
        drawing.layers.new(layer)
    modelspace = drawing.modelspace()
    modelspace.add_lwpolyline(
        [(0, 0), (5, 0), (5, 4), (0, 4)], close=True, dxfattribs={"layer": "A-WALL"}
    )
    modelspace.add_circle(
        (10, 2), radius=math.sqrt(0.5 / math.pi), dxfattribs={"layer": "A-FURN"}
    )
    modelspace.add_text("Office", height=0.2, dxfattribs={"layer": "A-ANNO-TEXT"}).set_placement(
        (2.5, 2.0)
    )
    drawing.saveas(path)
    return path


def test_dialog_shows_one_row_per_candidate_and_per_layer(app, tmp_path):
    session = RoomReviewSession.from_path(_plan(tmp_path / "plan.dxf"))

    dialog = RoomReviewDialog(session)

    assert dialog.tbl_rooms.rowCount() == 2
    assert dialog.tbl_layers.rowCount() == len(session.document.layers)
    statuses = {
        dialog.tbl_rooms.item(row, 6).text() for row in range(dialog.tbl_rooms.rowCount())
    }
    assert statuses == {"AUTO_ACCEPTED", "BLOCKED"}
    assert "blocked by rules" in dialog.lbl_status.text()


def test_apply_is_disabled_when_nothing_is_accepted(app, tmp_path):
    session = RoomReviewSession.from_path(_plan(tmp_path / "plan.dxf", units=0))

    dialog = RoomReviewDialog(session)

    # Units unknown -> every candidate is pending, so there is nothing to apply.
    assert dialog.session.accepted_rooms == []
    assert dialog.btn_apply.isEnabled() is False
    assert "units are not confirmed" in dialog.btn_apply.toolTip().lower()


def test_confirming_units_from_the_dialog_updates_the_session(app, tmp_path):
    session = RoomReviewSession.from_path(_plan(tmp_path / "plan.dxf", units=0))
    dialog = RoomReviewDialog(session)

    index = dialog.cmb_units.findData("meters")
    dialog.cmb_units.setCurrentIndex(index)

    assert session.units_override == "meters"
    assert session.units_verified is True
    assert dialog.tbl_rooms.item(0, 5).text() != "\u2014"  # area now shown
    assert dialog.btn_apply.isEnabled() is True


def test_layer_override_from_the_dialog_reruns_detection(app, tmp_path):
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    drawing.layers.new("Layer1")
    modelspace = drawing.modelspace()
    modelspace.add_lwpolyline(
        [(0, 0), (5, 0), (5, 4), (0, 4)], close=True, dxfattribs={"layer": "Layer1"}
    )
    path = tmp_path / "unclassified.dxf"
    drawing.saveas(path)

    session = RoomReviewSession.from_path(path)
    dialog = RoomReviewDialog(session)
    assert session.usable_rooms == []

    row = next(
        index
        for index in range(dialog.tbl_layers.rowCount())
        if dialog.tbl_layers.item(index, 0).text() == "Layer1"
    )
    combo = dialog.tbl_layers.cellWidget(row, 1)
    combo.setCurrentIndex(combo.findData(LayerClass.WALLS.value))

    assert session.layer_overrides == {"Layer1": LayerClass.WALLS}
    assert session.wall_layers() == ["layer1"]
    assert session.rooms[0].layer_class == "WALLS"
    # The drawing has no room label, so the candidate still needs a decision.
    assert session.rooms[0].candidate_status == "NEEDS_REVIEW"
    dialog.btn_accept_all.click()
    assert len(session.usable_rooms) == 1


def test_accept_all_button_never_unblocks_a_rejected_candidate(app, tmp_path):
    session = RoomReviewSession.from_path(_plan(tmp_path / "plan.dxf", units=0))
    dialog = RoomReviewDialog(session)

    dialog.btn_accept_all.click()

    statuses = {
        dialog.tbl_rooms.item(row, 6).text() for row in range(dialog.tbl_rooms.rowCount())
    }
    assert "BLOCKED" in statuses
    assert all(row.status is not ReviewStatus.NEEDS_REVIEW for row in session.rows())


def test_result_rooms_are_only_the_usable_ones(app, tmp_path):
    session = RoomReviewSession.from_path(_plan(tmp_path / "plan.dxf"))
    dialog = RoomReviewDialog(session)

    rooms = dialog.result_rooms()

    assert len(rooms) == 1
    assert rooms[0].layer == "A-WALL"
    assert round(rooms[0].area, 3) == 20.0


def test_main_window_no_longer_calls_the_unreviewed_detector():
    """The GUI must go through the review session, not straight to detect_spaces.

    ``detect_spaces`` is still the geometry engine and is still exported, but
    calling it directly from ``main.py`` would bypass every Phase 3 rule.
    """

    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "main.py"
    text = source.read_text(encoding="utf-8")

    assert "from cad.room_review import RoomReviewSession" in text
    assert "RoomReviewDialog(" in text
    assert "detect_spaces(" not in text
