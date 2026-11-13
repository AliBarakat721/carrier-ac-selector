from pathlib import Path
import math

import ezdxf
import pytest

import cad.space_detector as space_detector
from cad.dxf_reader import read_dxf
from cad.space_detector import (
    _snap_line_endpoints,
    detect_spaces,
    detect_spaces_with_review,
    fuse_space_candidates,
)


def make_sample_dxf(path: Path) -> None:
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6  # meters
    drawing.layers.new("ROOMS")
    drawing.layers.new("WALLS")
    modelspace = drawing.modelspace()
    modelspace.add_lwpolyline(
        [(0, 0), (5, 0), (5, 4), (0, 4)],
        close=True,
        dxfattribs={"layer": "ROOMS"},
    )
    modelspace.add_line((10, 0), (15, 0), dxfattribs={"layer": "WALLS"})
    modelspace.add_line((15, 0), (15, 4), dxfattribs={"layer": "WALLS"})
    modelspace.add_line((15, 4), (10, 4), dxfattribs={"layer": "WALLS"})
    modelspace.add_line((10, 4), (10, 0), dxfattribs={"layer": "WALLS"})
    drawing.saveas(path)


def test_read_dxf_extracts_units_layers_and_entities(tmp_path):
    source = tmp_path / "sample.dxf"
    make_sample_dxf(source)

    document = read_dxf(source)

    assert document.units == "meters"
    assert document.layers == ["0", "Defpoints", "ROOMS", "WALLS"]
    assert document.entity_count == 5
    assert len(document.entities_of_type("LWPOLYLINE")) == 1
    assert len(document.entities_of_type("LINE")) == 4


def test_solid_quadrilateral_is_read_in_polygon_order_and_detected(tmp_path):
    source = tmp_path / "solid-quad.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    drawing.modelspace().add_solid(
        # ezdxf preserves the DXF SOLID convention where the last two
        # vertices are stored in reverse order; wcs_vertices() must restore
        # the polygon order for the detector.
        [(0, 0), (5, 0), (0, 4), (5, 4)],
        dxfattribs={"layer": "ROOMS"},
    )
    drawing.saveas(source)

    document = read_dxf(source)
    solid = document.entities_of_type("SOLID")[0]
    rooms = detect_spaces(document, include_wall_lines=False)

    assert len(solid.data["points"]) == 4
    assert solid.data["closed"] is True
    assert len(rooms) == 1
    assert rooms[0].source == "solid_polygon"
    assert rooms[0].area == pytest.approx(20.0)
    assert rooms[0].boundary_handles == (solid.handle,)


def test_solid_triangle_is_detected_without_duplicate_vertex(tmp_path):
    source = tmp_path / "solid-triangle.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    drawing.modelspace().add_solid(
        [(0, 0), (4, 0), (0, 3)],
        dxfattribs={"layer": "ROOMS"},
    )
    drawing.saveas(source)

    document = read_dxf(source)
    solid = document.entities_of_type("SOLID")[0]
    rooms = detect_spaces(document, include_wall_lines=False)

    assert len(solid.data["points"]) == 3
    assert len(rooms) == 1
    assert rooms[0].source == "solid_polygon"
    assert rooms[0].area == pytest.approx(6.0)


def test_solid_inside_block_is_transformed_and_keeps_insert_handle(tmp_path):
    source = tmp_path / "inserted-solid.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    block = drawing.blocks.new("ROOM_SOLID")
    block.add_solid([(0, 0), (5, 0), (0, 4), (5, 4)], dxfattribs={"layer": "0"})
    insert = drawing.modelspace().add_blockref(
        "ROOM_SOLID",
        (10, 20),
        dxfattribs={"layer": "ROOMS", "xscale": 2.0},
    )
    drawing.saveas(source)

    document = read_dxf(source)
    solids = document.entities_of_type("SOLID")
    rooms = detect_spaces(document, include_wall_lines=False)

    assert len(solids) == 1
    assert solids[0].layer == "ROOMS"
    assert solids[0].source_insert_handles == (insert.dxf.handle,)
    assert rooms[0].area == pytest.approx(40.0)
    assert insert.dxf.handle in rooms[0].boundary_handles


