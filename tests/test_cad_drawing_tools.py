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
from cad.dxf_exporter import apply_cad_document_edits
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


def test_geometry_changed_signal_emits_the_edited_document(app):
    workspace = _workspace(app)
    document = CadDocument(Path("sample.dxf"), "meters", 6, ["0"], [])
    changes = []
    workspace.geometry_changed.connect(changes.append)
    workspace.set_project_data(document, [])

    workspace._add_entity(
        CadEntity("LINE", "MANUS_DRAWN", None, {"start": (0, 0), "end": (10, 0)})
    )
    app.processEvents()

    assert changes
    assert changes[-1] is document


def test_in_memory_cad_edits_are_applied_to_export_document(tmp_path):
    import ezdxf

    source = tmp_path / "source.dxf"
    drawing = ezdxf.new("R2018")
    drawing.layers.new("ORIGINAL")
    line = drawing.modelspace().add_line(
        (0, 0), (1, 0), dxfattribs={"layer": "ORIGINAL"}
    )
    drawing.saveas(source)

    edited = CadDocument(
        source,
        "meters",
        6,
        ["ORIGINAL", "MANUS_DRAWN"],
        [
            CadEntity(
                "LINE",
                "ORIGINAL",
                line.dxf.handle,
                {"start": (2, 3), "end": (4, 3)},
            ),
            CadEntity(
                "CIRCLE",
                "MANUS_DRAWN",
                None,
                {"center": (8, 8), "radius": 2},
            ),
        ],
    )

    raw = ezdxf.readfile(source)
    apply_cad_document_edits(raw, edited)
    lines = list(raw.modelspace().query("LINE"))
    circles = list(raw.modelspace().query("CIRCLE"))
    assert tuple(lines[0].dxf.start)[:2] == (2, 3)
    assert tuple(lines[0].dxf.end)[:2] == (4, 3)
    assert tuple(circles[0].dxf.center)[:2] == (8, 8)
