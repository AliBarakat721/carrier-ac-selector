import logging
import sys
from pathlib import Path

import pandas as pd
import pytest
from docx import Document
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
import roi_tab


def _electrical_rows():
    return [
        {
            "system": "G01",
            "model": "53QHE36-708F",
            "qty": 1,
            "kw_elec": 3.42,
            "voltage": 220.0,
            "phase": 1,
            "fla": 16.0,
            "mcb_size": 25,
            "cable_desc": "3×4 mm² + 4 mm² E",
            "vdrop_pct": 1.4,
            "vdrop_ok": True,
        }
    ]


def _electrical_summary():
    return {
        "total_kw": 3.42,
        "demand_fla": 16.0,
        "main_mcb": 25,
        "generator_kva": 5.0,
        "main_cable_desc": "2×4 mm² + 4 mm² E",
        "warnings": [],
    }


def _roi_inputs():
    return {
        "btu": 36_000,
        "units": 1,
        "hours_per_day": 8.0,
        "days_per_year": 180,
        "tariff": 1.5,
        "currency": "EGP",
        "fixed_unit_price": 18_000.0,
        "inv_unit_price": 26_500.0,
        "install_delta": 0.0,
        "saving_pct": 30.0,
        "lifetime_years": 12,
        "co2_factor": 0.30,
    }


def test_excel_export_is_styled_and_receives_optional_sheets(tmp_path):
    path = tmp_path / "report.xlsx"
    pd.DataFrame({"System": ["G01"], "Cooling Capacity": [36_000]}).to_excel(
        path, index=False
    )
    main._style_excel_file(str(path))

    roi_inputs = _roi_inputs()
    roi_result = roi_tab.compute_roi(
        **{key: value for key, value in roi_inputs.items() if key != "currency"}
    )
    roi_tab.append_roi_sheet_to_excel(str(path), roi_inputs, roi_result)
    main.append_electrical_sheet_to_excel(
        str(path), _electrical_rows(), _electrical_summary()
    )

    workbook = load_workbook(path)
    assert workbook.active.freeze_panes == "A2"
    assert "Inverter_ROI" in workbook.sheetnames
    assert "Electrical_Schedule" in workbook.sheetnames


def test_word_export_helpers_create_readable_sections(tmp_path):
    path = tmp_path / "report.docx"
    document = Document()

    roi_inputs = _roi_inputs()
    roi_result = roi_tab.compute_roi(
        **{key: value for key, value in roi_inputs.items() if key != "currency"}
    )
    roi_tab.append_roi_section_to_docx(document, roi_inputs, roi_result)
    main.append_electrical_section_to_docx(
        document, _electrical_rows(), _electrical_summary()
    )
    document.save(path)

    reopened = Document(path)
    text = "\n".join(paragraph.text for paragraph in reopened.paragraphs)
    assert "Inverter vs Fixed Speed" in text
    assert "HVAC Electrical Load" in text
    assert len(reopened.tables) >= 2


def test_export_failures_are_logged_and_propagated(tmp_path, caplog):
    missing_xlsx = tmp_path / "missing.xlsx"
    missing_docx = tmp_path / "missing.docx"
    roi_inputs = _roi_inputs()
    roi_result = roi_tab.compute_roi(
        **{key: value for key, value in roi_inputs.items() if key != "currency"}
    )

    caplog.set_level(logging.ERROR)
    with pytest.raises(FileNotFoundError):
        main._style_excel_file(str(missing_xlsx))
    assert "Failed to style Excel export" in caplog.text

    caplog.clear()
    with pytest.raises(FileNotFoundError):
        roi_tab.append_roi_sheet_to_excel(
            str(missing_xlsx), roi_inputs, roi_result
        )
    assert "Failed to append ROI sheet to Excel" in caplog.text

    caplog.clear()
    with pytest.raises(FileNotFoundError):
        main.append_electrical_sheet_to_excel(
            str(missing_xlsx), _electrical_rows(), _electrical_summary()
        )
    assert "Failed to append electrical sheet to Excel" in caplog.text

    class BrokenDocument:
        def add_page_break(self):
            raise RuntimeError("simulated Word export failure")

    caplog.clear()
    with pytest.raises(RuntimeError, match="simulated Word export failure"):
        roi_tab.append_roi_section_to_docx(BrokenDocument(), roi_inputs, roi_result)
    assert "Failed to append ROI section to Word document" in caplog.text

    caplog.clear()
    with pytest.raises(RuntimeError, match="simulated Word export failure"):
        main.append_electrical_section_to_docx(
            BrokenDocument(), _electrical_rows(), _electrical_summary()
        )
    assert "Failed to append electrical section to Word document" in caplog.text


