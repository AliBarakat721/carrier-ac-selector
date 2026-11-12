"""Tests for the Phase 3 room-candidate review rules.

The governing defect these tests lock down:

    A furniture circle (0.5 m²) was reported as a room because every closed
    curve was treated as a room boundary.

The fix is not "ignore small areas".  It is a layered decision:

1. layer class (furniture / door / window / column / dimension / text -> never
   a room, whatever the geometry),
2. geometry validity,
3. area plausibility against an explicit, user-controlled threshold,
4. drawing-unit verification,
5. label presence,
6. explicit review status so nothing is silently promoted.
"""

from __future__ import annotations

import math

import ezdxf
import pytest

from cad.layer_analyzer import (
    CandidateStatus,
    LayerClass,
    evaluate_room_candidate,
)
from cad.space_detector import detect_spaces_with_review


# ---------------------------------------------------------------------------
# Rule-level tests (no CAD file needed)
# ---------------------------------------------------------------------------


def test_furniture_polygon_is_rejected_even_when_large_and_labelled():
    decision = evaluate_room_candidate(
        layer_class=LayerClass.FURNITURE,
        source="closed_polyline",
        area_m2=25.0,
        has_label=True,
        units_verified=True,
    )

    assert decision.status is CandidateStatus.REJECTED
    assert decision.blocking is True
    assert decision.reasons == ("LAYER_CLASS_NOT_A_ROOM:FURNITURE",)


@pytest.mark.parametrize(
    "layer_class",
    [
        LayerClass.DOORS,
        LayerClass.WINDOWS,
        LayerClass.COLUMNS,
        LayerClass.DIMENSIONS,
        LayerClass.TEXT_LABELS,
    ],
)
def test_non_room_layer_classes_are_always_rejected(layer_class):
    decision = evaluate_room_candidate(
        layer_class=layer_class,
        source="closed_polyline",
        area_m2=12.0,
        has_label=True,
        units_verified=True,
    )

    assert decision.status is CandidateStatus.REJECTED
    assert decision.blocking is True


def test_area_below_minimum_is_rejected_with_the_measured_area():
    decision = evaluate_room_candidate(
        layer_class=LayerClass.WALLS,
        source="circle",
        area_m2=0.5,
        has_label=False,
        units_verified=True,
        min_area_m2=1.0,
    )

    assert decision.status is CandidateStatus.REJECTED
    assert decision.reasons == ("AREA_BELOW_MINIMUM:0.500<1.000",)


def test_small_but_real_room_can_be_accepted_by_lowering_the_threshold():
    decision = evaluate_room_candidate(
        layer_class=LayerClass.WALLS,
        source="closed_polyline",
        area_m2=0.8,
        has_label=True,
        units_verified=True,
        min_area_m2=0.5,
    )

    assert decision.status is CandidateStatus.CONFIRMED


def test_invalid_geometry_is_rejected_before_any_soft_reason():
    decision = evaluate_room_candidate(
        layer_class=LayerClass.WALLS,
        source="closed_polyline",
        area_m2=20.0,
        has_label=False,
        units_verified=False,
        geometry_valid=False,
    )

    assert decision.status is CandidateStatus.REJECTED
    assert decision.reasons == ("INVALID_GEOMETRY",)


def test_unverified_units_block_confirmation():
    decision = evaluate_room_candidate(
        layer_class=LayerClass.WALLS,
        source="closed_polyline",
        area_m2=20.0,
        has_label=True,
        units_verified=False,
    )

    assert decision.status is CandidateStatus.NEEDS_REVIEW
    assert "DRAWING_UNITS_UNVERIFIED" in decision.reasons
    assert decision.usable_for_engineering is False


def test_missing_label_requires_review():
    decision = evaluate_room_candidate(
        layer_class=LayerClass.WALLS,
        source="closed_polyline",
        area_m2=20.0,
        has_label=False,
        units_verified=True,
    )

    assert decision.status is CandidateStatus.NEEDS_REVIEW
    assert decision.reasons == ("NO_ROOM_LABEL",)