def test_solid_on_furniture_layer_is_rejected_by_review_rules(tmp_path):
    source = tmp_path / "furniture-solid.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    drawing.layers.new("A-FURN")
    drawing.modelspace().add_solid(
        [(0, 0), (2, 0), (2, 2), (0, 2)],
        dxfattribs={"layer": "A-FURN"},
    )
    drawing.saveas(source)

    report = detect_spaces_with_review(source, include_wall_lines=False)

    assert len(report.rooms) == 1
    assert report.rooms[0].source == "solid_polygon"
    assert report.rooms[0].candidate_status == "REJECTED"
    assert report.rooms[0].candidate_reasons == ("LAYER_CLASS_NOT_A_ROOM:FURNITURE",)


def test_hatch_polyline_boundary_is_read_and_detected(tmp_path):
    source = tmp_path / "hatch-polyline.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    hatch = drawing.modelspace().add_hatch(dxfattribs={"layer": "ROOMS"})
    hatch.set_solid_fill()
    hatch.paths.add_polyline_path([(0, 0), (5, 0), (5, 4), (0, 4)], is_closed=True)
    drawing.saveas(source)

    document = read_dxf(source)
    rooms = detect_spaces(document, include_wall_lines=False)

    assert document.entities_of_type("HATCH")[0].data["solid_fill"] is True
    assert len(document.entities_of_type("HATCH")[0].data["paths"]) == 1
    assert len(rooms) == 1
    assert rooms[0].source == "hatch_polygon"
    assert rooms[0].area == pytest.approx(20.0)


def test_hatch_edge_path_with_arc_is_polygonized(tmp_path):
    source = tmp_path / "hatch-edge-circle.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    hatch = drawing.modelspace().add_hatch(dxfattribs={"layer": "ROOMS"})
    hatch.set_solid_fill()
    edge_path = hatch.paths.add_edge_path()
    edge_path.add_arc((0, 0), 5, 0, 360, ccw=True)
    drawing.saveas(source)

    document = read_dxf(source)
    rooms = detect_spaces(document, include_wall_lines=False)

    assert len(document.entities_of_type("HATCH")[0].data["paths"][0]["points"]) > 8
    assert len(rooms) == 1
    assert rooms[0].source == "hatch_polygon"
    assert rooms[0].area == pytest.approx(math.pi * 25.0, rel=0.01)


def test_hatch_inner_boundary_is_preserved_as_a_hole(tmp_path):
    source = tmp_path / "hatch-hole.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    hatch = drawing.modelspace().add_hatch(dxfattribs={"layer": "ROOMS"})
    hatch.set_solid_fill()
    hatch.paths.add_polyline_path([(0, 0), (5, 0), (5, 4), (0, 4)], is_closed=True)
    hatch.paths.add_polyline_path([(1, 1), (2, 1), (2, 3), (1, 3)], is_closed=True)
    drawing.saveas(source)

    rooms = detect_spaces(source, include_wall_lines=False)

    assert len(rooms) == 1
    assert rooms[0].source == "hatch_polygon"
    assert rooms[0].area == pytest.approx(18.0)
    assert len(rooms[0].polygon.interiors) == 1


def test_matching_hatch_and_solid_are_deduplicated_into_one_space(tmp_path):
    source = tmp_path / "hatch-solid-duplicate.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    modelspace = drawing.modelspace()
    modelspace.add_solid(
        [(0, 0), (5, 0), (0, 4), (5, 4)],
        dxfattribs={"layer": "ROOMS"},
    )
    hatch = modelspace.add_hatch(dxfattribs={"layer": "ROOMS"})
    hatch.set_solid_fill()
    hatch.paths.add_polyline_path([(0, 0), (5, 0), (5, 4), (0, 4)], is_closed=True)
    drawing.saveas(source)

    rooms = detect_spaces(source, include_wall_lines=False)

    assert len(rooms) == 1
    assert rooms[0].source == "solid_polygon"
    assert rooms[0].area == pytest.approx(20.0)
    assert len(rooms[0].boundary_handles) == 2
    assert set(rooms[0].sources) == {"solid_polygon", "hatch_polygon"}
    assert rooms[0].confidence in {"MEDIUM", "HIGH"}


