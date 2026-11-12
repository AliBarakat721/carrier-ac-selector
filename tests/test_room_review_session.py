"""Tests for the interactive room-review session (Phase 3, increment 2).

These tests are headless on purpose: the review *workflow* must be provable
without opening a Qt window.  The GUI is only a table that calls these methods.

The rules under test:

1. A rule-rejected candidate cannot be accepted from the review table; the
   reviewer has to re-classify the layer and let the rules run again.
2. A room is only usable for engineering when it is accepted **and** the
   drawing units are known.
3. Reviewer decisions are keyed by geometry, so they survive re-detection that
   renumbers the rooms.
4. ``to_dict`` / ``apply_dict`` round-trip the whole review state, which is the
   only thing a future project file would need to store.
"""

from __future__ import annotations

import json
import math

import ezdxf
import pytest

from cad.layer_analyzer import LayerClass
from cad.room_review import (
    ReviewAction,
    ReviewStatus,
    RoomReviewSession,
    room_key,
)


def _plan(path, *, units: int = 6):
    """5x4 m room on A-WALL, 0.5 m2 furniture circle, room label on A-ANNO-TEXT.

    ``units=0`` writes an explicitly unitless header, which is what the review
    UI has to cope with on real drawings.
    """

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


def _unclassified_plan(path):
    """Same geometry, but the wall layer is called Layer1 so it cannot be classified."""

    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    for layer in ("Layer1", "A-ANNO-TEXT"):
        drawing.layers.new(layer)
    modelspace = drawing.modelspace()
    modelspace.add_lwpolyline(
        [(0, 0), (5, 0), (5, 4), (0, 4)], close=True, dxfattribs={"layer": "Layer1"}
    )
    modelspace.add_text("Office", height=0.2, dxfattribs={"layer": "A-ANNO-TEXT"}).set_placement(
        (2.5, 2.0)
    )
    drawing.saveas(path)
    return path


# ---------------------------------------------------------------------------
# Default state
# ---------------------------------------------------------------------------


def test_confirmed_room_is_auto_accepted_and_furniture_is_blocked(tmp_path):
    session = RoomReviewSession.from_path(_plan(tmp_path / "plan.dxf"))

    by_layer = {row.layer: row for row in session.rows()}

    assert by_layer["A-WALL"].status is ReviewStatus.AUTO_ACCEPTED
    assert by_layer["A-WALL"].usable is True
    assert by_layer["A-FURN"].status is ReviewStatus.BLOCKED
    assert by_layer["A-FURN"].usable is False
    assert [room.layer for room in session.usable_rooms] == ["A-WALL"]
    assert session.ready_for_engineering is True


def test_status_line_and_summary_count_every_candidate(tmp_path):
    session = RoomReviewSession.from_path(_plan(tmp_path / "plan.dxf"))

    summary = session.summary()

    assert summary["total"] == 2
    assert summary["AUTO_ACCEPTED"] == 1
    assert summary["BLOCKED"] == 1
    assert summary["usable"] == 1
    assert "units NOT confirmed" not in session.status_line()


def test_unverified_units_make_every_room_unusable(tmp_path):
    session = RoomReviewSession.from_path(_plan(tmp_path / "plan.dxf", units=0))

    assert session.units_verified is False
    assert session.usable_rooms == []
    assert session.accepted_rooms == []
    assert session.pending_rooms  # units unverified -> nothing is auto-accepted
    assert "DRAWING_UNITS_UNVERIFIED" in session.rooms[0].candidate_reasons
    assert any("units are not confirmed" in message.lower() for message in session.blockers())
    assert all(row.area_m2 is None for row in session.rows())


def test_confirming_units_makes_the_room_usable(tmp_path):
    session = RoomReviewSession.from_path(_plan(tmp_path / "plan.dxf", units=0))

    session.set_units("meters")

    assert session.units_verified is True
    assert session.units_label == "meters"
    assert len(session.usable_rooms) == 1
    assert round(session.usable_rooms[0].area, 3) == 20.0
    assert session.report.units_source == "USER_CONFIRMED"


def test_millimetre_drawing_is_scaled_when_units_are_confirmed(tmp_path):
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 0  # unitless on purpose
    drawing.layers.new("A-WALL")
    modelspace = drawing.modelspace()
    # 5000 x 4000 mm room, header left unitless on purpose.
    modelspace.add_lwpolyline(
        [(0, 0), (5000, 0), (5000, 4000), (0, 4000)],
        close=True,
        dxfattribs={"layer": "A-WALL"},
    )
    path = tmp_path / "mm.dxf"
    drawing.saveas(path)

    session = RoomReviewSession.from_path(path)
    assert session.usable_rooms == []

    session.set_units("millimeters")
    session.accept_all_reviewable()  # the room has no text label in this drawing

    assert len(session.usable_rooms) == 1
    assert round(session.usable_rooms[0].area, 1) == 20.0