def test_unknown_layer_class_requires_review_not_rejection():
    decision = evaluate_room_candidate(
        layer_class=LayerClass.OTHER,
        source="closed_polyline",
        area_m2=20.0,
        has_label=True,
        units_verified=True,
    )

    assert decision.status is CandidateStatus.NEEDS_REVIEW
    assert "LAYER_CLASS_UNKNOWN" in decision.reasons


def test_ambiguous_and_unconfirmed_layer_flags_are_reported():
    decision = evaluate_room_candidate(
        layer_class=LayerClass.WALLS,
        source="closed_polyline",
        area_m2=20.0,
        has_label=True,
        units_verified=True,
        ambiguous=True,
        unconfirmed=True,
    )

    assert decision.status is CandidateStatus.NEEDS_REVIEW
    assert set(decision.reasons) == {"LAYER_CLASS_AMBIGUOUS", "LAYER_CLASS_UNCONFIRMED"}


def test_circular_closed_curve_is_flagged_unless_explicitly_allowed():
    blocked = evaluate_room_candidate(
        layer_class=LayerClass.WALLS,
        source="circle",
        area_m2=12.0,
        has_label=True,
        units_verified=True,
    )
    allowed = evaluate_room_candidate(
        layer_class=LayerClass.WALLS,
        source="circle",
        area_m2=12.0,
        has_label=True,
        units_verified=True,
        allow_circular_curves=True,
    )

    assert "CIRCULAR_CLOSED_CURVE_SUSPECT" in blocked.reasons
    assert allowed.status is CandidateStatus.CONFIRMED


def test_clean_candidate_is_confirmed_and_usable():
    decision = evaluate_room_candidate(
        layer_class=LayerClass.ROOM_BOUNDARIES,
        source="closed_polyline",
        area_m2=20.0,
        has_label=True,
        units_verified=True,
    )

    assert decision.status is CandidateStatus.CONFIRMED
    assert decision.reasons == ()
    assert decision.usable_for_engineering is True


def test_oversized_polygon_is_flagged_not_rejected():
    decision = evaluate_room_candidate(
        layer_class=LayerClass.WALLS,
        source="closed_polyline",
        area_m2=5000.0,
        has_label=True,
        units_verified=True,
        max_area_m2=1000.0,
    )

    assert decision.status is CandidateStatus.NEEDS_REVIEW
    assert decision.reasons == ("AREA_ABOVE_MAXIMUM:5000.000>1000.000",)


# ---------------------------------------------------------------------------
# End-to-end detection tests on real DXF files
# ---------------------------------------------------------------------------


def _drawing_with_furniture_circle_and_room(path):
    """One real room (5x4 m on A-WALL) plus a 0.5 m² furniture circle."""

    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6  # metres
    for layer in ("A-WALL", "A-FURN", "A-ANNO-TEXT"):
        drawing.layers.new(layer)
    modelspace = drawing.modelspace()
    modelspace.add_lwpolyline(
        [(0, 0), (5, 0), (5, 4), (0, 4)],
        close=True,
        dxfattribs={"layer": "A-WALL"},
    )
    radius = math.sqrt(0.5 / math.pi)
    modelspace.add_circle(
        (10, 2), radius=radius, dxfattribs={"layer": "A-FURN"}
    )
    modelspace.add_text(
        "Office", height=0.2, dxfattribs={"layer": "A-ANNO-TEXT"}
    ).set_placement((2.5, 2.0))
    drawing.saveas(path)
    return path


def test_furniture_circle_is_rejected_and_real_room_is_confirmed(tmp_path):
    source = _drawing_with_furniture_circle_and_room(tmp_path / "plan.dxf")

    report = detect_spaces_with_review(source)

    assert report.summary()["total"] == 2
    assert len(report.rejected) == 1
    rejected = report.rejected[0]
    assert rejected.layer == "A-FURN"
    assert rejected.layer_class == "FURNITURE"
    assert rejected.candidate_status == "REJECTED"
    assert rejected.candidate_reasons[0].startswith("LAYER_CLASS_NOT_A_ROOM")

    assert len(report.accepted) == 1
    accepted = report.accepted[0]
    assert accepted.layer_class == "WALLS"
    assert accepted.label == "Office"
    assert round(accepted.area, 3) == 20.0
    assert accepted.is_usable_room is True