def test_wall_boundary_and_inner_hatch_fuse_to_inner_engineering_area(tmp_path):
    source = tmp_path / "wall-and-inner-hatch.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    modelspace = drawing.modelspace()
    for start, end in [
        ((0, 0), (6, 0)),
        ((6, 0), (6, 5)),
        ((6, 5), (0, 5)),
        ((0, 5), (0, 0)),
    ]:
        modelspace.add_line(start, end, dxfattribs={"layer": "WALLS"})
    hatch = modelspace.add_hatch(dxfattribs={"layer": "ROOMS"})
    hatch.set_solid_fill()
    hatch.paths.add_polyline_path([(0.5, 0.5), (5.5, 0.5), (5.5, 4.5), (0.5, 4.5)], is_closed=True)
    drawing.saveas(source)

    rooms = detect_spaces(source, wall_layers={"WALLS"})

    assert len(rooms) == 1
    assert rooms[0].source == "hatch_polygon"
    assert set(rooms[0].sources) == {"wall_lines", "hatch_polygon"}
    assert rooms[0].area == pytest.approx(20.0)


def test_adjacent_closed_spaces_are_not_fused_by_proximity():
    left = space_detector.RoomSpace(
        room_id="LEFT", polygon=space_detector.Polygon([(0, 0), (4, 0), (4, 4), (0, 4)]),
        area=16.0, perimeter=16.0, source="hatch_polygon", layer="ROOMS",
    )
    right = space_detector.RoomSpace(
        room_id="RIGHT", polygon=space_detector.Polygon([(4, 0), (8, 0), (8, 4), (4, 4)]),
        area=16.0, perimeter=16.0, source="solid_polygon", layer="ROOMS",
    )

    fused = fuse_space_candidates([left, right])

    assert len(fused) == 2


def test_block_room_geometry_is_transformed_and_inherits_insert_layer(tmp_path):
    source = tmp_path / "inserted-room.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    block = drawing.blocks.new("ROOM_GEOMETRY")
    block.add_lwpolyline(
        [(0, 0), (5, 0), (5, 4), (0, 4)],
        close=True,
        dxfattribs={"layer": "0"},
    )
    insert = drawing.modelspace().add_blockref(
        "ROOM_GEOMETRY",
        (10, 20),
        dxfattribs={"layer": "WALLS", "xscale": 2.0},
    )
    drawing.saveas(source)

    document = read_dxf(source)
    expanded = document.entities_of_type("LWPOLYLINE")
    rooms = detect_spaces(document, wall_layers={"WALLS"})

    assert len(expanded) == 1
    assert expanded[0].layer == "WALLS"
    assert expanded[0].source_insert_handles == (insert.dxf.handle,)
    assert len(rooms) == 1
    assert rooms[0].area == pytest.approx(40.0)
    assert insert.dxf.handle in rooms[0].boundary_handles


def test_nested_block_room_geometry_is_expanded_recursively(tmp_path):
    source = tmp_path / "nested-room.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    geometry = drawing.blocks.new("ROOM_GEOMETRY")
    geometry.add_lwpolyline([(0, 0), (5, 0), (5, 4), (0, 4)], close=True)
    outer = drawing.blocks.new("LEVEL_PLAN")
    outer.add_blockref("ROOM_GEOMETRY", (2, 3))
    root_insert = drawing.modelspace().add_blockref(
        "LEVEL_PLAN", (100, 200), dxfattribs={"layer": "WALLS"}
    )
    drawing.saveas(source)

    document = read_dxf(source)
    rooms = detect_spaces(document, wall_layers={"WALLS"})

    assert len(document.entities_of_type("LWPOLYLINE")) == 1
    assert document.entities_of_type("LWPOLYLINE")[0].layer == "WALLS"
    assert len(rooms) == 1
    assert rooms[0].area == pytest.approx(20.0)
    assert root_insert.dxf.handle in rooms[0].boundary_handles


def test_room_is_reconstructed_from_connected_open_lwpolylines(tmp_path):
    source = tmp_path / "open-polylines.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    modelspace = drawing.modelspace()
    for start, end in [
        ((0, 0), (5, 0)),
        ((5, 0), (5, 4)),
        ((5, 4), (0, 4)),
        ((0, 4), (0, 0)),
    ]:
        modelspace.add_lwpolyline([start, end], dxfattribs={"layer": "WALLS"})
    drawing.saveas(source)

    rooms = detect_spaces(source, wall_layers={"WALLS"})

    assert len(rooms) == 1
    assert rooms[0].source == "wall_lines"
    assert rooms[0].area == pytest.approx(20.0)
    assert len(rooms[0].boundary_handles) == 4


