from types import SimpleNamespace

import ezdxf
import pytest
from shapely.geometry import Point, Polygon

from cad.dxf_exporter import export_hvac_dxf, reopen_and_verify
from cad.tag_config import CAD_LAYERS


def indoor(entity_id="INDOOR-001", tag_id="IDU-01"):
    return {
        "ENTITY_ID": entity_id,
        "TAG_ID": tag_id,
        "ROOM_ID": "G01",
        "ROOM_NAME": "غرفة المعيشة",
        "MODEL": "42QSS",
        "CAPACITY_KW": 5.5,
        "CAPACITY_MBH": 18.8,
        "AIRFLOW_CFM": 530,
        "HAP_LOAD_KW": 5.0,
        "CAPACITY_STATUS": "SELECTED",
        "CAPACITY_MARGIN_PCT": 10.0,
        "SYSTEM_ID": "SYS-01",
        "SELECTION_STATUS": "SELECTED",
        "DATA_VERIFICATION": "NEEDS_REVIEW",
        "SOURCE_REFERENCE": "Carrier Manual",
        "POSITION": (2.0, 2.0),
    }


def outdoor():
    return {
        "ENTITY_ID": "OUTDOOR-001",
        "TAG_ID": "ODU-01",
        "SYSTEM_ID": "SYS-01",
        "MODEL": "38MAR",
        "SYSTEM_MODEL": "38MAR",
        "POWER_SUPPLY": "220V/1Ph/50Hz",
        "REFRIGERANT": "R410A",
        "WEIGHT_KG": 50,
        "DIMENSIONS_MM": "800x300x600",
        "SELECTION_STATUS": "SELECTED",
        "DATA_VERIFICATION": "NEEDS_REVIEW",
        "SOURCE_REFERENCE": "Carrier Manual",
        "POSITION": (8.0, 2.0),
    }


def make_source(path):
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = 6
    drawing.layers.new("ORIGINAL")
    drawing.modelspace().add_line((100, 100), (110, 100), dxfattribs={"layer": "ORIGINAL"})
    drawing.saveas(path)


def test_export_preserves_original_layers_and_round_trips(tmp_path):
    source = tmp_path / "source.dxf"
    output = tmp_path / "hvac-output.dxf"
    schedule = tmp_path / "equipment.csv"
    make_source(source)
    room = SimpleNamespace(
        room_id="G01",
        label="غرفة المعيشة",
        area=20.0,
        perimeter=18.0,
        status="NEEDS_REVIEW",
        source="closed_polyline",
        polygon=Polygon([(0, 0), (5, 0), (5, 4), (0, 4)]),
    )

    result = export_hvac_dxf(
        source,
        output,
        indoor_units=[indoor()],
        outdoor_units=[outdoor()],
        relationships=[
            {"SYSTEM_ID": "SYS-01", "ENTITY_ID": "INDOOR-001", "ROLE": "INDOOR"},
            {"SYSTEM_ID": "SYS-01", "ENTITY_ID": "OUTDOOR-001", "ROLE": "OUTDOOR"},
        ],
        rooms=[room],
        schedule_path=schedule,
    )

    assert result.output_path == output
    assert output.exists() and schedule.exists()
    exported = ezdxf.readfile(output)
    assert "ORIGINAL" in exported.layers
    assert set(CAD_LAYERS.values()).issubset({layer.dxf.name for layer in exported.layers})
    assert len(list(exported.modelspace().query("INSERT"))) == 3
    assert reopen_and_verify(output) == []
    assert "غرفة المعيشة" in schedule.read_text(encoding="utf-8-sig")


def test_repeated_export_updates_existing_entities_without_duplicates(tmp_path):
    source = tmp_path / "source.dxf"
    output = tmp_path / "hvac-output.dxf"
    make_source(source)
    kwargs = dict(
        indoor_units=[indoor()],
        outdoor_units=[outdoor()],
        relationships=[
            {"SYSTEM_ID": "SYS-01", "ENTITY_ID": "INDOOR-001", "ROLE": "INDOOR"},
            {"SYSTEM_ID": "SYS-01", "ENTITY_ID": "OUTDOOR-001", "ROLE": "OUTDOOR"},
        ],
    )
    export_hvac_dxf(source, output, **kwargs)
    export_hvac_dxf(output, output, **kwargs)

    exported = ezdxf.readfile(output)
    references = list(exported.modelspace().query("INSERT"))
    assert len(references) == 2
    assert sum(1 for ref in references for attr in ref.attribs if attr.dxf.tag == "ENTITY_ID") == 2
    assert reopen_and_verify(output) == []


