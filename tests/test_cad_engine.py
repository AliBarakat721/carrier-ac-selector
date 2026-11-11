from pathlib import Path

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


def test_missing_dxf_is_reported():
    with pytest.raises(FileNotFoundError):
        read_dxf("does-not-exist.dxf")