def test_room_is_reconstructed_from_connected_open_legacy_polylines(tmp_path):
    source = tmp_path / "open-legacy-polylines.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    modelspace = drawing.modelspace()
    for start, end in [
        ((0, 0), (5, 0)),
        ((5, 0), (5, 4)),
        ((5, 4), (0, 4)),
        ((0, 4), (0, 0)),
    ]:
        modelspace.add_polyline2d([start, end], dxfattribs={"layer": "WALLS"})
    drawing.saveas(source)

    rooms = detect_spaces(source, wall_layers={"WALLS"})

    assert len(rooms) == 1
    assert rooms[0].source == "wall_lines"
    assert rooms[0].area == pytest.approx(20.0)
    assert len(rooms[0].boundary_handles) == 4


def test_detect_spaces_from_closed_polyline_and_wall_lines(tmp_path):
    source = tmp_path / "sample.dxf"
    make_sample_dxf(source)

    rooms = detect_spaces(source)

    assert len(rooms) == 2
    assert sorted(round(room.area, 3) for room in rooms) == [20.0, 20.0]
    assert {room.source for room in rooms} == {"closed_polyline", "wall_lines"}
    assert all(room.status == "NEEDS_REVIEW" for room in rooms)
    assert all(room.boundary_handles for room in rooms)


def test_duplicate_polyline_and_wall_boundary_are_deduplicated(tmp_path):
    source = tmp_path / "duplicate.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    modelspace = drawing.modelspace()
    modelspace.add_lwpolyline([(0, 0), (5, 0), (5, 4), (0, 4)], close=True)
    for start, end in [((0, 0), (5, 0)), ((5, 0), (5, 4)), ((5, 4), (0, 4)), ((0, 4), (0, 0))]:
        modelspace.add_line(start, end)
    drawing.saveas(source)

    rooms = detect_spaces(source)

    assert len(rooms) == 1
    assert rooms[0].source == "closed_polyline"
    assert len(rooms[0].boundary_handles) == 5


def test_millimeter_units_are_converted_to_square_meters(tmp_path):
    source = tmp_path / "millimeters.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 4
    drawing.modelspace().add_lwpolyline([(0, 0), (5000, 0), (5000, 4000), (0, 4000)], close=True)
    drawing.saveas(source)

    rooms = detect_spaces(source, include_wall_lines=False)

    assert len(rooms) == 1
    assert round(rooms[0].area, 3) == 20.0
    assert round(rooms[0].perimeter, 3) == 18.0


def test_decimeter_units_use_the_correct_metric_scale(tmp_path):
    source = tmp_path / "decimeters.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 14
    drawing.modelspace().add_lwpolyline([(0, 0), (50, 0), (50, 40), (0, 40)], close=True)
    drawing.saveas(source)

    rooms = detect_spaces(source, include_wall_lines=False)

    assert round(rooms[0].area, 3) == 20.0
    assert round(rooms[0].perimeter, 3) == 18.0


def test_unitless_geometry_is_marked_unknown(tmp_path):
    source = tmp_path / "unitless.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 0
    drawing.modelspace().add_lwpolyline([(0, 0), (5, 0), (5, 4), (0, 4)], close=True)
    drawing.saveas(source)

    rooms = detect_spaces(source, include_wall_lines=False)

    assert rooms[0].unit_status == "UNIT_UNKNOWN"


def test_bulge_is_preserved_and_curved_boundary_is_not_flattened(tmp_path):
    source = tmp_path / "bulge.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    drawing.modelspace().add_lwpolyline(
        [(0, 0, 0, 0, 1.0), (10, 0), (10, 10), (0, 10)], close=True
    )
    drawing.saveas(source)

    document = read_dxf(source)
    rooms = detect_spaces(document, include_wall_lines=False)

    assert document.entities_of_type("LWPOLYLINE")[0].data["bulges"][0] == 1.0
    assert len(rooms) == 1
    assert rooms[0].area != 100.0


