import pytest

from main import (
    _RESULTS_TABLE_BASE_COLUMNS,
    _RESULTS_TABLE_COLUMNS,
    _reorder_results_table_cells,
)


def test_results_table_prioritizes_selection_metrics():
    assert _RESULTS_TABLE_COLUMNS == (
        "Category", "Series", "System Model", "Cooling (Btu/hr)",
        "Excess %", "Airflow (CFM)", "CFM Cover %", "EER", "Sensible",
        "Cooling (Tons)", "Cooling (kW)", "Indoor Unit", "Outdoor Unit",
        "Type", "Power Supply",
    )


def test_results_table_keeps_system_model_at_column_two():
    assert _RESULTS_TABLE_COLUMNS[2] == "System Model"
    assert _RESULTS_TABLE_COLUMNS[0] == "Category"


def test_results_cell_specs_follow_the_display_headers():
    base_cells = [f"cell:{column}" for column in _RESULTS_TABLE_BASE_COLUMNS]

    reordered = _reorder_results_table_cells(base_cells)

    assert reordered == [f"cell:{column}" for column in _RESULTS_TABLE_COLUMNS]


def test_results_cell_reordering_rejects_incomplete_rows():
    with pytest.raises(ValueError, match="cell count"):
        _reorder_results_table_cells(["only one cell"])
