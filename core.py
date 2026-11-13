"""Backward-compatible facade for the Carrier AC Selector logic (no Qt imports).

Phase 1 (architecture) moved the implementation into focused modules:

* ``engineering.units``            - unit constants and tolerant number parsing
* ``equipment.catalog_reader``     - catalog (database) loading
* ``equipment.indoor_selector``    - single / multi-unit / batch selection rules
* ``hap.parser``                   - HAP report text parsing

Every public (and legacy private) name is re-exported here, so existing
``import core`` / ``core.select_units(...)`` callers keep working unchanged.
New code should import from the focused modules directly.
"""
from __future__ import annotations

from engineering.units import (  # noqa: F401
    BTU_PER_KW,
    BTU_PER_TON,
    _NUMERIC_NA,
    parse_user_number,
    to_float,
)
from equipment.catalog_reader import (  # noqa: F401
    EXPECTED_COLUMNS,
    find_database,
    load_database,
)
from equipment.indoor_selector import (  # noqa: F401
    MAX_MULTI_UNITS,
    SENSIBLE_RATIO,
    _apply_category_type,
    _validate_selection_inputs,
    batch_select,
    cover_factor,
    multi_unit_options,
    select_units,
)
from hap.parser import (  # noqa: F401
    HapResult,
    SystemLoad,
    _LEADER,
    _NUM,
    _SYSTEM_HEADER,
    _SYSTEM_HEADER_LENIENT,
    _SYSTEM_SUMMARY_HEADER,
    _SYSTEM_SUMMARY_HEADER_LENIENT,
    _cooling_section,
    _normalize_rtf_ws,
    _parse_hap_chunk,
    parse_all_systems,
    parse_hap_text,
    parse_hap_text_reconciled,
    SourceRef, ExtractedValue, ThermalValidation, HapRoom,
    parse_hap_room, parse_hap_rooms, thermal_validate,
)
