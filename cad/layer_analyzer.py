"""Layer classification and room-candidate review for CAD room detection.

Why this module exists
----------------------
The room detector can only polygonize what it is given.  Before this module the
caller had to supply ``wall_layers`` by hand (or supply nothing, in which case
*every* closed polyline in the drawing became a candidate room).  That is how a
furniture circle was reported as a 0.5 m² room.

This module answers two separate questions and keeps them separate:

1. **What kind of thing is this layer?**  (:class:`LayerClass`)
   A name-based classification with an explicit confidence and an explicit
   "ambiguous with" list.  It is a *suggestion* for the user to confirm, never
   an authoritative statement about the drawing.
2. **Is this polygon a room we may use for engineering?**  (:func:`evaluate_room_candidate`)
   A rule-based decision returning ``CONFIRMED`` / ``NEEDS_REVIEW`` /
   ``REJECTED`` plus machine-readable reasons.

Nothing here mutates a drawing, writes a file, or persists state.  The caller
passes layer names in and receives plain dataclasses back, so a future project
database only has to serialise the ``overrides`` mapping.

Deliberate limitations
----------------------
* Classification is lexical.  A layer called ``0`` or ``Layer1`` cannot be
  classified from its name and is reported as ``OTHER`` with ``LOW``
  confidence so the UI asks the user.
* Area is never the only test.  A 0.5 m² circle on a furniture layer is
  rejected for two independent reasons (layer class and area), and a genuinely
  small room can be accepted by lowering ``min_area_m2``.
* Units are never assumed.  When the DXF ``$INSUNITS`` header is missing the
  candidate is marked ``NEEDS_REVIEW`` with ``DRAWING_UNITS_UNVERIFIED``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Iterable, Mapping, Sequence


class LayerClass(str, Enum):
    """Functional class of a CAD layer, from the room-detection point of view."""

    WALLS = "WALLS"
    ROOM_BOUNDARIES = "ROOM_BOUNDARIES"
    DOORS = "DOORS"
    WINDOWS = "WINDOWS"
    COLUMNS = "COLUMNS"
    FURNITURE = "FURNITURE"
    DIMENSIONS = "DIMENSIONS"
    TEXT_LABELS = "TEXT_LABELS"
    OTHER = "OTHER"


class ClassificationConfidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class CandidateStatus(str, Enum):
    """Review state of a detected polygon."""

    CONFIRMED = "CONFIRMED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    REJECTED = "REJECTED"


#: Classes that may legitimately bound a room.
ROOM_BOUNDING_CLASSES: frozenset[LayerClass] = frozenset(
    {LayerClass.WALLS, LayerClass.ROOM_BOUNDARIES}
)

#: Classes that must never produce a room, whatever their geometry.
NON_ROOM_CLASSES: frozenset[LayerClass] = frozenset(
    {
        LayerClass.DOORS,
        LayerClass.WINDOWS,
        LayerClass.COLUMNS,
        LayerClass.FURNITURE,
        LayerClass.DIMENSIONS,
        LayerClass.TEXT_LABELS,
    }
)

# Tie-break order.  Annotation classes come first because a layer named
# ``ROOM-TEXT`` is a label layer, not a boundary layer.
_PRIORITY: tuple[LayerClass, ...] = (
    LayerClass.TEXT_LABELS,
    LayerClass.DIMENSIONS,
    LayerClass.FURNITURE,
    LayerClass.DOORS,
    LayerClass.WINDOWS,
    LayerClass.COLUMNS,
    LayerClass.WALLS,
    LayerClass.ROOM_BOUNDARIES,
)

_ARABIC_FOLD = str.maketrans(
    {"أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه", "ى": "ي", "ؤ": "و", "ئ": "ي"}
)
_DIACRITICS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]")
_TOKEN_SPLIT = re.compile(r"[^0-9a-z\u0600-\u06FF]+")


#: English + Arabic keywords.  Matching is token based (see :func:`classify_layer`).
_KEYWORDS: dict[LayerClass, tuple[str, ...]] = {
    LayerClass.WALLS: (
        "wall", "walls", "wl", "partition", "partitions",
        "حائط", "حوائط", "جدار", "جدران", "قواطيع", "قاطع",
    ),
    LayerClass.ROOM_BOUNDARIES: (
        "room", "rooms", "space", "spaces", "area", "areas", "zone", "zones",
        "boundary", "boundaries", "outline",
        "غرفه", "غرف", "مساحه", "مساحات", "منطقه", "مناطق", "حدود",
    ),
    LayerClass.DOORS: (
        "door", "doors", "dr", "opening", "openings",
        "باب", "ابواب", "فتحه", "فتحات",
    ),
    LayerClass.WINDOWS: (
        "window", "windows", "win", "glaz", "glazing", "curtain",
        "شباك", "شبابيك", "نافذه", "نوافذ", "زجاج",
    ),
    LayerClass.COLUMNS: (
        "column", "columns", "col", "cols", "pillar", "pillars", "struct",
        "عمود", "اعمده", "دعامه", "دعامات",
    ),
    LayerClass.FURNITURE: (
        "furniture", "furn", "furni", "ff", "ffe", "fittings", "fixture", "fixtures",
        "اثاث", "فرش", "موبيليا", "تجهيزات",
    ),
    LayerClass.DIMENSIONS: (
        "dim", "dims", "dimension", "dimensions", "measure", "measures",
        "ابعاد", "قياسات", "مقاسات",
    ),
    LayerClass.TEXT_LABELS: (
        "text", "txt", "label", "labels", "annotation", "annotations",
        "note", "notes", "tag", "tags", "name", "names",
        "نص", "نصوص", "كتابه", "ملاحظات", "وسم", "وسوم", "اسم", "اسماء",
    ),
}

# Fold the keyword table with the same rules as the layer names, so Arabic
# keywords containing أ/إ/آ/ة/ى/ؤ/ئ still match after normalisation.
_KEYWORDS = {
    layer_class: tuple(
        " ".join(
            token
            for token in _TOKEN_SPLIT.split(
                _DIACRITICS.sub("", keyword).translate(_ARABIC_FOLD).casefold()
            )
            if token
        )
        for keyword in keywords
    )
    for layer_class, keywords in _KEYWORDS.items()
}

def normalize_layer_name(name: object) -> str:
    """Fold a layer name to lowercase, separator-free tokens joined by spaces."""

    text = "" if name is None else str(name)
    text = _DIACRITICS.sub("", text).translate(_ARABIC_FOLD).casefold()
    tokens = [token for token in _TOKEN_SPLIT.split(text) if token]
    return " ".join(tokens)


def layer_tokens(name: object) -> tuple[str, ...]:
    normalized = normalize_layer_name(name)
    return tuple(normalized.split()) if normalized else ()


@dataclass(frozen=True)
class LayerClassification:
    """Classification result for one layer name.

    ``alternatives`` lists other classes that scored equally well.  A non-empty
    list means the name is ambiguous and the UI should ask the user instead of
    trusting ``layer_class``.
    """

    layer: str
    layer_class: LayerClass
    confidence: ClassificationConfidence
    method: str
    matched_keywords: tuple[str, ...] = ()
    alternatives: tuple[LayerClass, ...] = ()

    @property
    def is_ambiguous(self) -> bool:
        return bool(self.alternatives)

    @property
    def needs_user_confirmation(self) -> bool:
        return self.is_ambiguous or self.confidence is not ClassificationConfidence.HIGH

    def as_row(self) -> dict[str, object]:
        return {
            "layer": self.layer,
            "layer_class": self.layer_class.value,
            "confidence": self.confidence.value,
            "method": self.method,
            "matched_keywords": ", ".join(self.matched_keywords),
            "alternatives": ", ".join(item.value for item in self.alternatives),
            "needs_confirmation": self.needs_user_confirmation,
        }


def _score(tokens: Sequence[str], keywords: Sequence[str]) -> tuple[int, list[str]]:
    best = 0
    matched: list[str] = []
    for token in tokens:
        for keyword in keywords:
            if token == keyword:
                score = 3
            elif len(keyword) >= 4 and token.startswith(keyword):
                score = 2
            elif len(keyword) >= 5 and keyword in token:
                score = 1
            else:
                continue
            if score > best:
                best, matched = score, [keyword]
            elif score == best and keyword not in matched:
                matched.append(keyword)
    return best, matched


def classify_layer(name: object) -> LayerClassification:
    """Classify a single layer name.  Never raises; unknown names become OTHER."""

    raw = "" if name is None else str(name)
    tokens = layer_tokens(raw)
    if not tokens:
        return LayerClassification(
            layer=raw,
            layer_class=LayerClass.OTHER,
            confidence=ClassificationConfidence.LOW,
            method="empty_name",
        )

    scores: dict[LayerClass, tuple[int, list[str]]] = {
        layer_class: _score(tokens, keywords)
        for layer_class, keywords in _KEYWORDS.items()
    }
    best_score = max((score for score, _ in scores.values()), default=0)
    if best_score == 0:
        return LayerClassification(
            layer=raw,
            layer_class=LayerClass.OTHER,
            confidence=ClassificationConfidence.LOW,
            method="no_keyword",
        )

    winners = [layer_class for layer_class in _PRIORITY if scores[layer_class][0] == best_score]
    chosen = winners[0]
    alternatives = tuple(item for item in winners[1:])
    confidence = (
        ClassificationConfidence.HIGH
        if best_score >= 3 and not alternatives
        else ClassificationConfidence.MEDIUM
        if best_score >= 2
        else ClassificationConfidence.LOW
    )
    return LayerClassification(
        layer=raw,
        layer_class=chosen,
        confidence=confidence,
        method="exact_token" if best_score >= 3 else "token_prefix" if best_score == 2 else "token_substring",
        matched_keywords=tuple(scores[chosen][1]),
        alternatives=alternatives,
    )


def classify_layers(layer_names: Iterable[object]) -> dict[str, LayerClassification]:
    """Classify every layer, preserving the original spelling as the key."""

    result: dict[str, LayerClassification] = {}
    for name in layer_names:
        raw = "" if name is None else str(name)
        if raw in result:
            continue
        result[raw] = classify_layer(raw)
    return result


@dataclass
class LayerMapping:
    """User-reviewable mapping of layer name -> :class:`LayerClass`.

    ``overrides`` is the only state a project file would ever need to store: a
    plain ``{layer_name: class_name}`` dict.
    """

    classifications: dict[str, LayerClassification] = field(default_factory=dict)
    overrides: dict[str, LayerClass] = field(default_factory=dict)

    def classification(self, layer: object) -> LayerClassification:
        raw = "" if layer is None else str(layer)
        base = self.classifications.get(raw) or classify_layer(raw)
        override = self.overrides.get(raw)
        if override is None:
            return base
        return replace(
            base,
            layer_class=override,
            confidence=ClassificationConfidence.HIGH,
            method="user_override",
            alternatives=(),
        )

    def layer_class(self, layer: object) -> LayerClass:
        return self.classification(layer).layer_class

    def with_override(self, layer: object, layer_class: LayerClass | str) -> "LayerMapping":
        raw = "" if layer is None else str(layer)
        target = layer_class if isinstance(layer_class, LayerClass) else LayerClass(str(layer_class))
        overrides = dict(self.overrides)
        overrides[raw] = target
        return LayerMapping(classifications=self.classifications, overrides=overrides)

    def layers_of(self, *classes: LayerClass) -> set[str]:
        """Return casefolded layer names belonging to any of ``classes``."""

        wanted = set(classes)
        return {
            raw.casefold()
            for raw in self.classifications
            if self.layer_class(raw) in wanted
        }

    def wall_layers(self) -> set[str]:
        """Layers allowed to participate in wall polygonization."""

        return self.layers_of(*ROOM_BOUNDING_CLASSES)

    def label_layers(self) -> set[str]:
        return self.layers_of(LayerClass.TEXT_LABELS)

    def excluded_layers(self) -> set[str]:
        return self.layers_of(*NON_ROOM_CLASSES)

    def unconfirmed_layers(self) -> list[LayerClassification]:
        return [
            self.classification(raw)
            for raw in self.classifications
            if self.classification(raw).needs_user_confirmation
        ]

    def rows(self) -> list[dict[str, object]]:
        """Flat rows for a Qt table model."""

        return [self.classification(raw).as_row() for raw in self.classifications]


def build_layer_mapping(
    layer_names: Iterable[object],
    overrides: Mapping[str, LayerClass | str] | None = None,
) -> LayerMapping:
    mapping = LayerMapping(classifications=classify_layers(layer_names))
    for layer, layer_class in (overrides or {}).items():
        mapping = mapping.with_override(layer, layer_class)
    return mapping


@dataclass(frozen=True)
class RoomCandidateDecision:
    """Outcome of the room-candidate rules."""

    status: CandidateStatus
    reasons: tuple[str, ...] = ()
    blocking: bool = False

    @property
    def usable_for_engineering(self) -> bool:
        return self.status is CandidateStatus.CONFIRMED and not self.blocking


# Reason codes are part of the UI/QA contract; keep them stable.
REASON_LAYER_NOT_A_ROOM = "LAYER_CLASS_NOT_A_ROOM"
REASON_LAYER_UNKNOWN = "LAYER_CLASS_UNKNOWN"
REASON_LAYER_AMBIGUOUS = "LAYER_CLASS_AMBIGUOUS"
REASON_LAYER_UNCONFIRMED = "LAYER_CLASS_UNCONFIRMED"
REASON_AREA_UNKNOWN = "AREA_UNKNOWN"
REASON_AREA_BELOW_MIN = "AREA_BELOW_MINIMUM"
REASON_AREA_ABOVE_MAX = "AREA_ABOVE_MAXIMUM"
REASON_UNITS_UNVERIFIED = "DRAWING_UNITS_UNVERIFIED"
REASON_NO_LABEL = "NO_ROOM_LABEL"
REASON_INVALID_GEOMETRY = "INVALID_GEOMETRY"
REASON_CIRCULAR_SUSPECT = "CIRCULAR_CLOSED_CURVE_SUSPECT"


def evaluate_room_candidate(
    *,
    layer_class: LayerClass | str,
    source: str,
    area_m2: float | None,
    has_label: bool,
    units_verified: bool,
    geometry_valid: bool = True,
    ambiguous: bool = False,
    unconfirmed: bool = False,
    min_area_m2: float = 1.0,
    max_area_m2: float | None = None,
    allow_circular_curves: bool = False,
) -> RoomCandidateDecision:
    """Decide whether a detected polygon may be treated as a room.

    The rules are ordered so that a hard rejection (wrong layer class, invalid
    geometry, impossible area) always wins over a soft "needs review" reason.
    """

    cls = layer_class if isinstance(layer_class, LayerClass) else LayerClass(str(layer_class))
    reasons: list[str] = []

    # 1. Hard rejections ---------------------------------------------------
    if cls in NON_ROOM_CLASSES:
        return RoomCandidateDecision(
            status=CandidateStatus.REJECTED,
            reasons=(f"{REASON_LAYER_NOT_A_ROOM}:{cls.value}",),
            blocking=True,
        )
    if not geometry_valid:
        return RoomCandidateDecision(
            status=CandidateStatus.REJECTED,
            reasons=(REASON_INVALID_GEOMETRY,),
            blocking=True,
        )
    if area_m2 is not None and area_m2 < min_area_m2:
        return RoomCandidateDecision(
            status=CandidateStatus.REJECTED,
            reasons=(f"{REASON_AREA_BELOW_MIN}:{area_m2:.3f}<{min_area_m2:.3f}",),
            blocking=True,
        )

    # 2. Soft review reasons ----------------------------------------------
    if area_m2 is None:
        reasons.append(REASON_AREA_UNKNOWN)
    if max_area_m2 is not None and area_m2 is not None and area_m2 > max_area_m2:
        reasons.append(f"{REASON_AREA_ABOVE_MAX}:{area_m2:.3f}>{max_area_m2:.3f}")
    if not units_verified:
        reasons.append(REASON_UNITS_UNVERIFIED)
    if not has_label:
        reasons.append(REASON_NO_LABEL)
    if cls is LayerClass.OTHER:
        reasons.append(REASON_LAYER_UNKNOWN)
    if ambiguous:
        reasons.append(REASON_LAYER_AMBIGUOUS)
    if unconfirmed:
        reasons.append(REASON_LAYER_UNCONFIRMED)
    if source == "circle" and not allow_circular_curves:
        reasons.append(REASON_CIRCULAR_SUSPECT)

    if reasons:
        return RoomCandidateDecision(status=CandidateStatus.NEEDS_REVIEW, reasons=tuple(reasons))
    return RoomCandidateDecision(status=CandidateStatus.CONFIRMED)


def summarize_decisions(decisions: Sequence[RoomCandidateDecision]) -> dict[str, int]:
    """Count decisions per status, for a UI badge or a QA assertion."""

    summary = {status.value: 0 for status in CandidateStatus}
    for decision in decisions:
        summary[decision.status.value] += 1
    return summary
