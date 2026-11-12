# Integrated CAD + HAP + Carrier Selection Workflow

## Goal

Deliver one desktop workflow in Carrier AC Selector:

1. Import a DXF floor plan and a HAP report into one project.
2. Detect CAD spaces and let the engineer review/correct their identity and geometry.
3. Parse HAP air-system data and explicitly map CAD spaces to HAP systems.
4. Obtain **room-level** design loads and airflow before selecting a unit for a room.
5. Select compatible Carrier indoor/outdoor equipment from the existing catalogue.
6. Automatically propose indoor-unit positions, allow manual move/rotation, validate each placement, and export a new DXF plus schedules/reports.

The workflow must support any number of spaces. No fixed 15/30-room limit is allowed.

## Critical engineering data rule

The current HAP parser exposes `SystemLoad` records (system name, total coil BTU/h, CFM and sensible BTU/h). A system total is **not** a room load. If one air system serves multiple spaces, its total load/airflow must never be copied to every space or divided by area without an approved, explicit allocation method.

The first integration release therefore supports:
- mapping one or more CAD spaces to an HAP air system;
- room-level load/airflow supplied by a room-level HAP source when available, or entered/confirmed by the engineer;
- blocking room-level selection when only aggregate system data exists.

## Architecture boundaries

- `core.py` remains the source of truth for Carrier catalogue loading and unit selection.
- `ocr_engine.py` remains the source of truth for HAP text extraction.
- `cad.dxf_reader` and `cad.space_detector` remain the source of truth for DXF reading and candidate space geometry.
- `cad.dxf_exporter` remains the source of truth for non-destructive DXF export and round-trip validation.
- A Qt-independent `integration/` application layer owns project state, explicit room/system mapping, validation and orchestration.
- The PySide6 UI calls the application layer; it must not duplicate engineering calculations.
- Automatic placement is a proposal, not engineering approval. Rules must be equipment-family aware and use verified catalogue dimensions, manufacturer clearances, obstacles and room geometry. Unknown dimensions/clearances produce `NEEDS_REVIEW`, never a fabricated placement pass.

## Project data flow

```text
DXF + HAP report + Carrier catalogue
              |
       Input validation
              |
   CAD spaces <-> HAP systems
              |
  Engineer confirms room-level loads
              |
    Carrier selection per room/zone
              |
     Equipment-family placement rules
              |
    Auto proposal <-> Manual adjustment
              |
  Clearance/geometry validation + review
              |
  New DXF + equipment schedule + reports
```

## Phased implementation and acceptance tests

### Phase 1 — Integration contracts
- Add typed project, CAD-space, HAP-system, room-load and selection records.
- Add deterministic room/system mapping with explicit ambiguity and unmatched states.
- Preserve source values and provenance; never infer room loads from system totals.
- Tests: exact-name match, normalized-name match, explicit override, ambiguous match, unmatched room, multiple rooms on one system, and aggregate-only load guard.

### Phase 2 — Project input orchestration
- Load DXF via `read_dxf` / `detect_spaces`.
- Extract HAP text via `ocr_engine.extract_text`; parse all systems via `core.parse_all_systems`.
- Return a reviewable project snapshot and diagnostics without opening the GUI.
- Tests: valid DXF + multi-system HAP fixture, invalid DXF, missing file, empty/unparseable HAP, unknown drawing units.

### Phase 3 — Room-level Carrier selection
- Require explicit room-level BTU/h, CFM and optional sensible load.
- Call `core.load_database` and `core.select_units`; do not reimplement catalogue filtering.
- Retain selection inputs, selected row, source references and warnings.
- Tests: valid selection, no match, invalid/non-finite inputs, missing room load, catalogue compatibility and multi-room scalability.

### Phase 4 — Equipment-family placement engine
- Create a family-specific rule registry for every supported catalogue unit type.
- Require verified symbol/footprint dimensions, installation orientation, service clearances and obstacle geometry.
- Generate candidate placements inside the room polygon; validate footprint containment and clearances.
- Support manual coordinates/rotation through the same validator.
- Tests: cassette and concealed/ducted fixtures, rotated footprint, narrow room, obstacle collision, insufficient clearance, unknown dimensions, invalid geometry, deterministic candidate ordering.

### Phase 5 — DXF/UI integration
- Add a dedicated project workflow screen: Import → Space Review → HAP Mapping → Room Loads → Selection → Placement Review → Export.
- Show CAD preview and room/unit selection state; manual drag/rotate updates the project model.
- Export only approved/reviewed records through `export_hvac_dxf`; original DXF remains unchanged.
- Tests: UI-independent controller tests, stable entity IDs, repeated export without duplicates, DXF reopen/attribute checks.

### Phase 6 — Windows distribution and end-to-end acceptance
- Include `integration/` and `cad/` in Windows release artifacts and secure updater allowlists.
- Test fresh install, update, rollback, and launch on Windows.
- End-to-end fixture: multiple rooms (including 15, 30 and larger generated cases), multi-system HAP, room-level selections, placement review, DXF + CSV/XLSX + Word output.
- Require CI on Ubuntu and Windows, then manual acceptance with a real AutoCAD DXF and real HAP report.

## Safety and engineering statuses

Use explicit statuses such as `READY_FOR_REVIEW`, `NEEDS_REVIEW`, `BLOCKED_MISSING_ROOM_LOAD`, `BLOCKED_UNKNOWN_UNITS`, `PLACEMENT_FAILED`, and `APPROVED_BY_ENGINEER`.

A successful parser, geometry check, catalogue match or block insertion is not proof of code compliance, airflow distribution, ADPI, thermal comfort or an approved HVAC design.

## Non-goals for the first increment

- Native DWG editing (DXF is the first supported exchange format).
- Automatic allocation of system-level HAP loads to rooms without validated source data.
- Claiming automatic placement is a complete duct design or diffuser/air-distribution verification.
