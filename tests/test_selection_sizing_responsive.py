from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QInputDialog

from cad.dxf_reader import CadDocument, CadEntity
from ui.cad_workspace import CadWorkspace
from ui.selection_workspace import SelectionWorkspace

_TEST_WIDGETS = []


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    _TEST_WIDGETS.clear()
    yield application
    for widget in reversed(_TEST_WIDGETS):
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    application.processEvents()
    _TEST_WIDGETS.clear()


def _track(app, widget):
    _TEST_WIDGETS.append(widget)
    return widget


def _catalog():
    return pd.DataFrame([
        {"Category": "High Wall", "Series": "X", "System Model": "MODEL-30",
         "Indoor Unit Model": "IND-30", "Outdoor Unit Model": "OUT-30",
         "Cooling Capacity (Btu/hr)": 30000, "High CFM (numeric)": 1000,
         "EER (Btu/Wh)": 12.0, "Type": "Heat Pump"},
        {"Category": "High Wall", "Series": "X", "System Model": "MODEL-25",
         "Indoor Unit Model": "IND-25", "Outdoor Unit Model": "OUT-25",
         "Cooling Capacity (Btu/hr)": 25000, "High CFM (numeric)": 800,
         "EER (Btu/Wh)": 11.0, "Type": "Heat Pump"},
    ])


def test_selection_automatically_matches_hap_load_with_derating(app):
    workspace = _track(app, SelectionWorkspace())
    workspace.set_catalog(_catalog(), {})
    workspace.set_context("AHU-01", 24000, 900, derate_pct=10)
    assert workspace.table.rowCount() == 1
    assert workspace.table.item(0, 2).text() == "MODEL-30"
    assert workspace.match_summary.text().startswith("Auto-sizing for 24,000 Btu/h + 900 CFM")
    assert workspace.table.item(0, 9).text() == "112%"
    assert workspace.table.item(0, 10).text() == "111%"
    workspace.auto_match.setChecked(False)
    assert workspace.table.rowCount() == 2
    workspace.close()


def test_cad_rotate_and_scale_transform_geometry_and_undo(app):
    workspace = _track(app, CadWorkspace(Path(__file__).resolve().parents[1]))
    document = CadDocument(Path("sample.dxf"), "meters", 6, ["0"], [])
    line = CadEntity("LINE", "WALLS", None, {"start": (0.0, 0.0), "end": (10.0, 0.0)})
    document.entities.append(line)
    workspace.set_project_data(document, [])
    assert not workspace.tool_buttons["rotate"].icon().isNull()
    assert not workspace.tool_buttons["scale"].icon().isNull()

    workspace._transform_entity(line, "rotate", 90.0)
    rotated = document.entities[0]
    assert rotated.data["start"] == pytest.approx((5.0, -5.0))
    assert rotated.data["end"] == pytest.approx((5.0, 5.0))
    workspace._transform_entity(rotated, "scale", 2.0)
    scaled = document.entities[0]
    assert scaled.data["start"] == pytest.approx((5.0, -10.0))
    assert scaled.data["end"] == pytest.approx((5.0, 10.0))
    workspace._undo_edit()
    assert document.entities[0] == rotated
    workspace.close()


def test_rotate_and_scale_buttons_transform_canvas_item_and_undo(app, monkeypatch):
    workspace = _track(app, CadWorkspace(Path(__file__).resolve().parents[1]))
    document = CadDocument(Path("sample.dxf"), "meters", 6, ["0"], [])
    line = CadEntity("LINE", "WALLS", None, {"start": (-100.0, 0.0), "end": (100.0, 0.0)})
    document.entities.append(line)
    workspace.set_project_data(document, [])
    workspace.resize(1000, 650)
    workspace.show()
    app.processEvents()
    viewport = workspace.viewport
    viewport.setSceneRect(-250, -200, 500, 400)
    viewport.fitInView(viewport.sceneRect(), Qt.KeepAspectRatio)
    app.processEvents()

    monkeypatch.setattr(QInputDialog, "getDouble", staticmethod(lambda *_args: (90.0, True)))
    workspace.tool_buttons["rotate"].click()
    point = viewport.mapFromScene(QPointF(0, 0))
    QTest.mouseClick(viewport.viewport(), Qt.LeftButton, pos=point)
    app.processEvents()
    rotated = document.entities[0]
    assert rotated.data["start"] == pytest.approx((0.0, -100.0))
    assert rotated.data["end"] == pytest.approx((0.0, 100.0))

    monkeypatch.setattr(QInputDialog, "getDouble", staticmethod(lambda *_args: (2.0, True)))
    workspace.tool_buttons["scale"].click()
    point = viewport.mapFromScene(QPointF(0, 0))
    QTest.mouseClick(viewport.viewport(), Qt.LeftButton, pos=point)
    app.processEvents()
    assert document.entities[0].data["start"] == pytest.approx((0.0, -200.0))
    assert document.entities[0].data["end"] == pytest.approx((0.0, 200.0))

    workspace.tool_buttons["undo"].click()
    app.processEvents()
    assert document.entities[0] == rotated


def test_cad_and_selection_reflow_at_compact_and_desktop_widths(app):
    root = Path(__file__).resolve().parents[1]
    cad = _track(app, CadWorkspace(root))
    cad.show()
    cad.resize(650, 600)
    app.processEvents()
    assert not cad.workspace_title.isVisible()
    assert cad.tool_buttons["rotate"].text() == ""
    cad.resize(1200, 700)
    app.processEvents()
    assert cad.tool_buttons["rotate"].text() == "Rotate"

    selection = _track(app, SelectionWorkspace())
    selection.show()
    selection.resize(720, 600)
    app.processEvents()
    assert selection._compact_filters is True
    selection.resize(1200, 700)
    app.processEvents()
    assert selection._compact_filters is False
    cad.close()
    selection.close()
