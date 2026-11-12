import sys

import core
import pytest
from PySide6.QtWidgets import QApplication

from ui.selection_workspace import SelectionWorkspace


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def test_selection_workspace_loads_catalog_and_filters(qapp):
    catalog = core.load_database(core.find_database("."))
    workspace = SelectionWorkspace()
    workspace.set_catalog(catalog, {})

    assert workspace.table.rowCount() == len(catalog)
    assert workspace.lbl_models.text() == str(len(catalog))
    assert int(workspace.lbl_categories.text()) >= 1
    assert int(workspace.lbl_series.text()) >= 1

    workspace.category.setCurrentText("High Wall")
    assert workspace.table.rowCount() > 0
    assert workspace.table.rowCount() < len(catalog)
    assert all(
        workspace.table.item(row, 0).text() == "High Wall"
        for row in range(workspace.table.rowCount())
    )


def test_selection_workspace_emits_catalog_row_and_system_name(qapp):
    catalog = core.load_database(core.find_database("."))
    workspace = SelectionWorkspace()
    workspace.set_catalog(catalog, {})
    workspace.system_name.setText("AHU-01")
    workspace.table.selectRow(0)

    received = []
    workspace.model_requested.connect(lambda row, name: received.append((row, name)))
    workspace._emit_selected_model()

    assert len(received) == 1
    row, name = received[0]
    assert name == "AHU-01"
    assert row["System Model"] == catalog.iloc[0]["System Model"]
