import os
from pathlib import Path

import ezdxf
import pytest

from cad import importer


def test_dxf_is_read_directly_without_oda(tmp_path, monkeypatch):
    source = tmp_path / "plan.dxf"
    source.write_text("fixture", encoding="utf-8")
    expected = object()
    monkeypatch.setattr(importer, "read_dxf", lambda path: expected)
    monkeypatch.setattr(importer.odafc, "is_installed", lambda: False)

    assert importer.read_cad_file(source) is expected


def test_explicit_converter_path_is_written_to_ezdxf_options(tmp_path, monkeypatch, request):
    source = tmp_path / "office plan.dwg"
    source.write_bytes(b"DWG fixture")
    executable = tmp_path / (
        "ODA File Converter.exe" if os.name == "nt" else "ODA File Converter.AppImage"
    )
    executable.write_bytes(b"converter fixture")
    if os.name != "nt":
        executable.chmod(0o755)

    key = "win_exec_path" if os.name == "nt" else "unix_exec_path"
    previous = ezdxf.options.get("odafc-addon", key)
    request.addfinalizer(lambda: ezdxf.options.set("odafc-addon", key, previous))
    monkeypatch.setenv("ODA_FILE_CONVERTER", str(executable))

    converted_paths = []

    def fake_convert(source_path, destination_path, **kwargs):
        converted_paths.append(Path(destination_path))
        ezdxf.new("R2018").saveas(destination_path)

    monkeypatch.setattr(importer.odafc, "convert", fake_convert)

    document = importer.read_cad_file(source)

    assert Path(importer._configure_converter()) == executable.resolve()
    assert document.path == source
    assert len(converted_paths) == 1


@pytest.mark.skipif(os.name == "nt", reason="Executable permission bits are Linux-specific")
def test_non_executable_converter_path_has_actionable_error(tmp_path, monkeypatch, request):
    source = tmp_path / "plan.dwg"
    source.write_bytes(b"DWG fixture")
    executable = tmp_path / "ODA File Converter.AppImage"
    executable.write_bytes(b"not executable")
    executable.chmod(0o644)

    previous = ezdxf.options.get("odafc-addon", "unix_exec_path")
    request.addfinalizer(
        lambda: ezdxf.options.set("odafc-addon", "unix_exec_path", previous)
    )
    monkeypatch.setenv("ODA_FILE_CONVERTER", str(executable))

    with pytest.raises(importer.DwgImportError, match="not executable"):
        importer.read_cad_file(source)


def test_dwg_reports_missing_oda_converter(tmp_path, monkeypatch):
    source = tmp_path / "plan.dwg"
    source.write_bytes(b"DWG fixture")
    monkeypatch.setattr(importer.odafc, "is_installed", lambda: False)

    with pytest.raises(importer.DwgImportError, match="ODA File Converter"):
        importer.read_cad_file(source)


def test_dwg_is_converted_to_temporary_dxf_and_read(tmp_path, monkeypatch):
    source = tmp_path / "office.dwg"
    source.write_bytes(b"DWG fixture")
    converted_paths = []

    def fake_convert(source_path, destination_path, **kwargs):
        converted_paths.append(Path(destination_path))
        drawing = ezdxf.new("R2018")
        drawing.saveas(destination_path)

    monkeypatch.setattr(importer.odafc, "is_installed", lambda: True)
    monkeypatch.setattr(importer.odafc, "convert", fake_convert)

    document = importer.read_cad_file(source)

    assert document.path == source
    assert document.entity_count == 0
    assert len(converted_paths) == 1
    assert not converted_paths[0].exists()
