from pathlib import Path
import math

import ezdxf
import pytest

from cad.dxf_reader import read_dxf
from cad.space_detector import detect_spaces


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
    assert diagnostics[0]["distance"] == pytest.approx(0.001)


def test_missing_dxf_is_reported():
    with pytest.raises(FileNotFoundError):
        read_dxf("does-not-exist.dxf")