def test_bulge_arc_area_and_perimeter_converge_to_reference(tmp_path):
    source = tmp_path / "arc-reference.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    drawing.modelspace().add_lwpolyline(
        [(0, 0, 0, 0, 1.0), (10, 0), (10, 10), (0, 10)], close=True
    )
    drawing.saveas(source)
    expected_area = 100.0 + math.pi * 25.0 / 2.0
    expected_perimeter = 30.0 + math.pi * 5.0

    coarse = detect_spaces(source, include_wall_lines=False, arc_angle_tolerance=math.radians(5))[0]
    precise = detect_spaces(source, include_wall_lines=False, arc_angle_tolerance=math.radians(0.1))[0]

    assert abs(precise.area - expected_area) < abs(coarse.area - expected_area)
    assert abs(precise.perimeter - expected_perimeter) < 0.001


def test_self_intersecting_polygon_is_marked_invalid(tmp_path):
    source = tmp_path / "bow-tie.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    drawing.modelspace().add_lwpolyline([(0, 0), (5, 5), (0, 5), (5, 0)], close=True)
    drawing.saveas(source)

    rooms = detect_spaces(source, include_wall_lines=False)

    assert len(rooms) == 1
    assert rooms[0].status == "INVALID_GEOMETRY"
    assert rooms[0].geometry_diagnostic


def test_gap_snapping_reports_moved_endpoints(tmp_path):
    source = tmp_path / "gap.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    modelspace = drawing.modelspace()
    modelspace.add_line((0, 0), (5, 0))
    modelspace.add_line((5.002, 0), (5, 4))
    modelspace.add_line((5, 4), (0, 4))
    modelspace.add_line((0, 4), (0, 0))
    drawing.saveas(source)
    diagnostics = []

    rooms = detect_spaces(source, gap_tolerance=0.005, diagnostics=diagnostics)

    assert rooms
    assert diagnostics
    snapped = [item for item in diagnostics if item["type"] == "snapped_endpoint"]
    assert snapped[0]["distance"] == pytest.approx(0.002)
    assert snapped[0]["snapped_to"] == (5.0, 0.0)
    assert snapped[0]["handle"]
    assert any(room.status == "SNAPPED" for room in rooms)
    assert any(room.geometry_diagnostic and snapped[0]["handle"] in room.geometry_diagnostic for room in rooms)


def test_unresolved_gaps_are_unique_and_include_gaps_larger_than_ten_times_tolerance():
    _, diagnostics = _snap_line_endpoints(
        [((0.0, 0.0), (1.0, 0.0), "A"), ((3.0, 0.0), (4.0, 0.0), "B")],
        0.1,
    )

    gaps = [item for item in diagnostics if item["type"] == "unresolved_gap"]
    pairs = {
        frozenset((item["endpoint"], item["other_endpoint"]))
        for item in gaps
    }
    assert len(pairs) == len(gaps)
    assert any(item["distance"] > 1.0 for item in gaps)


def test_circle_is_detected_as_a_room(tmp_path):
    source = tmp_path / "circle.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    drawing.modelspace().add_circle((0, 0), 5, dxfattribs={"layer": "WALLS"})
    drawing.saveas(source)

    rooms = detect_spaces(source, include_wall_lines=False)

    assert len(rooms) == 1
    assert rooms[0].source == "circle"
    assert rooms[0].area == pytest.approx(math.pi * 25, rel=1e-3)


def test_arc_segments_can_close_a_room(tmp_path):
    source = tmp_path / "arc-room.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    modelspace = drawing.modelspace()
    modelspace.add_arc((5, 0), 5, 0, 180, dxfattribs={"layer": "WALLS"})
    modelspace.add_line((0, 0), (10, 0), dxfattribs={"layer": "WALLS"})
    drawing.saveas(source)

    rooms = detect_spaces(source, wall_layers={"WALLS"})

    assert len(rooms) == 1
    assert rooms[0].area == pytest.approx(math.pi * 25 / 2, rel=2e-3)


def test_spline_is_read_and_polygonized_when_marked_closed(tmp_path):
    source = tmp_path / "spline.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    spline = drawing.modelspace().add_spline(
        fit_points=[(0, 0), (4, 0), (4, 4), (0, 4), (0, 0)],
        dxfattribs={"layer": "WALLS"},
    )
    spline.dxf.flags = 1
    drawing.saveas(source)

    document = read_dxf(source)
    rooms = detect_spaces(source, include_wall_lines=False)

    assert document.entities_of_type("SPLINE")[0].data["points"]
    assert len(rooms) == 1
    assert rooms[0].source == "spline"
    assert rooms[0].area > 0


