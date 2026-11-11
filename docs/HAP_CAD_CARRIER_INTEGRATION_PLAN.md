# HAP + CAD + Carrier Integration Plan

## Scope and decisions

- Unit scope: all Carrier indoor-unit categories represented in the catalogue.
- Placement workflow: automatic suggestions followed by user review and manual adjustment.
- Development base: current main commit at branch creation. Work stays on feature/hap-cad-carrier-integration; no changes are made to main and no PR is merged as part of implementation.
- DXF is the initial CAD exchange format. DWG conversion is a separate future adapter.

## Existing application boundaries

- core.py owns catalogue loading, HAP text parsing, system-level parsing, and Carrier selection.
- ocr_engine.py extracts text from supported HAP documents.
- cad/ owns DXF reading, candidate-space detection, block attributes, DXF export, and schedule generation.
- main.py owns the PySide6 desktop UI.
- The existing HAP parser returns air-system coil load/airflow records. These are not automatically room-level loads.
- Existing room detection returns candidate geometry and verification status. Candidate polygons are not automatically approved room boundaries.

Integration must use adapters and preserve existing engineering calculations rather than duplicate or silently alter them.

## Target workflow

1. Import a DXF and a HAP report (PDF/RTF/image formats supported by the existing OCR layer).
2. Detect candidate spaces and display drawing units, geometry diagnostics, and review status.
3. Parse every HAP air system and preserve load, airflow, sensible load, and warnings.
4. Match names/IDs explicitly. Never distribute an air-system total across rooms by area or any other undocumented heuristic.
5. Select Carrier indoor units only when required load/airflow data is valid and the catalogue row has sufficient verified data.
6. Generate automatic placement proposals using room geometry, unit footprint/orientation, mounting rules, obstacles, and manufacturer/service clearances.
7. Let the user accept, reject, or move proposals; revalidate every manual move.
8. Export a new DXF (never overwrite the source by default), equipment schedule, and project reports.

## Phased implementation and exit gates

| Phase | Deliverable | Required tests / exit gate |
|---|---|---|
| 0. Baseline | Record base SHA, PR #9 separation, current tests and dependency boundaries | Existing CI baseline recorded; no main mutation |
| 1. Integration foundation | Typed project records, explicit room/system matching, pipeline orchestration | Pure unit tests; unknown and ambiguous matches remain unresolved |
| 2. CAD + HAP import | DXF/HAP import adapters, dynamic room collection, diagnostics | DXF fixtures with varied room counts; HAP multi-system fixtures; malformed input |
| 3. Carrier selection | Adapter to core batch_select / select_units | Catalogue compatibility, load/airflow validation, no room-level inference |
| 4. Automatic placement | Geometry-based proposals and explicit placement statuses | Polygon containment, footprint, clearances, obstacles, invalid geometry, no-valid-position |
| 5. Manual adjustment | Preview model, move/rotate/revalidate and save | Valid/invalid move, undo/reopen persistence, stable IDs |
| 6. Export | DXF round-trip and schedule/report generation | Source preservation, entity uniqueness, schedule consistency, rollback |
| 7. Windows distribution | Release packaging, dependency list and updater allowlist | Windows archive includes new packages; path traversal and update rollback tests |

## Engineering safety rules

- A HAP air-system coil load/CFM is a system-level quantity unless the source report explicitly identifies room-level data.
- No proportional allocation by room area, occupancy, or volume without a separately approved engineering method and explicit user confirmation.
- Unknown DXF units block metric footprint/clearance validation until the user confirms units or supplies a conversion.
- Missing manufacturer dimensions, service clearances, mounting constraints, or throw data produce NEEDS_REVIEW; they are not invented.
- AUTO_PLACED means a geometric placement proposal passed implemented geometric checks. It does not prove ADPI, air distribution, acoustics, thermal comfort, code compliance, or engineering approval.
- The original DXF remains unchanged. Exports are written to a new destination and reopened for validation.

## Known prerequisite / PR #9

PR #9 (feature/cad-integration) is a separate CAD geometry-hardening change and is not included in this branch's base. Its ARC/CIRCLE/SPLINE, T-junction, layer filtering, and stable room-ID changes must be reviewed and merged separately or deliberately ported after review. This integration branch must not assume those changes exist in the current main baseline.
