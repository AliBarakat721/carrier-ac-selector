"""Phase 0 — baseline contracts and regression locks.

Each test in this file corresponds to a defect recorded in the review.  Two
kinds of test live here and they must stay distinguishable:

``test_rN_*_current``
    Characterisation tests.  They assert what the code does **today**, so any
    accidental change is caught.  A passing characterisation test does not mean
    the behaviour is correct.

``test_rN_*`` (no ``_current`` suffix)
    The desired behaviour.  Where the defect is not fixed yet the test is
    marked ``xfail(strict=True)``: it fails today, and the moment somebody fixes
    the defect it starts passing, which turns the suite red on purpose so the
    marker is removed in the same commit as the fix.

Mapping
-------
R1 room matching by drawing order        -> Phase 4 (fixed; positional pairing
                                             removed, names are the only evidence)
R2 furniture closed curve treated as room -> Phase 3 (fixed for the review API,
                                             characterisation kept for the legacy
                                             API)
R3 negative / non-finite electrical input  -> Phase 11 (xfail)
R4 illogical ROI inputs                    -> Phase 13 (xfail)
R5 block geometry without model dimensions -> Phase 8 (xfail)
R6 multi-unit placement overlap            -> Phase 9 (xfail)
R7 export over the source drawing          -> Phase 0 (fixed)
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import ezdxf
import pytest
from shapely.geometry import Polygon

from cad.dxf_exporter import export_hvac_dxf
from cad.space_detector import detect_spaces, detect_spaces_with_review
from cad.workflow import RoomAssignment, build_indoor_placements, suggest_room_assignments
from electrical_engine import calculate_unit_electrical
from roi_tab import compute_roi


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _room(room_id: str, label: str | None = None, x: float = 0.0) -> SimpleNamespace:
    return SimpleNamespace(
        room_id=room_id,
        label=label,
        polygon=Polygon([(x, 0), (x + 5, 0), (x + 5, 4), (x, 4)]),
        area=20.0,
        perimeter=18.0,
        raw_area=20.0,
        raw_perimeter=18.0,
        source="closed_polyline",
        status="NEEDS_REVIEW",
        unit_status="VERIFIED",
        drawing_units="meters",
    )


def _selected(model: str = "42QSS", units: int = 1, load_btu: float = 36_000) -> dict:
    return {
        "row": {
            "Indoor Unit Model": model,
            "Cooling Capacity (kW)": 3.52,
            "High CFM (numeric)": 400,
        },
        "units": units,
        "load_btu": load_btu,
        "effective_btu": 12_000,
    }


def _minimal_dxf(path):
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    drawing.layers.new("A-WALL")
    drawing.modelspace().add_lwpolyline(
        [(0, 0), (5, 0), (5, 4), (0, 4)], close=True, dxfattribs={"layer": "A-WALL"}
    )
    drawing.saveas(path)
    return path


# ---------------------------------------------------------------------------
# R1 — room matching must never be inferred from drawing order
# ---------------------------------------------------------------------------


def test_r1_legacy_api_no_longer_pairs_remaining_rooms_by_order():
    """Regression: equal counts with no labels are no longer paired positionally."""

    rooms = [_room("ROOM-001"), _room("ROOM-002", x=10)]
    systems = [SimpleNamespace(name="G01"), SimpleNamespace(name="G02")]

    assert suggest_room_assignments(rooms, systems) == {}


def test_r1_unlabelled_rooms_are_not_paired_by_order():
    rooms = [_room("ROOM-001"), _room("ROOM-002", x=10)]
    systems = [SimpleNamespace(name="G01"), SimpleNamespace(name="G02")]

    assignments = suggest_room_assignments(rooms, systems)

    assert assignments == {}, (
        "A room with no label and no area/name evidence must not inherit a HAP "
        "system because it happened to be second in the drawing."
    )


# ---------------------------------------------------------------------------
# R2 — a furniture closed curve must not become a room
# ---------------------------------------------------------------------------


def test_r2_current_legacy_detector_accepts_any_closed_curve(tmp_path):
    """Characterisation of the defect: the legacy API has no layer gating."""

    source = tmp_path / "furniture.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    drawing.layers.new("A-FURN")
    drawing.modelspace().add_circle(
        (10, 2), radius=math.sqrt(0.5 / math.pi), dxfattribs={"layer": "A-FURN"}
    )
    drawing.saveas(source)

    rooms = detect_spaces(source, include_wall_lines=False)

    assert len(rooms) == 1
    assert round(rooms[0].area, 2) == 0.5


def test_r2_review_api_rejects_the_furniture_curve(tmp_path):
    source = tmp_path / "furniture.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    drawing.layers.new("A-FURN")
    drawing.modelspace().add_circle(
        (10, 2), radius=math.sqrt(0.5 / math.pi), dxfattribs={"layer": "A-FURN"}
    )
    drawing.saveas(source)

    report = detect_spaces_with_review(source)

    assert report.accepted == []
    assert len(report.rejected) == 1
    assert report.rejected[0].candidate_reasons[0].startswith("LAYER_CLASS_NOT_A_ROOM")


# ---------------------------------------------------------------------------
# R3 — negative / non-finite electrical inputs
# ---------------------------------------------------------------------------


def test_r3_current_non_finite_inputs_are_rejected():
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            calculate_unit_electrical(bad, 3.5, 3.2, "220-240/1/50")


def test_r3_current_negative_capacity_silently_becomes_a_default():
    """Characterisation: a negative load falls through to the 1.5 kW default."""

    result = calculate_unit_electrical(-36_000, -10.0, 3.2, "220-240/1/50")

    assert result["fla"] > 0


@pytest.mark.xfail(
    strict=True,
    reason="Phase 11: a negative cooling load must raise, not fall back to a default.",
)
def test_r3_negative_capacity_is_rejected():
    with pytest.raises(ValueError):
        calculate_unit_electrical(-36_000, -10.0, 3.2, "220-240/1/50")


# ---------------------------------------------------------------------------
# R4 — illogical ROI inputs
# ---------------------------------------------------------------------------


def test_r4_current_roi_accepts_impossible_savings():
    """Characterisation: a 150% saving produces a physically impossible result."""

    result = compute_roi(btu=36_000, saving_pct=150.0)

    assert result["inverter_kw"] < 0 or result["annual_kwh_saving"] > result["fixed_annual_kwh"]


@pytest.mark.xfail(
    strict=True,
    reason="Phase 13: saving_pct outside 0..100 must be rejected at the boundary.",
)
def test_r4_roi_rejects_saving_above_one_hundred_percent():
    with pytest.raises(ValueError):
        compute_roi(btu=36_000, saving_pct=150.0)


# ---------------------------------------------------------------------------
# R5 — CAD block geometry is not derived from the model
# ---------------------------------------------------------------------------


def test_r5_current_block_geometry_is_a_generic_rectangle(tmp_path):
    """Characterisation: the block symbol is 1.0 x 0.5 drawing units, always."""

    from cad.block_manager import create_block_definition

    drawing = ezdxf.new("R2018")
    block = create_block_definition(drawing, "indoor_unit")
    polylines = list(block.query("LWPOLYLINE"))
    points = [tuple(point[:2]) for polyline in polylines for point in polyline.get_points()]

    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    assert round(max(xs) - min(xs), 3) == 1.0
    assert round(max(ys) - min(ys), 3) == 0.5


@pytest.mark.xfail(
    strict=True,
    reason="Phase 8: block geometry must come from CADBlockMetadata, not a fixed rectangle.",
)
def test_r5_block_geometry_matches_the_model_dimensions(tmp_path):
    from cad.block_manager import create_block_definition

    drawing = ezdxf.new("R2018")
    block = create_block_definition(
        drawing,
        "indoor_unit",
        metadata={"width_mm": 1050, "height_mm": 350, "depth_mm": 250},
    )
    polylines = list(block.query("LWPOLYLINE"))
    points = [tuple(point[:2]) for polyline in polylines for point in polyline.get_points()]
    xs = [point[0] for point in points]
    assert round(max(xs) - min(xs), 3) == pytest.approx(1.05, abs=1e-3)


# ---------------------------------------------------------------------------
# R6 — multi-unit placement overlap
# ---------------------------------------------------------------------------


def test_r6_current_multi_unit_positions_are_closer_than_the_unit_width():
    """Characterisation: the spread radius is a fixed 0.25 drawing units."""

    records, _, warnings = build_indoor_placements(
        [_room("ROOM-001", "Office")],
        [RoomAssignment("ROOM-001", "G01")],
        {"G01": _selected(units=2)},
    )

    assert not warnings
    distance = math.dist(records[0]["POSITION"], records[1]["POSITION"])
    assert distance < 1.05, "two 1.05 m wide units are only 0.5 m apart"


@pytest.mark.xfail(
    strict=True,
    reason="Phase 9: placement must guarantee non-overlapping footprints inside the room.",
)
def test_r6_multi_unit_placements_do_not_overlap():
    records, _, _ = build_indoor_placements(
        [_room("ROOM-001", "Office")],
        [RoomAssignment("ROOM-001", "G01")],
        {"G01": _selected(units=2)},
    )

    distance = math.dist(records[0]["POSITION"], records[1]["POSITION"])
    assert distance >= 1.05


# ---------------------------------------------------------------------------
# R7 — export must never overwrite the source drawing
# ---------------------------------------------------------------------------


def test_r7_export_over_the_source_path_is_refused(tmp_path):
    source = _minimal_dxf(tmp_path / "plan.dxf")
    original = source.read_bytes()

    with pytest.raises(ValueError, match="Refusing to export over the source"):
        export_hvac_dxf(
            source,
            source,
            indoor_units=[],
            outdoor_units=[],
            relationships=[],
        )

    assert source.read_bytes() == original


def test_r7_export_to_a_new_path_leaves_the_source_untouched(tmp_path):
    source = _minimal_dxf(tmp_path / "plan.dxf")
    original = source.read_bytes()
    destination = tmp_path / "plan_HVAC.dxf"

    result = export_hvac_dxf(
        source,
        destination,
        indoor_units=[],
        outdoor_units=[],
        relationships=[],
    )

    assert result.output_path == destination
    assert destination.is_file()
    assert source.read_bytes() == original