def test_atomic_export_replaces_only_after_success(tmp_path):
    final_path = tmp_path / "report.xlsx"
    final_path.write_text("original", encoding="utf-8")

    with main._atomic_export(str(final_path)) as temporary_path:
        Path(temporary_path).write_text("complete", encoding="utf-8")
    assert final_path.read_text(encoding="utf-8") == "complete"

    with pytest.raises(RuntimeError, match="simulated failure"):
        with main._atomic_export(str(final_path)) as temporary_path:
            Path(temporary_path).write_text("partial", encoding="utf-8")
            raise RuntimeError("simulated failure")
    assert final_path.read_text(encoding="utf-8") == "complete"
    assert not list(tmp_path.glob(".report.xlsx.*"))


def test_dotted_export_basename_is_preserved_when_changing_extension():
    assert main._replace_export_extension("AHU 1.2", "docx") == "AHU 1.2.docx"
    assert main._replace_export_extension("AHU 1.xlsx", "docx") == "AHU 1.docx"


def test_locked_destination_shows_actionable_message_and_atomic_export_keeps_old_file(
    tmp_path, monkeypatch
):
    final_path = tmp_path / "locked.xlsx"
    final_path.write_text("original", encoding="utf-8")

    def fail_replace(_source, _destination):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(main.os, "replace", fail_replace)
    with pytest.raises(PermissionError):
        with main._atomic_export(str(final_path)) as temporary_path:
            Path(temporary_path).write_text("incomplete", encoding="utf-8")

    message = main._export_error_message(PermissionError(13, "Permission denied"))
    roi_message = roi_tab._export_error_message(
        PermissionError(13, "Permission denied")
    )
    assert "Excel or Word" in message
    assert "Excel or Word" in roi_message
    assert "Permission denied" not in message
    assert "Permission denied" not in roi_message
    assert final_path.read_text(encoding="utf-8") == "original"
    assert not list(tmp_path.glob(".locked.xlsx.*"))


def test_standalone_electrical_export_shows_error_without_success(
    tmp_path, monkeypatch
):
    final_path = tmp_path / "electrical.xlsx"
    messages = []

    class FakeDialog:
        @staticmethod
        def getSaveFileName(*_args):
            return str(final_path), ""

    class FakeMessageBox:
        @staticmethod
        def critical(*_args):
            messages.append("critical")

        @staticmethod
        def information(*_args):
            messages.append("information")

    def fail_export(*_args, **_kwargs):
        raise RuntimeError("simulated electrical failure")

    monkeypatch.setattr(main, "QFileDialog", FakeDialog)
    monkeypatch.setattr(main, "QMessageBox", FakeMessageBox)
    monkeypatch.setattr(main, "append_electrical_sheet_to_excel", fail_export)

    fake_tab = type("ElectricalTab", (), {})()
    fake_tab.last_results = (_electrical_rows(), _electrical_summary())
    main.ElectricalScheduleTab._export_standalone(fake_tab)

    assert messages == ["critical"]
    assert not final_path.exists()


