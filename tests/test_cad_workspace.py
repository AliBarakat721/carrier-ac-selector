from pathlib import Path
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.cad_workspace import CadWorkspace, _icon  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_cad_toolbar_uses_repository_icons_and_undo_icon(app):
    root = Path(__file__).resolve().parents[1]
    workspace = CadWorkspace(root)
    try:
        expected = {
            "fit_to_page", "fileopen", "export", "line_2p", "line_rectangle",
            "circle_center_point", "move_copy", "move_rotate", "scale",
            "delete", "undo", "cursor",
        }
        assert all(not _icon(root, name).isNull() for name in expected)
        assert not workspace.tool_buttons["undo"].icon().isNull()
        assert workspace.tool_buttons["undo"].property("icon_name") == "undo"
    finally:
        workspace.close()
