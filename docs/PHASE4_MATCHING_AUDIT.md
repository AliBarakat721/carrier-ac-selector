# Phase 4 — Audit of the room-to-system matching flow

This document records the state of the code **before** Phase 4 changed anything.
It is the evidence base for the design in `PHASE4_ROOM_SYSTEM_MATCHING.md`.

## 1. Entry point

`cad/workflow.py::suggest_room_assignments(rooms, systems) -> dict[str, str]`

It is the only function that decides which HAP system a detected room belongs to.
It is called from two places in `main.py`:

* `_apply_cad_review()` — after the room-review dialog is applied.
* `_refresh_cad_workflow()` — after a HAP report is parsed or a selection changes.

Both callers use `dict.setdefault(...)`, so a user's manual choice is preserved,
but the *automatic* suggestion itself is never questioned.

## 2. The current algorithm (verbatim behaviour)

```python
system_names = [_text(getattr(system, "name", system)) for system in systems]
result = {}
unused = list(system_names)
for room in rooms:
    label = _key(room.label)
    if not label:
        continue
    match = next((name for name in unused if _key(name) == label), None)
    if match:
        result[room.room_id] = match
        unused.remove(match)
remaining_rooms = [room for room in rooms if room.room_id not in result]
if len(remaining_rooms) == len(unused):
    for room, system_name in zip(remaining_rooms, unused):
        result[room.room_id] = system_name
```

## 3. Positional / implicit fallbacks found

| # | Location | Fallback | Consequence |
|---|---|---|---|
| F1 | `workflow.py:61-64` | If the number of *remaining* rooms equals the number of *unused* systems, they are paired with `zip()` in drawing order and report order. | A room inherits a system purely because it is the *n*-th unmatched room. This is the R1 defect. |
| F2 | `workflow.py:57` | `next((name for name in unused if ...), None)` | When two systems share a name, the **first** one is silently chosen. Ambiguity is resolved by list order. |
| F3 | `workflow.py:60` | `unused.remove(match)` | Removes by *value*; with duplicate system names only one occurrence is removed, so a second room can consume the second duplicate without any warning. |
| F4 | `workflow.py:55-56` | `if not label: continue` | An unlabelled room is skipped, then F1 may still assign it positionally. |
| F5 | `_key()` `re.sub(r"[^a-z0-9]+", "", casefold())` | **All Arabic characters are deleted.** | `"غرفة 1"` becomes `""`, so any Arabic room label can never match an Arabic system name. Arabic matching is effectively broken. |
| F6 | `_key()` | All separators are deleted, including spaces. | `"A B"` and `"AB"` collapse to the same key — two genuinely different names can be merged. |
| F7 | return type `dict[str, str]` | No status, no reason, no record of unmatched rooms or unused systems. | The caller cannot distinguish "matched" from "guessed", and cannot show the user why a room is blank. |

## 4. What the current code does with the acceptance-criteria cases

| Case | Current behaviour | Correct? |
|---|---|---|
| Exact name match | matched | yes |
| Match after normalisation | matched for Latin only; **Arabic never matches** (F5) | no |
| Room has no name | skipped, then possibly paired positionally (F1) | no |
| No system with that name | left blank **unless** counts happen to be equal, then paired (F1) | no |
| Two rooms with the same name | first wins, second may be paired positionally (F1/F2) | no |
| Two systems with the same name | first wins silently (F2/F3) | no |
| Different room/system order | **changes the result** (F1) | no |

## 5. Why `casefold()` + whitespace stripping is not enough

* `casefold()` is Latin-oriented. It does not fold `أ/إ/آ → ا`, `ة → ه`, `ى → ي`,
  `ؤ → و`, `ئ → ي`, does not remove Arabic diacritics or tatweel, and does not
  convert Arabic-Indic digits (`٠١٢…`) to ASCII digits.
* Stripping *all* non-alphanumerics deletes Arabic entirely (F5) and merges
  distinct names that differ only by a space (F6).

Phase 4 therefore replaces `_key()` with an explicit, documented, tested
`normalize_name()` and removes F1–F4 entirely.

## 6. Out of scope (unchanged by Phase 4)

* CAD geometry, room detection, and the review session (`cad/space_detector.py`,
  `cad/room_review.py`).
* HAP extraction and equipment selection (`core.py`).
* Project Management and Project Database (explicitly excluded by the user).
