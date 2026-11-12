# Phase 4 — Explicit room-to-system matching

Phase 4 removes the last place where the application guessed an engineering
relationship. Before this phase, `suggest_room_assignments` paired any left-over
rooms with any left-over HAP systems whenever the two counts happened to be
equal, using `zip()` in drawing order and report order. That is the R1 defect.
A room could inherit a system purely because it was the *n*-th unmatched room.

The audit that led to this design is in `PHASE4_MATCHING_AUDIT.md`.

## 1. The rule

A room is matched to a system **only** when their names are equal after an
explicit normalisation, and only when that name is unique on both sides. There
is no positional fallback, no `room_id` heuristic, and no DXF entity order.

The engine is `cad/room_matching.py::match_rooms_to_systems`. It is pure Python
(no Qt, no persistence), so it can be tested headlessly and reused by the GUI,
the CAD workflow, and the future Project Database.

## 2. Normalisation rules

`normalize_name(value)` applies these rules, in this order:

1. Missing values and spreadsheet placeholders (`None`, `nan`, `null`, `n/a`)
   become an empty string.
2. Unicode NFKC normalisation.
3. Invisible zero-width / bidi-control characters are removed.
4. Arabic diacritics (harakat) and tatweel (`ـ`) are removed.
5. Arabic letter variants are folded: `أ إ آ ٱ → ا`, `ة → ه`, `ى → ي`,
   `ؤ → و`, `ئ → ي`.
6. Arabic-Indic (`٠١٢…`) and Extended Arabic-Indic (`۰۱۲…`) digits become ASCII
   digits.
7. Latin text is case-folded.
8. Punctuation and separators are removed; whitespace is collapsed to a single
   space.
9. Leading/trailing whitespace is removed.

The function **never** transliterates between Arabic and Latin, and it **never**
removes internal spaces. This is deliberate: it prevents two genuinely different
names from collapsing into one key.

Consequences worth knowing:

| Pair | Same key? | Why |
|---|---|---|
| `Office` / `office` | yes | case folding |
| `G-01` / `G01` | yes | separators removed |
| `Unit 3` / `unit   3` | yes | whitespace collapsed |
| `غرفة أ` / `غرفة ا` | yes | hamza folded |
| `شقة` / `شقه` | yes | ta-marbuta folded |
| `مكتب ١٢` / `مكتب 12` | yes | Arabic-Indic digits folded |
| `A B` / `AB` | **no** | internal space is a real word boundary |
| `غرفة` / `ghurfa` | **no** | no transliteration |

## 3. Statuses and reasons

Every room gets exactly one `RoomMatch`:

| Status | Reason | Meaning |
|---|---|---|
| `MATCHED` | `EXACT_NAME_MATCH` | names are identical as written |
| `MATCHED` | `NORMALIZED_NAME_MATCH` | names are equal after normalisation |
| `UNASSIGNED` | `NO_ROOM_NAME` | the room has no usable label |
| `UNASSIGNED` | `NO_MATCHING_SYSTEM_NAME` | no system has that name |
| `AMBIGUOUS` | `DUPLICATE_ROOM_NAME` | two or more rooms share the name |
| `AMBIGUOUS` | `DUPLICATE_SYSTEM_NAME` | two or more systems share the name |

Ambiguity is **never** resolved by choosing the first candidate. The candidate
names are recorded on the `RoomMatch` so the user can see what needs fixing.

## 4. What the result preserves

`MatchingResult` keeps, for every room, the original room name and (when
matched) the original system name, plus the normalised key. It also keeps
`unused_systems`, so nothing is silently dropped. `as_dict()` makes the whole
result serialisable for display and for the future Project Database.

The legacy `suggest_room_assignments(rooms, systems) -> dict[str, str]` still
exists for compatibility, but it now returns **only** the `MATCHED` rows. It can
no longer return a positional guess.

## 5. GUI

The CAD table gained a seventh column, **Match**, showing
`STATUS / REASON` with a tooltip listing the original names and any ambiguity
candidates. The status line reports the matching summary
(`n matched, n unassigned, n ambiguous, n unused system(s)`). The user's manual
choice in the **HAP System** combo still wins over the automatic suggestion.

## 6. Tests

`tests/test_room_system_matching.py` covers every row of the acceptance table:

* exact match;
* match after normalisation;
* Arabic normalisation (hamza, ta-marbuta, alef-maqsura, waw/ya hamza, tatweel,
  diacritics, Arabic-Indic digits);
* English normalisation (case, separators, whitespace);
* no matching system;
* unlabelled room;
* duplicate room names;
* duplicate system names;
* reversed DXF room order;
* reversed HAP system order;
* different room/system counts;
* no positional fallback under any condition (parametrised);
* names that differ by a word boundary are not merged;
* original names preserved; unmatched rooms and unused systems preserved.

`tests/test_cad_matching_gui.py` checks that the table renders the status and
that an unlabelled room is never given a system by position.

The R1 `xfail` in `tests/test_baseline_contracts.py` was removed in the same
commit, because the regression test now passes.

## 7. Results

* `293 passed, 4 xfailed` (the four remaining xfails are R3–R6, owned by later
  phases).
* `python -m compileall -q cad main.py room_review_dialog.py` exits 0.
* `git diff --check` is clean.

## 8. Remaining limitations

* **No transliteration.** An Arabic room label will not match a Latin system
  name (or vice versa). This is intentional; transliteration is a guess.
* **Internal spaces are significant.** `A B` and `AB` are different names.
* **Ambiguous rows stay blank.** The user must rename a duplicate room/system in
  the source, or choose a system manually in the CAD table. The engine will not
  pick one.
* **Manual choices are not re-validated.** If the user assigns a room manually,
  that choice is used for export even if the name would not have matched. This
  is a deliberate user override, not an automatic decision.