def test_reexport_without_position_preserves_existing_location(tmp_path):
    source = tmp_path / "source.dxf"
    output = tmp_path / "hvac-output.dxf"
    make_source(source)
    export_hvac_dxf(
        source,
        output,
        indoor_units=[indoor()],
        outdoor_units=[outdoor()],
        relationships=[],
    )
    updated = indoor()
    del updated["POSITION"]
    export_hvac_dxf(
        output,
        output,
        indoor_units=[updated],
        outdoor_units=[outdoor()],
        relationships=[],
    )

    exported = ezdxf.readfile(output)
    indoor_ref = next(
        ref for ref in exported.modelspace().query("INSERT")
        if any(attr.dxf.tag == "ENTITY_ID" and attr.dxf.text == "INDOOR-001" for attr in ref.attribs)
    )
    assert tuple(indoor_ref.dxf.insert)[:2] == (2.0, 2.0)


def test_export_rolls_back_when_second_output_replace_fails(tmp_path, monkeypatch):
    source = tmp_path / "source.dxf"
    output = tmp_path / "hvac-output.dxf"
    schedule = tmp_path / "equipment.csv"
    make_source(source)
    import cad.dxf_exporter as exporter

    original_replace = exporter.os.replace
    calls = {"count": 0}

    def fail_second_replace(src, dst):
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("injected schedule replacement failure")
        return original_replace(src, dst)

    monkeypatch.setattr(exporter.os, "replace", fail_second_replace)
    try:
        export_hvac_dxf(
            source,
            output,
            indoor_units=[indoor()],
            outdoor_units=[outdoor()],
            relationships=[],
            schedule_path=schedule,
        )
    except OSError:
        pass
    else:
        raise AssertionError("fault injection should fail export")

    assert not output.exists()
    assert not schedule.exists()


def test_export_restores_existing_outputs_when_schedule_replace_fails(tmp_path, monkeypatch):
    source = tmp_path / "source.dxf"
    output = tmp_path / "hvac-output.dxf"
    schedule = tmp_path / "equipment.csv"
    make_source(source)
    export_hvac_dxf(source, output, indoor_units=[indoor()], outdoor_units=[outdoor()], relationships=[], schedule_path=schedule)
    old_dxf = output.read_bytes()
    old_schedule = schedule.read_bytes()
    import cad.dxf_exporter as exporter
    original_replace = exporter.os.replace
    calls = {"count": 0}

    def fail_schedule_replace(src, dst):
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("injected replacement failure")
        return original_replace(src, dst)

    monkeypatch.setattr(exporter.os, "replace", fail_schedule_replace)
    with pytest.raises(OSError):
        export_hvac_dxf(source, output, indoor_units=[indoor()], outdoor_units=[outdoor()], relationships=[], schedule_path=schedule)

    assert output.read_bytes() == old_dxf
    assert schedule.read_bytes() == old_schedule


def test_export_retains_backups_when_rollback_itself_fails(tmp_path, monkeypatch):
    source = tmp_path / "source.dxf"
    output = tmp_path / "hvac-output.dxf"
    schedule = tmp_path / "equipment.csv"
    make_source(source)
    export_hvac_dxf(source, output, indoor_units=[indoor()], outdoor_units=[outdoor()], relationships=[], schedule_path=schedule)
    import cad.dxf_exporter as exporter
    original_replace = exporter.os.replace
    calls = {"count": 0}

    def fail_replacement_and_restore(src, dst):
        calls["count"] += 1
        if calls["count"] in {2, 3}:
            raise OSError("injected replacement/rollback failure")
        return original_replace(src, dst)

    monkeypatch.setattr(exporter.os, "replace", fail_replacement_and_restore)
    with pytest.raises(RuntimeError, match="recovery backups retained at"):
        export_hvac_dxf(source, output, indoor_units=[indoor()], outdoor_units=[outdoor()], relationships=[], schedule_path=schedule)

    backups = list(tmp_path.glob("*.backup"))
    assert backups
    for backup in backups:
        backup.unlink()