def test_unsupported_units_are_rejected(tmp_path):
    session = RoomReviewSession.from_path(_plan(tmp_path / "plan.dxf"))

    with pytest.raises(ValueError, match="Cannot confirm"):
        session.set_units("furlongs")


# ---------------------------------------------------------------------------
# The central rule: you cannot tick a blocked candidate
# ---------------------------------------------------------------------------


def test_blocked_candidate_cannot_be_accepted(tmp_path):
    session = RoomReviewSession.from_path(_plan(tmp_path / "plan.dxf"))
    furniture = next(row for row in session.rows() if row.layer == "A-FURN")

    with pytest.raises(ValueError, match="rejected by the detection rules"):
        session.accept(furniture.room_id)

    assert session.room_decisions == {}


def test_reclassifying_the_layer_is_the_way_to_unblock(tmp_path):
    session = RoomReviewSession.from_path(_unclassified_plan(tmp_path / "plan.dxf"))

    # Layer1 cannot be classified, so the closed polyline is found but is only
    # a NEEDS_REVIEW candidate, never an accepted room.
    assert session.wall_layers() == []
    assert len(session.rooms) == 1
    assert session.rooms[0].candidate_status == "NEEDS_REVIEW"
    assert "LAYER_CLASS_UNKNOWN" in session.rooms[0].candidate_reasons
    assert session.usable_rooms == []

    session.set_layer_class("Layer1", LayerClass.WALLS)

    assert session.wall_layers() == ["layer1"]
    assert len(session.rooms) == 1
    assert session.rooms[0].candidate_status == "CONFIRMED"
    assert session.rooms[0].layer_class == "WALLS"
    assert len(session.usable_rooms) == 1
    assert session.layer_overrides == {"Layer1": LayerClass.WALLS}


def test_clearing_an_override_restores_the_automatic_classification(tmp_path):
    session = RoomReviewSession.from_path(_unclassified_plan(tmp_path / "plan.dxf"))
    session.set_layer_class("Layer1", LayerClass.WALLS)

    session.clear_layer_override("Layer1")

    assert session.layer_overrides == {}
    assert session.rooms[0].candidate_status == "NEEDS_REVIEW"
    assert session.usable_rooms == []


def test_layer_rows_put_walls_first_and_flag_overrides(tmp_path):
    session = RoomReviewSession.from_path(_plan(tmp_path / "plan.dxf"))
    session.set_layer_class("A-FURN", LayerClass.OTHER)

    rows = session.layer_rows()

    assert rows[0]["layer_class"] == "WALLS"
    furniture = next(row for row in rows if row["layer"] == "A-FURN")
    assert furniture["overridden"] is True
    assert furniture["layer_class"] == "OTHER"
    assert furniture["boundary"] is False


# ---------------------------------------------------------------------------
# Reviewer decisions
# ---------------------------------------------------------------------------


def test_rejecting_a_confirmed_room_removes_it_from_engineering(tmp_path):
    session = RoomReviewSession.from_path(_plan(tmp_path / "plan.dxf"))
    office = next(row for row in session.rows() if row.layer == "A-WALL")

    session.reject(office.room_id)

    assert session.usable_rooms == []
    assert len(session.rejected_rooms) == 1
    assert session.ready_for_engineering is False
    # The polygon is still reported, so the decision stays auditable.
    assert len(session.rooms) == 2


def test_clearing_a_decision_returns_the_room_to_auto_accepted(tmp_path):
    session = RoomReviewSession.from_path(_plan(tmp_path / "plan.dxf"))
    office = next(row for row in session.rows() if row.layer == "A-WALL")
    session.reject(office.room_id)

    session.clear_decision(office.room_id)

    assert session.usable_rooms != []
    assert session.room_decisions == {}


def test_accept_all_reviewable_never_touches_blocked_candidates(tmp_path):
    session = RoomReviewSession.from_path(_plan(tmp_path / "plan.dxf", units=0))

    session.accept_all_reviewable()

    statuses = {row.layer: row.status for row in session.rows()}
    assert statuses["A-WALL"] is ReviewStatus.ACCEPTED
    assert statuses["A-FURN"] is ReviewStatus.BLOCKED
    assert session.pending_rooms == []

    # Accepted but still not usable: the units are unknown.
    assert session.usable_rooms == []
    session.set_units("meters")
    assert len(session.usable_rooms) == 1


def test_pending_room_blocks_readiness_until_decided(tmp_path):
    session = RoomReviewSession.from_path(_plan(tmp_path / "plan.dxf", units=0))
    # Units unknown -> nothing is usable, so nothing is auto-accepted either way.
    session.set_units("meters")
    session.set_min_area_m2(0.0)  # keep the furniture candidate for the check
    assert session.pending_rooms == []

    # Force a review state by requiring labels.
    session.require_label = True
    session.refresh()
    assert session.pending_rooms
    assert session.ready_for_engineering is False

    session.accept_all_reviewable()
    assert session.pending_rooms == []
    assert session.ready_for_engineering is True


