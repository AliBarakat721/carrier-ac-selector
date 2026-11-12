# Phase 3 — CAD Layer Analysis & Room Detection (increment 1)

**Scope of this increment:** layer classification + room-candidate review rules.
**Out of scope of this increment:** wiring the new review flow into the Qt
interface, and auto-detecting rooms from wall linework with door/window gaps
(the existing gap-snapping engine is reused unchanged).

---

## 1. The defect being fixed

The review recorded that a **furniture circle of 0.5 m² was reported as a room**.
The cause was structural, not a tuning problem: `detect_spaces` treats *every*
closed polyline, circle and closed spline as a room boundary, and it has no
concept of what a layer is for. The only defence was an optional `wall_layers`
argument that the caller had to supply by hand.

Area alone cannot fix this: a 1.2 m² riser shaft is a real room, and a 6 m²
furniture block is not. The fix therefore uses **five independent signals**:

| Signal | Question | Failure mode if ignored |
|---|---|---|
| Layer class | Is this layer furniture / door / window / column / dimension / text? | furniture becomes a room |
| Geometry validity | Is the polygon self-intersecting or empty? | garbage areas |
| Area plausibility | Is it below an explicit, user-set minimum? | 0.5 m² "rooms" |
| Drawing units | Is `$INSUNITS` present and understood? | m² computed from millimetres |
| Label + review status | Has a human confirmed it? | silent promotion to "room" |

---

## 2. What was added

### 2.1 `cad/layer_analyzer.py` (new module)

* `LayerClass` — `WALLS`, `ROOM_BOUNDARIES`, `DOORS`, `WINDOWS`, `COLUMNS`,
  `FURNITURE`, `DIMENSIONS`, `TEXT_LABELS`, `OTHER`.
* `classify_layer(name)` / `classify_layers(names)` — token-based lexical
  classification with English **and** Arabic keywords, Arabic letter folding
  (`أ/إ/آ → ا`, `ة → ه`, `ى → ي`) and diacritic removal.
* `LayerClassification` — carries `confidence` (`HIGH`/`MEDIUM`/`LOW`),
  `method`, `matched_keywords` and **`alternatives`**. A name like `ROOM-WALL`
  scores equally for two classes; instead of silently picking one, the result
  lists the other and sets `needs_user_confirmation = True`.
* `LayerMapping` — holds classifications plus `overrides`. `overrides` is the
  only state a future project file would need to store: a plain
  `{layer_name: "WALLS"}` dict. This is the deliberate link point to the
  excluded Project Database.
* `evaluate_room_candidate(...)` — rule engine returning
  `RoomCandidateDecision(status, reasons, blocking)` with stable reason codes
  (`LAYER_CLASS_NOT_A_ROOM:FURNITURE`, `AREA_BELOW_MINIMUM:0.500<1.000`,
  `DRAWING_UNITS_UNVERIFIED`, `NO_ROOM_LABEL`, `LAYER_CLASS_AMBIGUOUS`, …).

### 2.2 `cad/space_detector.py` (additive)

* `RoomSpace` gained `layer_class`, `candidate_status`, `candidate_reasons`,
  `boundary_layers`, plus `effective_layer` and `is_usable_room` properties.
  All defaults are neutral, so the existing API is unchanged.
* `RoomDetectionReport` — keeps **every** detected polygon, including rejected
  ones, with `accepted` / `needs_review` / `rejected` views, `summary()` and
  `reasons_table()` for a Qt table model.
* `detect_spaces_with_review(...)` — the Phase 3 entry point:
  * wall polygonization runs **only** on layers classified as `WALLS` or
    `ROOM_BOUNDARIES`;
  * if no such layer exists, wall-line detection is **skipped** and a
    `NO_WALL_LAYER_IDENTIFIED` diagnostic is recorded — the drawing is no
    longer polygonized wholesale;
  * labels are read only from `TEXT_LABELS` layers (with a diagnostic when none
    exists);
  * every polygon is evaluated and stamped with a status and reasons;
  * `min_area_m2` (default 1.0), `max_area_m2`, `require_label`,
    `allow_circular_curves` and `layer_overrides` are explicit parameters.
* Wall-line rooms now expose `boundary_layers` (most frequent first) instead of
  a `None` layer, without changing the existing `layer` contract.

### 2.3 `cad/dxf_exporter.py` (Phase 0 item 6)

Export to the source path is now refused unless the loaded drawing already
contains HVAC tag blocks (i.e. it is itself an export artifact). This closes R7
without breaking the documented idempotent re-export flow.

---

## 3. Behaviour on the recorded defect

A drawing containing a real 5 × 4 m room on `A-WALL` (labelled `Office`) and a
0.5 m² furniture circle on `A-FURN`:

```python
report = detect_spaces_with_review("plan.dxf")
report.summary()
# {'CONFIRMED': 1, 'NEEDS_REVIEW': 0, 'REJECTED': 1, 'total': 2}

report.rejected[0].layer_class        # 'FURNITURE'
report.rejected[0].candidate_reasons  # ('LAYER_CLASS_NOT_A_ROOM:FURNITURE',)
report.accepted[0].label              # 'Office'
report.accepted[0].area               # 20.0
```

The furniture polygon is still **visible** in `report.rooms` and in
`report.reasons_table()`; it is rejected, not hidden. A user can override the
classification and the decision is re-evaluated.

---

## 4. Tests added

| File | Tests | Covers |
|---|---|---|
| `tests/test_layer_analyzer.py` | 35 | English/Arabic classification, ambiguity, overrides, layer sets, serialisable rows |
| `tests/test_room_candidate_review.py` | 23 | every rule branch + real DXF fixtures (furniture circle, unverified units, no wall layer, override, label requirement, dedup) |
| `tests/test_baseline_contracts.py` | 15 (10 pass, 5 xfail) | R1–R7 regression locks |

Full suite: **222 passed, 5 xfailed** (154 original tests untouched).

---

## 5. Deliberate limitations (carried into later phases)

1. **Classification is lexical.** `Layer1` and `0` are reported as `OTHER` with
   `LOW` confidence so the user is asked. No geometric inference is attempted.
2. **The Qt interface still calls `detect_spaces`.** Wiring the review table,
   the layer-mapping editor and the "confirm rooms" gate into `main.py` is the
   next Phase 3 increment; the engine is ready and independent of Qt.
3. **Door/window gaps are not bridged automatically.** `gap_tolerance` remains
   opt-in; bridging openings changes room areas and must be a visible,
   reversible user action.
4. **No room is auto-promoted to `CONFIRMED`** when units are unverified or the
   label is missing. This is intentional: engineering use requires both.
5. **`SENSIBLE_RATIO`, `zip()` matching, block geometry, placement spread and
   ROI validation are untouched** — they belong to Phases 4, 6, 8, 9 and 13 and
   are locked by `xfail(strict=True)` tests so they cannot be forgotten.
