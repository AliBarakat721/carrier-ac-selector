# Architecture (Phase 1)

Goal: separate the GUI from business logic, step by step, without changing results.

## Dependency direction

```
main.py (PySide6 GUI)
   │
   ├── core.py  (compat facade) ──► engineering/  equipment/  hap/
   ├── reports/  (export helpers)  ──► engineering/
   ├── ui/layout.py (pure layout maths)
   └── cad/, electrical_engine.py, roi_tab.py, ocr_engine.py
```

* Logic packages (`engineering`, `equipment`, `hap`, `reports`, `ui`, `cad`) never import
  Qt, `main`, or `core`. `tests/test_architecture.py` enforces this.
* `core.py` only re-exports. Existing `import core; core.select_units(...)` keeps working.
* `main.py` keeps the old private names (`_atomic_export`, `_style_excel_file`, ...)
  as aliases, so existing tests and callers are unaffected.

## What moved (code moved verbatim; no behaviour change)

| From | To |
|---|---|
| `core.to_float`, `parse_user_number`, `BTU_PER_*` | `engineering/units.py` |
| `core.load_database`, `find_database`, `EXPECTED_COLUMNS` | `equipment/catalog_reader.py` |
| `core.select_units`, `multi_unit_options`, `batch_select`, helpers | `equipment/indoor_selector.py` |
| `core.HapResult`, `parse_hap_text`, `SystemLoad`, `parse_all_systems` | `hap/parser.py` |
| `main._atomic_export`, `_replace_export_extension`, `_export_error_message` | `reports/file_io.py` |
| `main._style_excel_file` | `reports/excel_export.py` |
| `main.append_electrical_*` | `reports/electrical_report.py` |
| `HAPToCarrierApp._prepare_export_df`, `_prepare_cart_export_df` | `reports/export_frames.py` |
| `HAPToCarrierApp._normalize_save_path`, `_default_export_name` | `reports/file_io.py` |
| `HAPToCarrierApp._best_overall_index`, rules inside `_pick_best` | `equipment/best_pick.py` |
| results-table column order, window sizing | `ui/layout.py` |

## Packaging note

The in-app updater and `apply_update.ps1` allow-list files per package. When adding a module
to a shipped package, add it to `updater.ALLOWED_PACKAGE_FILES` **and** `$allowedPackages`
in `apply_update.ps1` (a test fails if `updater.py` and the files on disk disagree).

## Still to do in later passes

* Word report builders (`_build_word_report`, `_build_multi_word_report`, `_build_batch_word_report`,
  `_build_cart_word_report`) still live in `main.py`; they are large and duplicated.
* `cad/` is not yet split into reader / modifier / exporter packages (`importer`, `layer_analyzer`,
  `room_matcher`, `placement/` from the plan do not exist yet).
* Interfaces are currently plain functions + dataclasses; formal Protocols come with Phase 4+.
