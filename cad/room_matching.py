"""Explicit, name-based matching of detected rooms to HAP systems.

Phase 4 replaces the old ``suggest_room_assignments`` heuristic, which paired
left-over rooms and systems by drawing/report order whenever the counts happened
to be equal.  This module never uses position, ``room_id``, or DXF entity order
as evidence.  A room is matched to a system **only** when their names are equal
after an explicit, documented normalisation, and only when that name is unique
on both sides.  Every other outcome is reported with a status and a reason.

The module is deliberately free of Qt and of any persistence, so it can be
tested headlessly and reused by the CAD workflow, the GUI, and the (out of
scope) Project Database alike.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Sequence

__all__ = [
    "AssignmentStatus",
    "AssignmentReason",
    "RoomMatch",
    "MatchingResult",
    "normalize_name",
    "match_rooms_to_systems",
]


# ---------------------------------------------------------------------------
# Statuses and reasons
# ---------------------------------------------------------------------------


class AssignmentStatus(str, Enum):
    """The outcome of trying to match one room to one HAP system."""

    MATCHED = "MATCHED"
    UNASSIGNED = "UNASSIGNED"
    AMBIGUOUS = "AMBIGUOUS"


class AssignmentReason(str, Enum):
    """Why a room ended up with a given status."""

    EXACT_NAME_MATCH = "EXACT_NAME_MATCH"
    NORMALIZED_NAME_MATCH = "NORMALIZED_NAME_MATCH"
    NO_ROOM_NAME = "NO_ROOM_NAME"
    NO_MATCHING_SYSTEM_NAME = "NO_MATCHING_SYSTEM_NAME"
    DUPLICATE_ROOM_NAME = "DUPLICATE_ROOM_NAME"
    DUPLICATE_SYSTEM_NAME = "DUPLICATE_SYSTEM_NAME"


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

# Arabic letter variants that are the same letter for matching purposes.
# These are orthographic variants, not different words.
_ARABIC_LETTER_FOLD = {
    "\u0623": "\u0627",  # أ -> ا
    "\u0625": "\u0627",  # إ -> ا
    "\u0622": "\u0627",  # آ -> ا
    "\u0671": "\u0627",  # ٱ -> ا
    "\u0629": "\u0647",  # ة -> ه
    "\u0649": "\u064a",  # ى -> ي
    "\u0624": "\u0648",  # ؤ -> و
    "\u0626": "\u064a",  # ئ -> ي
}

# Tatweel (kashida) is purely decorative.
_TATWEEL = "\u0640"

# Arabic diacritics (harakat), Quranic marks, and superscript alef.
_ARABIC_DIACRITICS = re.compile(r"[\u064b-\u065f\u0670\u06d6-\u06ed]")

# Arabic-Indic and Extended Arabic-Indic digits.
_ARABIC_INDIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_EXTENDED_ARABIC_INDIC_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")

# Zero-width and bidi-control characters that can hide inside a label.
_INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff]")

# Punctuation and separators are removed (they are not word boundaries in an
# identifier), but whitespace is kept and collapsed to a single space.  This
# means "G-01" == "G01" while "A B" != "AB" — we never merge names that differ
# by a real word boundary.
_SEPARATORS = re.compile(
    r"[\-_\u2010-\u2015/\\.,;:!?\"'`~@#$%^&*+=|<>()\[\]{}\u060c\u061b\u061f\u066a-\u066d\u2026\u00ab\u00bb]"
)

_WHITESPACE = re.compile(r"\s+")

# Values that a spreadsheet export uses to mean "no value".
_EMPTY_TOKENS = {"none", "nan", "null", "n/a", "na"}


def _clean_text(value: object) -> str:
    """Return the original text, or ``""`` for a missing/placeholder value.

    The original text is preserved (no case folding, no trimming of meaningful
    characters) so callers can display it exactly as the source wrote it.
    """

    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.casefold() in _EMPTY_TOKENS:
        return ""
    return text


def normalize_name(value: object) -> str:
    """Return the canonical matching key for a room label or system name.

    The rules are explicit and applied in this order:

    1. Missing values and spreadsheet placeholders (``None``, ``nan``, ``null``,
       ``n/a``) become ``""``.
    2. Unicode NFKC normalisation (compatibility forms, e.g. full-width digits).
    3. Invisible zero-width / bidi-control characters are removed.
    4. Arabic diacritics (harakat) and tatweel are removed.
    5. Arabic letter variants are folded: ``أ إ آ ٱ -> ا``, ``ة -> ه``,
       ``ى -> ي``, ``ؤ -> و``, ``ئ -> ي``.
    6. Arabic-Indic and Extended Arabic-Indic digits become ASCII digits.
    7. Latin text is case-folded.
    8. Punctuation and separators are removed; whitespace is collapsed to a
       single space.
    9. Leading/trailing whitespace is removed.

    The function never transliterates between Arabic and Latin, and it never
    removes internal spaces, so two genuinely different names cannot be merged
    into one key.
    """

    text = _clean_text(value)
    if not text:
        return ""

    text = unicodedata.normalize("NFKC", text)
    text = _INVISIBLE.sub("", text)
    text = _ARABIC_DIACRITICS.sub("", text)
    text = text.replace(_TATWEEL, "")
    text = "".join(_ARABIC_LETTER_FOLD.get(ch, ch) for ch in text)
    text = text.translate(_ARABIC_INDIC_DIGITS).translate(_EXTENDED_ARABIC_INDIC_DIGITS)
    text = text.casefold()
    text = _SEPARATORS.sub("", text)
    text = _WHITESPACE.sub(" ", text).strip()
    return text


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoomMatch:
    """The matching outcome for a single room.

    ``room_name`` and ``system_name`` keep the **original** text from the DXF
    label and the HAP report, so the UI and any audit trail can show exactly
    what the source said.  ``system_key`` is the normalised key that produced a
    match and is only populated for ``MATCHED`` rows.
    """

    room_id: str
    room_name: str
    status: AssignmentStatus
    reason: AssignmentReason
    system_name: str | None = None
    system_key: str | None = None
    candidates: tuple[str, ...] = ()

    @property
    def is_matched(self) -> bool:
        return self.status is AssignmentStatus.MATCHED

    def as_dict(self) -> dict[str, object]:
        return {
            "room_id": self.room_id,
            "room_name": self.room_name,
            "status": self.status.value,
            "reason": self.reason.value,
            "system_name": self.system_name,
            "system_key": self.system_key,
            "candidates": list(self.candidates),
        }


@dataclass
class MatchingResult:
    """The complete outcome of matching a set of rooms to a set of systems."""

    matches: list[RoomMatch] = field(default_factory=list)
    unused_systems: list[str] = field(default_factory=list)

    @property
    def matched(self) -> dict[str, str]:
        """``{room_id: system_name}`` for matched rooms only.

        This is the shape the legacy ``suggest_room_assignments`` returns, so
        existing callers keep working without ever seeing a positional guess.
        """

        return {m.room_id: m.system_name for m in self.matches if m.is_matched and m.system_name}

    @property
    def ambiguous(self) -> list[RoomMatch]:
        return [m for m in self.matches if m.status is AssignmentStatus.AMBIGUOUS]

    @property
    def unassigned(self) -> list[RoomMatch]:
        return [m for m in self.matches if m.status is AssignmentStatus.UNASSIGNED]

    def by_room_id(self) -> dict[str, RoomMatch]:
        return {m.room_id: m for m in self.matches}

    def counts(self) -> dict[str, int]:
        return {
            "matched": sum(1 for m in self.matches if m.status is AssignmentStatus.MATCHED),
            "unassigned": len(self.unassigned),
            "ambiguous": len(self.ambiguous),
            "unused_systems": len(self.unused_systems),
        }

    def summary(self) -> str:
        c = self.counts()
        return (
            f"{c['matched']} matched, {c['unassigned']} unassigned, "
            f"{c['ambiguous']} ambiguous, {c['unused_systems']} unused system(s)"
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "matches": [m.as_dict() for m in self.matches],
            "unused_systems": list(self.unused_systems),
            "counts": self.counts(),
        }


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _system_name(system: object) -> str:
    """Return the original name of a system, which may be a str or an object."""

    if isinstance(system, str):
        return _clean_text(system)
    return _clean_text(getattr(system, "name", system))


def match_rooms_to_systems(
    rooms: Sequence[object],
    systems: Iterable[object],
) -> MatchingResult:
    """Match rooms to systems strictly by name.

    The algorithm is deliberately simple and order-independent:

    * Each room's label and each system's name are reduced to a canonical key
      with :func:`normalize_name`.
    * A room is ``MATCHED`` only when its key is unique among the rooms **and**
      occurs exactly once among the systems.
    * A room with no usable name is ``UNASSIGNED`` / ``NO_ROOM_NAME``.
    * A named room whose key matches no system is ``UNASSIGNED`` /
      ``NO_MATCHING_SYSTEM_NAME``.
    * A room whose name is shared by another room is ``AMBIGUOUS`` /
      ``DUPLICATE_ROOM_NAME`` — we cannot tell which physical room is meant.
    * A room whose key matches more than one system is ``AMBIGUOUS`` /
      ``DUPLICATE_SYSTEM_NAME``.

    Ambiguity is never resolved by picking the first candidate.  Unmatched rooms
    and unused systems are both preserved in the result.

    Room order and system order do not affect any decision: the result is built
    from key-indexed maps, never from ``zip()``.
    """

    room_list = list(rooms)
    system_list = list(systems)

    system_names = [_system_name(system) for system in system_list]
    system_keys = [normalize_name(name) for name in system_names]

    # key -> indices of systems carrying that key (ignoring nameless systems).
    system_indices_by_key: dict[str, list[int]] = {}
    for index, key in enumerate(system_keys):
        if key:
            system_indices_by_key.setdefault(key, []).append(index)

    room_names = [_clean_text(getattr(room, "label", None)) for room in room_list]
    room_keys = [normalize_name(name) for name in room_names]
    room_key_counts = Counter(key for key in room_keys if key)

    matches: list[RoomMatch] = []
    used_system_indices: set[int] = set()

    for room, room_name, key in zip(room_list, room_names, room_keys):
        room_id = str(getattr(room, "room_id", ""))

        if not key:
            matches.append(
                RoomMatch(
                    room_id=room_id,
                    room_name=room_name,
                    status=AssignmentStatus.UNASSIGNED,
                    reason=AssignmentReason.NO_ROOM_NAME,
                )
            )
            continue

        if room_key_counts[key] > 1:
            candidates = tuple(
                system_names[i] for i in system_indices_by_key.get(key, [])
            )
            matches.append(
                RoomMatch(
                    room_id=room_id,
                    room_name=room_name,
                    status=AssignmentStatus.AMBIGUOUS,
                    reason=AssignmentReason.DUPLICATE_ROOM_NAME,
                    candidates=candidates,
                )
            )
            continue

        indices = system_indices_by_key.get(key, [])
        if not indices:
            matches.append(
                RoomMatch(
                    room_id=room_id,
                    room_name=room_name,
                    status=AssignmentStatus.UNASSIGNED,
                    reason=AssignmentReason.NO_MATCHING_SYSTEM_NAME,
                )
            )
            continue

        if len(indices) > 1:
            matches.append(
                RoomMatch(
                    room_id=room_id,
                    room_name=room_name,
                    status=AssignmentStatus.AMBIGUOUS,
                    reason=AssignmentReason.DUPLICATE_SYSTEM_NAME,
                    candidates=tuple(system_names[i] for i in indices),
                )
            )
            continue

        index = indices[0]
        used_system_indices.add(index)
        reason = (
            AssignmentReason.EXACT_NAME_MATCH
            if room_name == system_names[index]
            else AssignmentReason.NORMALIZED_NAME_MATCH
        )
        matches.append(
            RoomMatch(
                room_id=room_id,
                room_name=room_name,
                status=AssignmentStatus.MATCHED,
                reason=reason,
                system_name=system_names[index],
                system_key=key,
            )
        )

    unused_systems = [
        system_names[i] for i in range(len(system_list)) if i not in used_system_indices
    ]

    return MatchingResult(matches=matches, unused_systems=unused_systems)
