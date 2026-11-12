"""Qt-independent layout helpers for the main window (verbatim from main.py)."""


# Keep the data-building order separate from the user-facing Results layout.
_RESULTS_TABLE_BASE_COLUMNS = (
    "Category", "Series", "System Model", "Indoor Unit", "Outdoor Unit",
    "Cooling (Btu/hr)", "Cooling (Tons)", "Cooling (kW)", "Excess %",
    "Airflow (CFM)", "CFM Cover %", "Sensible", "EER", "Type", "Power Supply",
)
_RESULTS_TABLE_COLUMNS = (
    "Category", "Series", "System Model", "Cooling (Btu/hr)", "Excess %",
    "Airflow (CFM)", "CFM Cover %", "EER", "Sensible", "Cooling (Tons)",
    "Cooling (kW)", "Indoor Unit", "Outdoor Unit", "Type", "Power Supply",
)
_RESULTS_TABLE_COLUMN_ORDER = tuple(
    _RESULTS_TABLE_BASE_COLUMNS.index(name) for name in _RESULTS_TABLE_COLUMNS
)


def _reorder_results_table_cells(cells):
    """Reorder base-order cell specs to match the visible Results headers."""
    if len(cells) != len(_RESULTS_TABLE_COLUMN_ORDER):
        raise ValueError(
            "Results cell count must match the Results table column count."
        )
    return [cells[index] for index in _RESULTS_TABLE_COLUMN_ORDER]


def _window_sizes_for_available_geometry(width: int, height: int):
    """Return (width, height, min_width, min_height) in Qt logical pixels."""
    if width <= 0 or height <= 0:
        raise ValueError("Available screen dimensions must be positive.")

    target_width = max(1, min(1300, int(width * 0.95)))
    target_height = max(1, min(820, int(height * 0.92)))
    min_width = min(850, target_width)
    min_height = min(560, target_height)
    return target_width, target_height, min_width, min_height
