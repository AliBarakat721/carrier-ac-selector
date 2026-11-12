# Current Behaviour — Baseline Recorded Before Any Behavioural Change

| Item | Value |
|---|---|
| Repository | `AliBarakat721/carrier-ac-selector` |
| Branch | `main` @ `10b7406` |
| App version | `2.0.1` |
| Baseline recorded on | 2026-11-12 |
| Branch for this work | `fix/phase0-baseline-and-cad-room-detection` |

This document is the Phase 0 deliverable: **what the program does today**, so
that every later phase can prove it did not change something silently.

---

## 1. Measured baseline

| Check | Command | Result |
|---|---|---|
| Test suite | `python3 -m pytest -q` | **154 passed in 2.53 s** (`docs/baseline/pytest_baseline.txt`) |
| Byte-compilation | `python3 -m compileall -q .` | exit code 0 (`docs/baseline/compileall_baseline.txt`) |
| Largest module | `main.py` | 188,516 bytes / 4,307 lines |
| CAD package | `cad/*.py` | 2,022 lines across 10 modules |
| Equipment catalog | `data/Carrier_AC_Database.xlsx` | 31 model rows, 35 columns, 443 change-log rows |

After this branch: **222 passed, 5 xfailed** — the 154 original tests are
untouched and still pass.

---

## 2. Module map (business logic vs. GUI)

| Layer | Files | Notes |
|---|---|---|
| GUI only | `main.py` (4,307 lines), `roi_tab.py` (Qt widget) | `main.py` also contains orchestration and inline business rules |
| HAP / selection | `core.py` | `SENSIBLE_RATIO = 0.75` at line 20, used at line 406 |
| OCR / PDF | `ocr_engine.py` | pytesseract + pymupdf, PSM 4/6/11 |
| Electrical | `electrical_engine.py` | IEC 60364-5-52 ampacity, MCB sizing, voltage drop |
| CAD import | `cad/importer.py`, `cad/dxf_reader.py` | DWG via ODA File Converter, DXF via ezdxf |
| CAD geometry | `cad/space_detector.py` | polygonize, gap snapping, T-junction splitting, dedup |
| CAD layers | `cad/layer_manager.py`, `cad/tag_config.py` | 8 standard HVAC layers, 4 tag block types |
| CAD blocks | `cad/block_manager.py` | generic 1.0 × 0.5 symbol rectangle |
| CAD workflow | `cad/workflow.py` | room→system suggestion, indoor placement |
| CAD export | `cad/dxf_exporter.py` | atomic replace, backup, rollback, round-trip verify |
| Scheduling | `cad/schedule_builder.py` | 14 schedule columns |
| Updater | `updater.py` | SHA-256 verified self-update |

`main.py` imports are already cleanly separated (`core`, `ocr_engine`,
`updater`, `cad.*`), which is why Phase 1 can be incremental instead of a
rewrite.

---

## 3. Known defects locked by `tests/test_baseline_contracts.py`

Each defect has two tests: a **characterisation test** that passes today and
records the current behaviour, and a **desired-behaviour test** marked
`xfail(strict=True)` where the fix is not in this branch.

| ID | Defect | Current behaviour | Status in this branch |
|---|---|---|---|
| R1 | Room ↔ HAP system matching by drawing order | `zip()` pairs leftover rooms and systems when counts are equal (`cad/workflow.py:61-64`) | Locked, `xfail` → Phase 4 |
| R2 | Furniture closed curve becomes a room | legacy `detect_spaces` accepts any closed curve; 0.5 m² furniture circle reported as a room | **Fixed** in `detect_spaces_with_review` **and enforced in the GUI** via `RoomReviewSession` + `RoomReviewDialog`; legacy API unchanged and characterised |
| R3 | Negative / non-finite electrical input | `NaN`/`inf` raise `ValueError`; **negative capacity silently falls back to 1.5 kW** | Partially fixed, `xfail` → Phase 11 |
| R4 | Illogical ROI inputs | `saving_pct=150` produces negative inverter kW and impossible savings | Locked, `xfail` → Phase 13 |
| R5 | Block geometry ignores model dimensions | every block is a fixed 1.0 × 0.5 rectangle | Locked, `xfail` → Phase 8 |
| R6 | Multi-unit placement overlap | units are spread on a fixed 0.25-unit radius, so two 1.05 m units sit 0.5 m apart | Locked, `xfail` → Phase 9 |
| R7 | Export over the source drawing | no guard: exporting to the source path overwrote the original drawing | **Fixed** in this branch |

### R7 detail — the rule now enforced

`export_hvac_dxf` refuses to write when the destination resolves to the source
file **and** the loaded drawing does not already contain HVAC tag blocks. A
file that already carries `HVAC_INDOOR_UNIT` / `HVAC_OUTDOOR_UNIT` /
`HVAC_PIPE_LABEL` / `HVAC_ROOM_LABEL` inserts is an export artifact, so
re-exporting onto it stays allowed (idempotent update, covered by
`tests/test_dxf_exporter.py`).

---

## 4. Behaviours that must not change without an explicit decision

1. `detect_spaces(...)` signature and results — it is the public CAD API used by
   24 existing tests and by `detect_spaces_with_review`. The **GUI no longer
   calls it directly**: `main.py` goes through `RoomReviewSession`, and
   `tests/test_room_review_dialog.py` fails if that regresses.
2. `RoomSpace.layer` stays `None` for `wall_lines` rooms; the contributing
   layers are exposed separately as `boundary_layers` and `effective_layer`.
3. Export remains atomic: temp file → round-trip verify → backup → `os.replace`
   → rollback on failure.
4. Standard HVAC layer names in `CAD_LAYERS` and tag field names in
   `TAG_CONFIG` are a DXF data contract.
5. `SENSIBLE_RATIO = 0.75` still exists and is still used; Phase 6 removes it.

---

## 5. Environment caveats

* PySide6 cannot open a real window in this sandbox. GUI tests import only pure
  helpers from `main.py` and run with `QT_QPA_PLATFORM=offscreen`. The review
  dialog is exercised headlessly the same way (`tests/test_room_review_dialog.py`).
* DWG import requires the external ODA File Converter; DXF paths are fully
  testable without it.
* The equipment catalog is read from `data/Carrier_AC_Database.xlsx` and is
  **not** assumed complete or approved (Phase 5).

---

## 6. How to reproduce

```bash
python3 -m pip install -q -r requirements.txt -r requirements-dev.txt
python3 -m pytest -q --timeout=180
python3 -m compileall -q .
```