def test_rollback_error_lists_only_backups_still_present(tmp_path, monkeypatch):
    source = tmp_path / "source.dxf"
    output = tmp_path / "hvac-output.dxf"
    schedule = tmp_path / "equipment.csv"
    make_source(source)
    export_hvac_dxf(
        source,
        output,
        indoor_units=[indoor()],
        outdoor_units=[outdoor()],
        relationships=[],
        schedule_path=schedule,
    )
    import cad.dxf_exporter as exporter

    original_replace = exporter.os.replace
    calls = {"count": 0}

    def fail_schedule_restore(src, dst):
        calls["count"] += 1
        if calls["count"] in {2, 4}:
            raise OSError("injected schedule replacement/rollback failure")
        return original_replace(src, dst)

    monkeypatch.setattr(exporter.os, "replace", fail_schedule_restore)
    with pytest.raises(RuntimeError, match="recovery backups retained at:") as error:
        export_hvac_dxf(
            source,
            output,
            indoor_units=[indoor()],
            outdoor_units=[outdoor()],
            relationships=[],
            schedule_path=schedule,
        )

    message = str(error.value)
    assert ".hvac-output.dxf." not in message
    assert ".equipment.csv." in message
    for backup in tmp_path.glob("*.backup"):
        backup.unlink()


def test_unitless_room_export_does_not_label_raw_metrics_as_metric(tmp_path):
    source = tmp_path / "source.dxf"
    output = tmp_path / "hvac-output.dxf"
    make_source(source)
    room = SimpleNamespace(
        room_id="ROOM-001",
        label="Unknown Units",
        area=20.0,
        perimeter=18.0,
        raw_area=20.0,
        raw_perimeter=18.0,
        drawing_units="unitless",
        unit_status="UNIT_UNKNOWN",
        status="NEEDS_REVIEW",
        source="closed_polyline",
        polygon=Polygon([(0, 0), (5, 0), (5, 4), (0, 4)]),
    )
    export_hvac_dxf(
        source,
        output,
        indoor_units=[indoor()],
        outdoor_units=[outdoor()],
        relationships=[],
        rooms=[room],
    )

    exported = ezdxf.readfile(output)
    room_ref = next(
        ref for ref in exported.modelspace().query("INSERT")
        if any(attr.dxf.tag == "ENTITY_ID" and attr.dxf.text == "ROOM-001" for attr in ref.attribs)
    )
    values = {attr.dxf.tag: attr.dxf.text for attr in room_ref.attribs}
    assert values["AREA_M2"] == ""
    assert values["PERIMETER_M"] == ""
    assert values["AREA_SOURCE"] == "UNIT_UNKNOWN"


def test_invalid_geometry_room_export_does_not_publish_verified_metrics(tmp_path):
    source = tmp_path / "source.dxf"
    output = tmp_path / "hvac-output.dxf"
    make_source(source)
    room = SimpleNamespace(
        room_id="ROOM-BOWTIE",
        label="Invalid",
        area=0.0,
        perimeter=20.0,
        raw_area=0.0,
        raw_perimeter=20.0,
        drawing_units="meters",
        unit_status="VERIFIED",
        status="INVALID_GEOMETRY",
        source="closed_polyline",
        polygon=Polygon([(0, 0), (5, 5), (0, 5), (5, 0)]),
    )
    export_hvac_dxf(source, output, indoor_units=[indoor()], outdoor_units=[outdoor()], relationships=[], rooms=[room])

    exported = ezdxf.readfile(output)
    room_ref = next(ref for ref in exported.modelspace().query("INSERT") if any(
        attr.dxf.tag == "ENTITY_ID" and attr.dxf.text == "ROOM-BOWTIE" for attr in ref.attribs
    ))
    values = {attr.dxf.tag: attr.dxf.text for attr in room_ref.attribs}
    assert values["AREA_M2"] == ""
    assert values["PERIMETER_M"] == ""
    assert values["AREA_SOURCE"] == "INVALID_GEOMETRY"


def test_room_label_uses_point_inside_irregular_room(tmp_path):
    source = tmp_path / "source.dxf"
    output = tmp_path / "hvac-output.dxf"
    make_source(source)
    room_polygon = Polygon([(0, 0), (4, 0), (4, 1), (1, 1), (1, 4), (0, 4)])
    room = SimpleNamespace(
        room_id="G-L",
        label="L Room",
        area=room_polygon.area,
        perimeter=room_polygon.length,
        raw_area=room_polygon.area,
        raw_perimeter=room_polygon.length,
        drawing_units="meters",
        unit_status="VERIFIED",
        status="NEEDS_REVIEW",
        source="closed_polyline",
        polygon=room_polygon,
    )
    export_hvac_dxf(
        source,
        output,
        indoor_units=[indoor()],
        outdoor_units=[outdoor()],
        relationships=[],
        rooms=[room],
    )

    exported = ezdxf.readfile(output)
    room_ref = next(
        ref for ref in exported.modelspace().query("INSERT")
        if any(attr.dxf.tag == "ENTITY_ID" and attr.dxf.text == "ROOM-G-L" for attr in ref.attribs)
    )
    assert room_polygon.contains(Point(room_ref.dxf.insert.x, room_ref.dxf.insert.y))
