from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main import HAPToCarrierApp


def test_main_window_groups_workflows_and_tools_into_four_workspaces():
    app = QApplication.instance() or QApplication(sys.argv)
    window = HAPToCarrierApp()
    try:
        workspaces = [
            window.workspace_tabs.tabText(index)
            for index in range(window.workspace_tabs.count())
        ]
        assert workspaces == ["Design", "Select", "Analyze", "Tools"]
        assert window.workspace_tabs.tabBar().isHidden()
        assert [window.tools_tabs.tabText(index) for index in range(window.tools_tabs.count())] == [
            "Inverter ROI", "Electrical", "OCR Diagnostics"
        ]
        assert all("CAD Start" not in window.tabs.tabText(index) for index in range(window.tabs.count()))

        window._navigate_ribbon("Tools")
        assert window.workspace_tabs.currentWidget() is window.tools_workspace
        assert window.ribbon_buttons["Tools"].isChecked()
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_project_save_copies_hap_and_roundtrips_selected_equipment(tmp_path):
    import pandas as pd

    from project_store import ProjectStore

    app = QApplication.instance() or QApplication(sys.argv)
    window = HAPToCarrierApp()
    try:
        report = tmp_path / "report.pdf"
        report.write_bytes(b"portable HAP source")
        window.project_store = ProjectStore.create(tmp_path / "project", "Test Project")
        window.hap_source_path = str(report)
        window.selected_units = {
            "System 1": {
                "row": pd.Series({
                    "Cooling Capacity (Btu/hr)": 12000.0,
                    "Effective Btu": 12000.0,
                    "High CFM (numeric)": 400.0,
                    "EER (Btu/Wh)": 12.0,
                    "Category": "Ductless",
                    "Series": "40MAQ",
                    "System Model": "40MAQ-A12",
                    "Indoor Unit Model": "40MAQ-A12",
                    "Outdoor Unit Model": "38MHRBC12AA3",
                    "Type": "Heat Pump",
                    "Power Supply": "208/230V",
                }),
                "units": 1,
                "effective_btu": 12000.0,
                "load_btu": 10000.0,
                "load_cfm": 350.0,
            }
        }

        assert window.save_project(silent=True)
        reopened = ProjectStore.open(tmp_path / "project")
        assert reopened.asset("hap_source").read_bytes() == report.read_bytes()
        saved = reopened.state["selected_units"]["System 1"]
        assert saved["row"]["Indoor Unit Model"] == "40MAQ-A12"

        window._restore_project_cart(reopened.state["selected_units"])
        assert window.selected_units["System 1"]["row"]["Indoor Unit Model"] == "40MAQ-A12"
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_successful_cad_import_is_owned_by_active_project(tmp_path):
    import ezdxf

    from cad.dxf_reader import read_dxf
    from cad.room_review import RoomReviewSession
    from project_store import ProjectStore

    app = QApplication.instance() or QApplication(sys.argv)
    window = HAPToCarrierApp()
    try:
        source = tmp_path / "external-plan.dxf"
        drawing = ezdxf.new("R2018")
        drawing.layers.new("A-WALL")
        drawing.modelspace().add_line((0, 0), (10, 0), dxfattribs={"layer": "A-WALL"})
        drawing.saveas(source)
        raw = ezdxf.readfile(source)
        document = read_dxf(source, drawing=raw)
        project = ProjectStore.create(tmp_path / "project")
        window.project_store = project
        window._cad_job_id = 7
        window._cad_loading_path = str(source)
        window.review_cad_rooms = lambda initial=False: None

        window._on_cad_loaded(7, document, RoomReviewSession(document), {}, raw)

        saved_project = ProjectStore.open(project.root)
        assert saved_project.asset("cad_original").read_bytes() == source.read_bytes()
        working = saved_project.asset("cad_working")
        assert working is not None and working.is_file()
        assert ezdxf.readfile(working).modelspace().query("LINE")
        assert Path(window.cad_source_path).is_relative_to(project.root)
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
