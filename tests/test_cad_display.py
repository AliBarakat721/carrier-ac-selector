from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ezdxf
import pytest
from shapely.geometry import Polygon

pytest.importorskip("PySide6")
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication, QGraphicsSimpleTextItem

from cad.dxf_reader import read_dxf
from ui.cad_workspace import CadWorkspace


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application
    for widget in list(application.topLevelWidgets()):
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    application.processEvents()


@pytest.mark.parametrize(
    ("scale", "unit_code", "text_height"),
    [(1.0, 6, 0.2), (1000.0, 4, 200.0)],
)
def test_architectural_plan_fits_proportionally_and_preserves_annotation(
    tmp_path, app, scale, unit_code, text_height
):
    source = tmp_path / f"architectural-{unit_code}.dxf"
    drawing = ezdxf.new("R2018")
    drawing.header["$INSUNITS"] = unit_code
    msp = drawing.modelspace()
    msp.add_lwpolyline(
        [(0, 0), (15 * scale, 0), (15 * scale, 12 * scale), (0, 12 * scale)],
        close=True,
        dxfattribs={"layer": "A-WALL"},
    )
    msp.add_text(
        "Office",
        height=text_height,
        dxfattribs={"layer": "A-ANNO-TEXT", "rotation": 30},
    ).set_placement((4 * scale, 5 * scale))
    drawing.saveas(source)

    document = read_dxf(source)
    text_entity = document.entities_of_type("TEXT")[0]
    assert text_entity.data["height"] == pytest.approx(text_height)
    assert text_entity.data["rotation"] == pytest.approx(30)

    room = SimpleNamespace(
        room_id="ROOM-1",
        label="Office",
        polygon=Polygon([(0, 0), (15 * scale, 0), (15 * scale, 12 * scale), (0, 12 * scale)]),
    )
    workspace = CadWorkspace(Path(__file__).resolve().parents[1])
    workspace.resize(1100, 720)
    workspace.show()
    app.processEvents()
    workspace.viewport.render_document(document, [room])
    app.processEvents()

    text_items = [
        item for item in workspace.viewport.scene.items()
        if isinstance(item, QGraphicsSimpleTextItem)
    ]
    # The source DXF annotation is shown once; the detected-room overlay must
    # not paint a second identical name on top of it.
    assert len(text_items) == 1
    rendered_text = text_items[0]
    assert rendered_text.data(0) is text_entity
    assert rendered_text.rotation() == pytest.approx(-30)
    assert rendered_text.boundingRect().height() * rendered_text.scale() == pytest.approx(text_height, rel=0.02)

    # A fixed 80 drawing-unit border makes a 15 m plan occupy only a small
    # fraction of the canvas. The proportional fit should keep it prominent.
    assert workspace.viewport.transform().m11() * (15 * scale) > workspace.viewport.viewport().width() * 0.65
    workspace.close()
    workspace.deleteLater()
    app.processEvents()