def test_standalone_roi_export_shows_error_without_success(
    tmp_path, monkeypatch
):
    final_path = tmp_path / "roi.xlsx"
    messages = []

    class FakeDialog:
        @staticmethod
        def getSaveFileName(*_args):
            return str(final_path), ""

    class FakeMessageBox:
        @staticmethod
        def critical(*_args):
            messages.append("critical")

        @staticmethod
        def information(*_args):
            messages.append("information")

    def fail_export(*_args, **_kwargs):
        raise RuntimeError("simulated ROI failure")

    monkeypatch.setattr(roi_tab, "QFileDialog", FakeDialog)
    monkeypatch.setattr(roi_tab, "QMessageBox", FakeMessageBox)
    monkeypatch.setattr(roi_tab, "append_roi_sheet_to_excel", fail_export)

    fake_calculator = type("ROICalculator", (), {})()
    fake_calculator.get_inputs = lambda: _roi_inputs()
    roi_tab.InverterROICalculator._export_standalone_roi(fake_calculator)

    assert messages == ["critical"]
    assert not final_path.exists()


def test_batch_csv_export_does_not_run_excel_post_processing(tmp_path, monkeypatch):
    final_path = tmp_path / "systems.csv"
    messages = []

    class FakeMessageBox:
        @staticmethod
        def information(*_args):
            messages.append("information")

        @staticmethod
        def critical(*_args):
            messages.append("critical")

    fake_window = type("BatchWindow", (), {})()
    fake_window.batch_results = pd.DataFrame(
        {"System": ["G01"], "Load (Btu/hr)": [36_000]}
    )
    fake_window._default_export_name = lambda *_args, **_kwargs: "systems.xlsx"
    fake_window._save_file_dialog = lambda *_args: (str(final_path), "CSV File (*.csv)")
    fake_window._normalize_save_path = lambda path, _filter: path
    fake_window._set_status = lambda _message: None
    fake_window._roi_append_to_excel = lambda *_args: pytest.fail(
        "ROI Excel post-processing must not run for CSV exports"
    )
    fake_window._elec_append_to_excel = lambda *_args: pytest.fail(
        "Electrical Excel post-processing must not run for CSV exports"
    )
    monkeypatch.setattr(main, "QMessageBox", FakeMessageBox)

    main.HAPToCarrierApp._export_batch(fake_window)

    assert messages == ["information"]
    exported = pd.read_csv(final_path, encoding="utf-8-sig")
    assert exported.to_dict(orient="records") == [
        {"System": "G01", "Load (Btu/hr)": 36_000}
    ]


def test_multi_unit_csv_export_does_not_run_excel_post_processing(
    tmp_path, monkeypatch
):
    final_path = tmp_path / "multi.csv"
    messages = []

    class FakeMessageBox:
        @staticmethod
        def information(*_args):
            messages.append("information")

        @staticmethod
        def critical(*_args):
            messages.append("critical")

    multi = pd.DataFrame(
        {"Unit Btu": [18_000], "Total Btu": [36_000], "Excess %": [0.0]}
    )
    fake_window = type("MultiWindow", (), {})()
    fake_window.df = pd.DataFrame()
    fake_window.current_btu = 36_000
    fake_window.current_cfm = 1_200
    fake_window.current_system_name = "G01"
    fake_window._filter_args = lambda: {}
    fake_window._default_export_name = lambda *_args, **_kwargs: "multi.xlsx"
    fake_window._save_file_dialog = lambda *_args: (str(final_path), "CSV File (*.csv)")
    fake_window._normalize_save_path = lambda path, _filter: path
    fake_window._set_status = lambda _message: None
    fake_window._roi_append_to_excel = lambda *_args: pytest.fail(
        "ROI Excel post-processing must not run for CSV exports"
    )
    fake_window._elec_append_to_excel = lambda *_args: pytest.fail(
        "Electrical Excel post-processing must not run for CSV exports"
    )
    monkeypatch.setattr(main.core, "multi_unit_options", lambda *_args, **_kwargs: multi)
    monkeypatch.setattr(main, "QMessageBox", FakeMessageBox)

    main.HAPToCarrierApp._export_multi(fake_window)

    assert messages == ["information"]
    exported = pd.read_csv(final_path, encoding="utf-8-sig")
    assert list(exported.columns) == ["System", "Unit Btu", "Total Btu", "Excess %"]
    assert exported.iloc[0].to_dict() == {
        "System": "G01",
        "Unit Btu": 18_000,
        "Total Btu": 36_000,
        "Excess %": 0.0,
    }
