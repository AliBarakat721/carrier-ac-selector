"""Phase 4 — explicit, name-based room-to-system matching.

Every test here asserts one row of the Phase 4 acceptance table.  The tests use
plain objects (no Qt, no DXF) so they exercise the matching contract directly.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from cad.room_matching import (
    AssignmentReason,
    AssignmentStatus,
    match_rooms_to_systems,
    normalize_name,
)
from cad.workflow import suggest_room_assignments


def room(room_id: str, label=None):
    return SimpleNamespace(room_id=room_id, label=label)


def system(name: str):
    return SimpleNamespace(name=name)


def statuses(result):
    return {m.room_id: (m.status, m.reason) for m in result.matches}


# ---------------------------------------------------------------------------
# 1. Exact name match
# ---------------------------------------------------------------------------


def test_exact_name_match_is_matched():
    result = match_rooms_to_systems([room("R1", "Office")], [system("Office")])

    assert result.matched == {"R1": "Office"}
    assert statuses(result)["R1"] == (
        AssignmentStatus.MATCHED,
        AssignmentReason.EXACT_NAME_MATCH,
    )


# ---------------------------------------------------------------------------
# 2. Match after normalisation
# ---------------------------------------------------------------------------


def test_match_after_normalisation_is_matched():
    result = match_rooms_to_systems([room("R1", "  office  ")], [system("OFFICE")])

    assert result.matched == {"R1": "OFFICE"}
    assert statuses(result)["R1"] == (
        AssignmentStatus.MATCHED,
        AssignmentReason.NORMALIZED_NAME_MATCH,
    )


# ---------------------------------------------------------------------------
# 3. Arabic normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "room_label, system_name",
    [
        ("غرفة أ", "غرفة ا"),          # hamza-on-alef folded to bare alef
        ("غرفة إ", "غرفة ا"),          # hamza-below-alef folded
        ("شقة", "شقه"),                # ta-marbuta folded to ha
        ("مستشفى", "مستشفي"),          # alef-maqsura folded to ya
        ("مؤسسة", "موسسه"),            # waw-with-hamza folded
        ("شقـة", "شقة"),               # tatweel removed
        ("مكتب ١٢", "مكتب 12"),        # Arabic-Indic digits folded
        ("غَرْفَة", "غرفة"),            # diacritics removed
    ],
)
def test_arabic_normalisation_matches_orthographic_variants(room_label, system_name):
    result = match_rooms_to_systems([room("R1", room_label)], [system(system_name)])

    assert result.matched == {"R1": system_name}
    assert result.matches[0].reason is AssignmentReason.NORMALIZED_NAME_MATCH


def test_arabic_and_latin_names_are_never_merged():
    result = match_rooms_to_systems([room("R1", "غرفة")], [system("ghurfa")])

    assert result.matched == {}
    assert statuses(result)["R1"] == (
        AssignmentStatus.UNASSIGNED,
        AssignmentReason.NO_MATCHING_SYSTEM_NAME,
    )


# ---------------------------------------------------------------------------
# 4. English normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "room_label, system_name",
    [
        ("Office", "office"),          # case
        ("G-01", "G01"),               # separator removed
        ("Room_12", "Room12"),         # underscore removed
        ("Unit 3", "unit   3"),        # whitespace collapsed
        ("  Lobby  ", "LOBBY"),        # surrounding whitespace
    ],
)
def test_english_normalisation_matches(room_label, system_name):
    result = match_rooms_to_systems([room("R1", room_label)], [system(system_name)])

    assert result.matched == {"R1": system_name}


def test_normalisation_does_not_merge_names_that_differ_by_a_word_boundary():
    # "A B" and "AB" are different names; they must not collapse to one key.
    assert normalize_name("A B") != normalize_name("AB")

    result = match_rooms_to_systems([room("R1", "A B")], [system("AB")])

    assert result.matched == {}
    assert result.matches[0].reason is AssignmentReason.NO_MATCHING_SYSTEM_NAME


# ---------------------------------------------------------------------------
# 5. No matching system
# ---------------------------------------------------------------------------


def test_room_without_a_matching_system_is_unassigned():
    result = match_rooms_to_systems([room("R1", "Office")], [system("Storage")])

    assert result.matched == {}
    assert statuses(result)["R1"] == (
        AssignmentStatus.UNASSIGNED,
        AssignmentReason.NO_MATCHING_SYSTEM_NAME,
    )
    assert result.unused_systems == ["Storage"]


# ---------------------------------------------------------------------------
# 6. Unlabelled room
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", [None, "", "   ", "None", "nan"])
def test_unlabelled_room_is_unassigned(label):
    result = match_rooms_to_systems([room("R1", label)], [system("G01")])

    assert result.matched == {}
    assert statuses(result)["R1"] == (
        AssignmentStatus.UNASSIGNED,
        AssignmentReason.NO_ROOM_NAME,
    )
    assert result.unused_systems == ["G01"]


# ---------------------------------------------------------------------------
# 7. Duplicate room names
# ---------------------------------------------------------------------------


def test_duplicate_room_names_are_ambiguous_and_not_resolved():
    rooms = [room("R1", "Office"), room("R2", "office")]
    result = match_rooms_to_systems(rooms, [system("Office")])

    assert result.matched == {}
    for room_id in ("R1", "R2"):
        assert statuses(result)[room_id] == (
            AssignmentStatus.AMBIGUOUS,
            AssignmentReason.DUPLICATE_ROOM_NAME,
        )
    # The system is still reported, never silently attached to R1.
    assert result.unused_systems == ["Office"]


def test_duplicate_room_names_without_any_system_are_still_ambiguous():
    rooms = [room("R1", "Office"), room("R2", "Office")]
    result = match_rooms_to_systems(rooms, [])

    assert {m.status for m in result.matches} == {AssignmentStatus.AMBIGUOUS}
    assert {m.reason for m in result.matches} == {AssignmentReason.DUPLICATE_ROOM_NAME}


# ---------------------------------------------------------------------------
# 8. Duplicate system names
# ---------------------------------------------------------------------------


def test_duplicate_system_names_are_ambiguous_and_not_resolved():
    result = match_rooms_to_systems(
        [room("R1", "Office")],
        [system("Office"), system("Office")],
    )

    assert result.matched == {}
    assert statuses(result)["R1"] == (
        AssignmentStatus.AMBIGUOUS,
        AssignmentReason.DUPLICATE_SYSTEM_NAME,
    )
    assert result.matches[0].candidates == ("Office", "Office")


# ---------------------------------------------------------------------------
# 9 & 10. Order independence
# ---------------------------------------------------------------------------


def _scenario():
    rooms = [
        room("R1", "Office"),
        room("R2", "Meeting"),
        room("R3", "Store"),
    ]
    systems = [system("Meeting"), system("Store"), system("Office")]
    return rooms, systems


def test_reversing_dxf_room_order_does_not_change_the_result():
    rooms, systems = _scenario()
    forward = match_rooms_to_systems(rooms, systems)
    backward = match_rooms_to_systems(list(reversed(rooms)), systems)

    assert forward.matched == backward.matched
    assert statuses(forward) == statuses(backward)


def test_reversing_hap_system_order_does_not_change_the_result():
    rooms, systems = _scenario()
    forward = match_rooms_to_systems(rooms, systems)
    backward = match_rooms_to_systems(rooms, list(reversed(systems)))

    assert forward.matched == backward.matched
    assert statuses(forward) == statuses(backward)


# ---------------------------------------------------------------------------
# 11. Different room/system counts
# ---------------------------------------------------------------------------


def test_different_counts_never_fall_back_to_positional_pairing():
    rooms = [room("R1", "A"), room("R2", "B"), room("R3", "C")]
    systems = [system("A"), system("B")]

    result = match_rooms_to_systems(rooms, systems)

    assert result.matched == {"R1": "A", "R2": "B"}
    assert statuses(result)["R3"] == (
        AssignmentStatus.UNASSIGNED,
        AssignmentReason.NO_MATCHING_SYSTEM_NAME,
    )
    assert result.unused_systems == []


def test_more_systems_than_rooms_keeps_unused_systems():
    rooms = [room("R1", "A")]
    systems = [system("A"), system("B"), system("C")]

    result = match_rooms_to_systems(rooms, systems)

    assert result.matched == {"R1": "A"}
    assert result.unused_systems == ["B", "C"]


# ---------------------------------------------------------------------------
# 12. No positional fallback under any condition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "room_labels, system_names",
    [
        ([None, None], ["G01", "G02"]),
        (["X", "Y"], ["P", "Q"]),
        ([None, "Y"], ["P", "Q"]),
        (["X", None], ["P", "Q"]),
        (["X", "Y", "Z"], ["P", "Q"]),
        (["X"], ["P", "Q"]),
        ([], ["P", "Q"]),
        (["X", "Y"], []),
    ],
)
def test_no_positional_fallback_under_any_condition(room_labels, system_names):
    rooms = [room(f"R{i}", label) for i, label in enumerate(room_labels)]
    systems = [system(name) for name in system_names]

    result = match_rooms_to_systems(rooms, systems)

    # A room may only be matched when its own name matches a system name.
    for match in result.matches:
        if match.is_matched:
            assert normalize_name(match.room_name) == normalize_name(match.system_name)


def test_legacy_dict_view_contains_only_name_matches():
    rooms = [room("R1", "Office"), room("R2", None), room("R3", "Nowhere")]
    systems = [system("Office"), system("Other")]

    assert suggest_room_assignments(rooms, systems) == {"R1": "Office"}


# ---------------------------------------------------------------------------
# Traceability: original names preserved, unmatched rooms and unused systems kept
# ---------------------------------------------------------------------------


def test_original_names_are_preserved_for_display():
    result = match_rooms_to_systems([room("R1", "  Office  ")], [system("OFFICE")])
    match = result.matches[0]

    assert match.room_name == "Office"          # original casing preserved
    assert match.system_name == "OFFICE"        # original system name preserved
    assert match.system_key == "office"         # normalised key recorded


def test_unmatched_rooms_and_unused_systems_are_both_preserved():
    rooms = [room("R1", "Office"), room("R2", "Lab"), room("R3", None)]
    systems = [system("Office"), system("Store")]

    result = match_rooms_to_systems(rooms, systems)

    assert result.matched == {"R1": "Office"}
    assert {m.room_id for m in result.unassigned} == {"R2", "R3"}
    assert result.unused_systems == ["Store"]
    assert result.counts() == {
        "matched": 1,
        "unassigned": 2,
        "ambiguous": 0,
        "unused_systems": 1,
    }