def test_unknown_room_id_raises_a_clear_error(tmp_path):
    session = RoomReviewSession.from_path(_plan(tmp_path / "plan.dxf"))

    with pytest.raises(KeyError, match="No detected room"):
        session.accept("ROOM-999")


def test_min_area_threshold_can_be_lowered_for_a_genuinely_small_room(tmp_path):
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    drawing.layers.new("A-WALL")
    modelspace = drawing.modelspace()
    # 0.8 x 0.8 m shaft = 0.64 m2, a real room but below the 1.0 m2 default.
    modelspace.add_lwpolyline(
        [(0, 0), (0.8, 0), (0.8, 0.8), (0, 0.8)], close=True, dxfattribs={"layer": "A-WALL"}
    )
    path = tmp_path / "shaft.dxf"
    drawing.saveas(path)

    session = RoomReviewSession.from_path(path)
    assert session.usable_rooms == []
    assert session.rows()[0].status is ReviewStatus.BLOCKED

    session.set_min_area_m2(0.5)

    # The shaft has no text label, so it still needs an explicit decision.
    assert session.rows()[0].status is ReviewStatus.NEEDS_REVIEW
    session.accept_all_reviewable()

    assert len(session.usable_rooms) == 1
    assert round(session.usable_rooms[0].area, 2) == 0.64


# ---------------------------------------------------------------------------
# Geometry-keyed decisions survive re-detection
# ---------------------------------------------------------------------------


def test_decisions_are_keyed_by_geometry_not_by_room_number(tmp_path):
    session = RoomReviewSession.from_path(_plan(tmp_path / "plan.dxf"))
    office = next(row for row in session.rows() if row.layer == "A-WALL")
    session.reject(office.room_id)
    key = office.room_key

    # Re-detection renumbers nothing here, but the stored key must be geometry.
    session.refresh()

    assert key == room_key(session.rooms[0]) or key == room_key(session.rooms[1])
    assert session.room_decisions == {key: ReviewAction.REJECT}
    assert session.usable_rooms == []


def test_decision_survives_a_renumbering_change(tmp_path):
    session = RoomReviewSession.from_path(_plan(tmp_path / "plan.dxf"))
    office = next(row for row in session.rows() if row.layer == "A-WALL")
    session.reject(office.room_id)
    key = office.room_key

    # Lowering the minimum area keeps both polygons; ids stay, but the point is
    # that the decision is looked up by geometry, so it still applies.
    session.set_min_area_m2(0.4)

    rejected = [row for row in session.rows() if row.room_key == key]
    assert rejected and rejected[0].status is ReviewStatus.REJECTED


# ---------------------------------------------------------------------------
# Serialisation (the Project Database link point)
# ---------------------------------------------------------------------------


def test_review_state_round_trips_through_json(tmp_path):
    session = RoomReviewSession.from_path(_plan(tmp_path / "plan.dxf", units=0))
    office = next(row for row in session.rows() if row.layer == "A-WALL")
    session.set_units("meters")
    session.set_layer_class("A-FURN", LayerClass.OTHER)
    session.reject(office.room_id)

    payload = json.loads(json.dumps(session.to_dict()))

    assert payload["units_override"] == "meters"
    assert payload["layer_overrides"] == {"A-FURN": "OTHER"}
    assert list(payload["room_decisions"].values()) == ["REJECT"]

    restored = RoomReviewSession.from_path(_plan(tmp_path / "restored.dxf", units=0))
    restored.apply_dict(payload)

    assert restored.units_verified is True
    assert restored.layer_overrides == {"A-FURN": LayerClass.OTHER}
    assert restored.room_decisions == session.room_decisions
    assert [row.status for row in restored.rows()] == [row.status for row in session.rows()]


def test_reset_forgets_every_reviewer_input(tmp_path):
    session = RoomReviewSession.from_path(_plan(tmp_path / "plan.dxf", units=0))
    session.set_units("meters")
    session.set_layer_class("A-FURN", LayerClass.OTHER)
    session.set_min_area_m2(0.2)

    session.reset()

    assert session.layer_overrides == {}
    assert session.units_override is None
    assert session.room_decisions == {}
    assert session.min_area_m2 == 1.0
    assert session.units_verified is False


def test_rows_are_json_safe(tmp_path):
    session = RoomReviewSession.from_path(_plan(tmp_path / "plan.dxf"))

    rows = [row.as_dict() for row in session.rows()]

    json.dumps(rows)  # must not raise
    assert {row["status"] for row in rows} == {"AUTO_ACCEPTED", "BLOCKED"}
