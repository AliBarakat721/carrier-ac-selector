from __future__ import annotations

from types import SimpleNamespace

from shapely.geometry import Polygon

from cad.dxf_reader import CadDocument, CadEntity
from cad.validation import validate_cad_design
from ui.cad_workspace import CadViewport


def _room(room_id, polygon, *, unit_status="VERIFIED"):
    return SimpleNamespace(
        room_id=room_id,
        polygon=polygon,
        unit_status=unit_status,
    )


def test_validation_passes_for_valid_confirmed_design():
    document = CadDocument(
        path="plan.dxf",
        units="meters",
        unit_code=6,
        layers=["A-WALL"],
        entities=[CadEntity("LINE", "A-WALL", "1", {"start": (0, 0), "end": (1, 0)})],
    )

    report = validate_cad_design(
        document,
        [_room("ROOM-1", Polygon([(0, 0), (5, 0), (5, 4), (0, 4)]))],
        indoor_units=[object()],
    )

    assert report.blocking is False
    assert report.errors == []


def test_validation_blocks_unknown_units_and_overlapping_rooms():
    document = CadDocument(
        path="plan.dxf",
        units="unitless",
        unit_code=0,
        layers=["A-WALL"],
        entities=[],
    )
    rooms = [
        _room("ROOM-1", Polygon([(0, 0), (3, 0), (3, 3), (0, 3)]), unit_status="UNIT_UNKNOWN"),
        _room("ROOM-2", Polygon([(2, 2), (5, 2), (5, 5), (2, 5)]), unit_status="UNIT_UNKNOWN"),
    ]

    report = validate_cad_design(document, rooms)

    assert report.blocking is True
    assert {issue.code for issue in report.errors} >= {"UNCONFIRMED_UNITS", "OVERLAPPING_ROOMS"}


def test_validation_warns_about_unsupported_entities():
    document = CadDocument(
        path="plan.dxf",
        units="meters",
        unit_code=6,
        layers=["A-WALL"],
        entities=[CadEntity("ACAD_PROXY_ENTITY", "A-WALL", "1", {})],
    )

    report = validate_cad_design(
        document,
        [_room("ROOM-1", Polygon([(0, 0), (2, 0), (2, 2), (0, 2)]))],
        indoor_units=[object()],
    )

    assert any(issue.code == "UNSUPPORTED_ENTITIES" for issue in report.warnings)


def test_polygon_area_uses_shoelace_formula():
    assert CadViewport.polygon_area([(0, 0), (5, 0), (5, 4), (0, 4)]) == 20.0


def test_validation_marks_overlapping_rooms_as_errors():
    document = CadDocument("plan.dxf", "meters", 6, ["A-WALL"], [])
    report = validate_cad_design(
        document,
        [
            _room("ROOM-1", Polygon([(0, 0), (3, 0), (3, 3), (0, 3)])),
            _room("ROOM-2", Polygon([(2, 2), (5, 2), (5, 5), (2, 5)])),
        ],
        indoor_units=[object(), object()],
    )
    assert report.room_statuses == {"ROOM-1": "ERROR", "ROOM-2": "ERROR"}
