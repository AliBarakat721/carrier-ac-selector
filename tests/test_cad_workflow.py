from types import SimpleNamespace

import ezdxf
from shapely.geometry import Polygon

from cad.dxf_exporter import export_hvac_dxf, reopen_and_verify
from cad.workflow import RoomAssignment, build_indoor_placements, suggest_room_assignments


def room(room_id, label=None, x=0):
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


def selected(model="42QSS", units=1, load_btu=36_000):
    return {
        "units": units,
        "load_btu": load_btu,
        "row": {
            "Indoor Unit Model": model,
            "Cooling Capacity (kW)": 5.5,
            "High CFM (numeric)": 900,
        },
    }


def test_suggestions_match_labels_then_pair_only_equal_counts():
    rooms = [room("ROOM-001", "G01"), room("ROOM-002", "G02", x=10)]
    systems = [SimpleNamespace(name="G02"), SimpleNamespace(name="G01")]

    assignments = suggest_room_assignments(rooms, systems)

    assert assignments == {"ROOM-001": "G01", "ROOM-002": "G02"}


def test_unequal_counts_leave_unmatched_rooms_for_user_mapping():
    rooms = [room("ROOM-001"), room("ROOM-002", x=10), room("ROOM-003", x=20)]
    systems = [SimpleNamespace(name="G01"), SimpleNamespace(name="G02")]

    assignments = suggest_room_assignments(rooms, systems)

    assert len(assignments) == 0


def test_build_indoor_placements_spreads_multi_units_and_adds_relationships():
    rooms = [room("ROOM-001", "Office")]
    records, relationships, warnings = build_indoor_placements(
        rooms,
        [RoomAssignment("ROOM-001", "G01")],
        {"G01": selected(units=2)},
    )

    assert not warnings
    assert len(records) == 2
    assert len(relationships) == 2
    assert len({record["ENTITY_ID"] for record in records}) == 2
    assert records[0]["ROOM_ID"] == "ROOM-001"
    assert records[0]["POSITION"] != records[1]["POSITION"]
    assert all(link["ROLE"] == "INDOOR" for link in relationships)


def test_build_indoor_placements_reports_unassigned_system_without_origin_fallback():
    records, relationships, warnings = build_indoor_placements(
        [room("ROOM-001")],
        [RoomAssignment("ROOM-001", "")],
        {},
    )

    assert records == []
    assert relationships == []
    assert warnings == ["NO_SELECTED_UNIT:ROOM-001:UNASSIGNED"]


def test_hap_selection_can_be_exported_into_detected_room(tmp_path):
    source = tmp_path / "floor-plan.dxf"
    output = tmp_path / "placed.dxf"
    schedule = tmp_path / "placed.csv"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    drawing.modelspace().add_lwpolyline([(0, 0), (5, 0), (5, 4), (0, 4)], close=True)
    drawing.saveas(source)

    rooms = [room("ROOM-001", "Office")]
    indoor, relationships, warnings = build_indoor_placements(
        rooms, [RoomAssignment("ROOM-001", "G01")], {"G01": selected()}
    )
    assert not warnings
    result = export_hvac_dxf(
        source, output, indoor_units=indoor, outdoor_units=[],
        relationships=relationships, rooms=rooms, schedule_path=schedule,
    )

    assert result.indoor_count == 1
    assert output.exists() and schedule.exists()
    assert reopen_and_verify(output) == []
