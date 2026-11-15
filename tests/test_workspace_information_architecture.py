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
