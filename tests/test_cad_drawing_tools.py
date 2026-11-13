from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from cad.dxf_reader import CadDocument, CadEntity
from ui.cad_workspace import CadWorkspace

_TEST_WORKSPACES = []


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    _TEST_WORKSPACES.clear()
    yield application
    for workspace in reversed(_TEST_WORKSPACES):
        workspace.close()
        workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    application.processEvents()
    _TEST_WORKSPACES.clear()


def _workspace(app):
    workspace = CadWorkspace(Path(__file__).resolve().parents[1])
    _TEST_WORKSPACES.append(workspace)
    return workspace


@pytest.mark.parametrize(
    ("mode", "expected_type"),
    [("line", "LINE"), ("rectangle", "LWPOLYLINE"), ("circle", "CIRCLE")],
)
def test_mouse_drawing_preview_commits_geometry_to_canvas(app, mode, expected_type):
    workspace = _workspace(app)
    document = CadDocument(Path("sample.dxf"), "meters", 6, ["0"], [])
    workspace.set_project_data(document, [])
    workspace.resize(1000, 650)
    workspace.show()
    app.processEvents()

    viewport = workspace.viewport
    viewport.setSceneRect(-500, -350, 1000, 700)
    viewport.fitInView(viewport.sceneRect(), Qt.KeepAspectRatio)
    app.processEvents()
    workspace._activate_tool(mode)

    start = viewport.mapFromScene(QPointF(-100, 100))
    end = viewport.mapFromScene(QPointF(100, -100))
    QTest.mouseClick(viewport.viewport(), Qt.LeftButton, pos=start)
    app.processEvents()
    assert viewport._preview_item is not None
    assert not viewport._preview_item.path().isEmpty()

    QTest.mouseMove(viewport.viewport(), end)
    app.processEvents()
    assert viewport._preview_item is not None
    assert not viewport._preview_item.path().isEmpty()

    QTest.mouseClick(viewport.viewport(), Qt.LeftButton, pos=end)
    app.processEvents()
    assert viewport._preview_item is None
    assert document.entities[-1].entity_type == expected_type
    assert any(item.data(0) is document.entities[-1] for item in viewport.scene.items())


def test_undo_button_immediately_removes_last_drawn_shape(app):
    workspace = _workspace(app)
    document = CadDocument(Path("sample.dxf"), "meters", 6, ["0"], [])
    workspace.set_project_data(document, [])
    workspace._add_entity(
        CadEntity("LINE", "MANUS_DRAWN", None, {"start": (0, 0), "end": (10, 0)})
    )

    workspace.tool_buttons["undo"].click()
    app.processEvents()
    assert document.entities == []