def test_t_junctions_are_split_and_reported(tmp_path):
    source = tmp_path / "t-junction.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    modelspace = drawing.modelspace()
    for start, end in [
        ((0, 0), (10, 0)),
        ((10, 0), (10, 10)),
        ((10, 10), (0, 10)),
        ((0, 10), (0, 0)),
        ((5, 0), (5, 10)),
    ]:
        modelspace.add_line(start, end, dxfattribs={"layer": "WALLS"})
    drawing.saveas(source)
    diagnostics = []

    rooms = detect_spaces(source, wall_layers={"WALLS"}, diagnostics=diagnostics)

    assert len(rooms) == 2
    assert any(item["type"] == "t_junction" for item in diagnostics)


def test_wall_layer_filter_excludes_non_wall_lines(tmp_path):
    source = tmp_path / "wall-filter.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    modelspace = drawing.modelspace()
    for layer, offset in (("WALLS", 0), ("FURNITURE", 20)):
        for start, end in [
            ((offset, 0), (offset + 5, 0)),
            ((offset + 5, 0), (offset + 5, 4)),
            ((offset + 5, 4), (offset, 4)),
            ((offset, 4), (offset, 0)),
        ]:
            modelspace.add_line(start, end, dxfattribs={"layer": layer})
    drawing.saveas(source)

    rooms = detect_spaces(source, wall_layers={"WALLS"})

    assert len(rooms) == 1
    assert rooms[0].layer is None
    assert set(rooms[0].boundary_handles)


def test_room_text_is_assigned_to_containing_polygon(tmp_path):
    source = tmp_path / "room-label.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    modelspace = drawing.modelspace()
    for start, end in [
        ((0, 0), (5, 0)), ((5, 0), (5, 4)), ((5, 4), (0, 4)), ((0, 4), (0, 0))
    ]:
        modelspace.add_line(start, end, dxfattribs={"layer": "WALLS"})
    modelspace.add_text("Kitchen", dxfattribs={"insert": (2, 2), "layer": "ROOM-TEXT"})
    drawing.saveas(source)

    rooms = detect_spaces(source, wall_layers={"WALLS"}, label_layers={"ROOM-TEXT"})

    assert len(rooms) == 1
    assert rooms[0].label == "Kitchen"


def test_room_id_is_stable_when_entity_order_changes(tmp_path):
    paths = []
    boundaries = [
        [((0, 0), (5, 0)), ((5, 0), (5, 4)), ((5, 4), (0, 4)), ((0, 4), (0, 0))],
        [((10, 0), (15, 0)), ((15, 0), (15, 4)), ((15, 4), (10, 4)), ((10, 4), (10, 0))],
    ]
    for index, order in enumerate((boundaries, list(reversed(boundaries)))):
        path = tmp_path / f"stable-{index}.dxf"
        drawing = ezdxf.new("R2018")
        drawing.header["$INSUNITS"] = 6
        modelspace = drawing.modelspace()
        for boundary in order:
            for start, end in boundary:
                modelspace.add_line(start, end, dxfattribs={"layer": "WALLS"})
        drawing.saveas(path)
        paths.append(path)

    first = detect_spaces(paths[0], wall_layers={"WALLS"})
    second = detect_spaces(paths[1], wall_layers={"WALLS"})

    assert sorted(room.room_id for room in first) == sorted(room.room_id for room in second)


def test_missing_dxf_is_reported():
    with pytest.raises(FileNotFoundError):
        read_dxf("does-not-exist.dxf")


def test_detect_spaces_uses_unified_importer_for_path_inputs(tmp_path, monkeypatch):
    source = tmp_path / "office plan.dwg"
    source.write_bytes(b"mock DWG")
    dxf = tmp_path / "office plan.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    drawing.modelspace().add_lwpolyline(
        [(0, 0), (5, 0), (5, 4), (0, 4)], close=True
    )
    drawing.saveas(dxf)
    imported_paths = []

    def fake_read_cad_file(path):
        imported_paths.append(Path(path))
        return read_dxf(dxf)

    monkeypatch.setattr(space_detector, "read_cad_file", fake_read_cad_file)

    rooms = detect_spaces(source)

    assert imported_paths == [source]
    assert len(rooms) == 1
    assert rooms[0].area == pytest.approx(20.0)