def test_report_never_hides_a_rejected_polygon(tmp_path):
    source = _drawing_with_furniture_circle_and_room(tmp_path / "plan.dxf")

    report = detect_spaces_with_review(source)
    rows = report.reasons_table()

    assert len(rows) == 2
    furniture_row = next(row for row in rows if row["layer"] == "A-FURN")
    assert furniture_row["status"] == "REJECTED"
    assert furniture_row["reasons"]
    assert furniture_row["area_m2"] > 0


def test_missing_insunits_marks_every_candidate_for_review(tmp_path):
    source = tmp_path / "unitless.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 0
    drawing.layers.new("A-WALL")
    drawing.modelspace().add_lwpolyline(
        [(0, 0), (5, 0), (5, 4), (0, 4)],
        close=True,
        dxfattribs={"layer": "A-WALL"},
    )
    drawing.saveas(source)

    report = detect_spaces_with_review(source)

    assert report.units_status == "UNVERIFIED"
    assert report.rooms
    assert all(room.candidate_status == "NEEDS_REVIEW" for room in report.rooms)
    assert all(
        "DRAWING_UNITS_UNVERIFIED" in room.candidate_reasons for room in report.rooms
    )
    assert report.accepted == []


def test_no_identifiable_wall_layer_skips_wall_polygonization(tmp_path):
    source = tmp_path / "unclassified.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    drawing.layers.new("Layer1")
    modelspace = drawing.modelspace()
    for start, end in [
        ((0, 0), (5, 0)),
        ((5, 0), (5, 4)),
        ((5, 4), (0, 4)),
        ((0, 4), (0, 0)),
    ]:
        modelspace.add_line(start, end, dxfattribs={"layer": "Layer1"})
    drawing.saveas(source)

    report = detect_spaces_with_review(source)

    assert report.rooms == []
    assert any(
        item["type"] == "no_wall_layer_identified" for item in report.diagnostics
    )


def test_user_override_promotes_an_unclassified_layer_to_walls(tmp_path):
    source = tmp_path / "unclassified.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    drawing.layers.new("Layer1")
    modelspace = drawing.modelspace()
    for start, end in [
        ((0, 0), (5, 0)),
        ((5, 0), (5, 4)),
        ((5, 4), (0, 4)),
        ((0, 4), (0, 0)),
    ]:
        modelspace.add_line(start, end, dxfattribs={"layer": "Layer1"})
    drawing.saveas(source)

    report = detect_spaces_with_review(
        source, layer_overrides={"Layer1": LayerClass.WALLS}
    )

    assert len(report.rooms) == 1
    assert report.rooms[0].layer_class == "WALLS"
    assert round(report.rooms[0].area, 3) == 20.0
    # Still needs a label and a human decision, but the geometry is now used.
    assert report.rooms[0].candidate_status == "NEEDS_REVIEW"
    assert report.rooms[0].candidate_reasons == ("NO_ROOM_LABEL",)


def test_require_label_blocks_candidates_without_a_label(tmp_path):
    source = tmp_path / "plan.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    drawing.layers.new("A-WALL")
    drawing.modelspace().add_lwpolyline(
        [(0, 0), (5, 0), (5, 4), (0, 4)],
        close=True,
        dxfattribs={"layer": "A-WALL"},
    )
    drawing.saveas(source)

    report = detect_spaces_with_review(source, require_label=True)

    assert report.accepted == []
    assert "LABEL_REQUIRED" in report.rooms[0].candidate_reasons


def test_duplicate_room_from_polyline_and_wall_lines_is_still_deduplicated(tmp_path):
    source = tmp_path / "duplicate.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    drawing.layers.new("A-WALL")
    modelspace = drawing.modelspace()
    modelspace.add_lwpolyline(
        [(0, 0), (5, 0), (5, 4), (0, 4)], close=True, dxfattribs={"layer": "A-WALL"}
    )
    for start, end in [
        ((0, 0), (5, 0)),
        ((5, 0), (5, 4)),
        ((5, 4), (0, 4)),
        ((0, 4), (0, 0)),
    ]:
        modelspace.add_line(start, end, dxfattribs={"layer": "A-WALL"})
    drawing.saveas(source)

    report = detect_spaces_with_review(source)

    assert len(report.rooms) == 1
    assert round(report.rooms[0].area, 3) == 20.0
