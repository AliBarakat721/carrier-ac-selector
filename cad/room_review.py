"""Interactive room-review session (Phase 3, increment 2).

The detector (:func:`cad.space_detector.detect_spaces_with_review`) answers
"what does the drawing contain and which candidates are questionable?".  It
deliberately does **not** answer "what did the engineer decide?".

This module holds exactly that second answer, and nothing else:

* which layers the reviewer re-classified (``layer_overrides``),
* which drawing units the reviewer confirmed (``units_override``),
* the minimum room area the reviewer accepted (``min_area_m2``),
* which candidates the reviewer accepted or rejected (``room_decisions``).

Why a separate object instead of putting this in the Qt dialog
--------------------------------------------------------------
* It is **headless**: the whole review workflow is testable without a display,
  and the GUI becomes a thin table that calls these methods.
* It is **stateless with respect to the project**: :meth:`RoomReviewSession.to_dict`
  returns a plain JSON-safe dict.  That dict is the entire link point to the
  Project Database, which is explicitly out of scope here.  Nothing in this
  module reads or writes a project file.
* Decisions are keyed by **geometry** (centroid + raw area), not by the
  positional ``ROOM-001`` id, because re-classifying a layer changes how many
  polygons are found and would otherwise silently re-point a decision at a
  different room.

Engineering rules enforced here
-------------------------------
1. A candidate the rules rejected (furniture, door, window, column, dimension,
   text, invalid geometry, area below the minimum) **cannot** be accepted from
   the review table.  The reviewer must change the *layer classification* and
   let the rules run again.  This is what stops "I'll just tick it anyway".
2. A room is only ``usable`` for engineering when it is accepted **and** the
   drawing units are known.  An area in unknown units is not an area.
3. Rejecting a room never deletes the polygon from the report; it only removes
   it from the accepted set, so the reason stays auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from shapely.geometry import Polygon

from .dxf_reader import CadDocument, unit_code_for, unit_name, unit_scale_to_meters
from .layer_analyzer import CandidateStatus, LayerClass, LayerMapping
from .space_detector import RoomDetectionReport, RoomSpace, detect_spaces_with_review


class ReviewAction(str, Enum):
    """What the reviewer did with one candidate."""

    PENDING = "PENDING"
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"


class ReviewStatus(str, Enum):
    """Effective row status shown in the review table."""

    #: Rules confirmed the candidate and no reviewer action was needed.
    AUTO_ACCEPTED = "AUTO_ACCEPTED"
    #: Rules asked for review and the reviewer accepted it.
    ACCEPTED = "ACCEPTED"
    #: Rules asked for review and nobody has decided yet.
    NEEDS_REVIEW = "NEEDS_REVIEW"
    #: The reviewer rejected it.
    REJECTED = "REJECTED"
    #: The rules rejected it; the reviewer cannot accept it from this table.
    BLOCKED = "BLOCKED"


#: Statuses whose rooms may be used downstream.
ACCEPTED_STATUSES = frozenset({ReviewStatus.AUTO_ACCEPTED, ReviewStatus.ACCEPTED})


def room_key(room: RoomSpace) -> str:
    """Geometry-based identity for a detected polygon.

    ``ROOM-003`` is a position in a list; this key is the shape itself, so a
    decision survives a re-detection that renumbers the rooms.
    """

    polygon = room.polygon
    if isinstance(polygon, Polygon) and not polygon.is_empty:
        centroid = polygon.centroid
        return f"{centroid.x:.3f},{centroid.y:.3f}|{polygon.area:.3f}"
    return f"{room.room_id}|{room.area:.3f}"


@dataclass(frozen=True)
class RoomReviewRow:
    """One row of the room-candidate review table."""

    room_id: str
    room_key: str
    label: str
    layer: str
    layer_class: str
    source: str
    area_m2: float | None
    rule_status: str
    reasons: tuple[str, ...]
    action: str
    status: ReviewStatus
    usable: bool
    boundary_layers: tuple[str, ...] = ()

    @property
    def reasons_text(self) -> str:
        return "; ".join(self.reasons)

    def as_dict(self) -> dict[str, object]:
        return {
            "room_id": self.room_id,
            "room_key": self.room_key,
            "label": self.label,
            "layer": self.layer,
            "layer_class": self.layer_class,
            "source": self.source,
            "area_m2": None if self.area_m2 is None else round(self.area_m2, 3),
            "rule_status": self.rule_status,
            "reasons": self.reasons_text,
            "action": self.action,
            "status": self.status.value,
            "usable": self.usable,
        }


@dataclass
class RoomReviewSession:
    """Review state for one drawing.

    The session owns a :class:`~cad.space_detector.RoomDetectionReport` and
    re-runs detection whenever a classification input changes, so the table can
    never drift from the rules.
    """

    document: CadDocument
    layer_overrides: dict[str, LayerClass] = field(default_factory=dict)
    units_override: str | None = None
    min_area_m2: float = 1.0
    max_area_m2: float | None = None
    allow_circular_curves: bool = False
    require_label: bool = False
    auto_accept_confirmed: bool = True
    #: ``room_key -> ReviewAction``; only explicit reviewer decisions are stored.
    room_decisions: dict[str, ReviewAction] = field(default_factory=dict)
    report: RoomDetectionReport | None = None

    # -- construction ----------------------------------------------------
    @classmethod
    def from_path(cls, path: str | Path, **kwargs: Any) -> "RoomReviewSession":
        from .importer import read_cad_file

        return cls(document=read_cad_file(path), **kwargs)

    def __post_init__(self) -> None:
        if self.report is None:
            self.refresh()

    # -- detection -------------------------------------------------------
    def refresh(self) -> "RoomReviewSession":
        """Re-run detection with the current review inputs."""

        self.report = detect_spaces_with_review(
            self.document,
            layer_overrides=dict(self.layer_overrides),
            units_override=self.units_override,
            min_area_m2=self.min_area_m2,
            max_area_m2=self.max_area_m2,
            allow_circular_curves=self.allow_circular_curves,
            require_label=self.require_label,
        )
        return self

    @property
    def rooms(self) -> list[RoomSpace]:
        return list(self.report.rooms) if self.report else []

    @property
    def layer_mapping(self) -> LayerMapping:
        assert self.report is not None
        return self.report.layer_mapping

    @property
    def units_verified(self) -> bool:
        return bool(self.report and self.report.units_status in {"VERIFIED", "USER_CONFIRMED"})

    @property
    def units_label(self) -> str:
        if self.report is None:
            return "unknown"
        if self.units_override:
            code = unit_code_for(self.units_override)
            return unit_name(code) if code is not None else str(self.units_override)
        return self.document.units

    # -- layer classification --------------------------------------------
    def layer_rows(self) -> list[dict[str, object]]:
        """Rows for the layer-mapping table, ordered walls/labels first."""

        rows = self.layer_mapping.rows()
        priority = {
            LayerClass.WALLS.value: 0,
            LayerClass.ROOM_BOUNDARIES.value: 1,
            LayerClass.TEXT_LABELS.value: 2,
            LayerClass.DOORS.value: 3,
            LayerClass.WINDOWS.value: 4,
            LayerClass.COLUMNS.value: 5,
            LayerClass.FURNITURE.value: 6,
            LayerClass.DIMENSIONS.value: 7,
            LayerClass.OTHER.value: 8,
        }
        for row in rows:
            row["overridden"] = row["layer"] in self.layer_overrides
            row["boundary"] = row["layer_class"] in {
                LayerClass.WALLS.value,
                LayerClass.ROOM_BOUNDARIES.value,
            }
        rows.sort(
            key=lambda row: (priority.get(str(row["layer_class"]), 9), str(row["layer"]).casefold())
        )
        return rows

    def set_layer_class(self, layer: str, layer_class: LayerClass | str) -> "RoomReviewSession":
        """Re-classify ``layer`` and re-detect.

        This is the only way to unblock a candidate the rules rejected.
        """

        target = layer_class if isinstance(layer_class, LayerClass) else LayerClass(str(layer_class))
        self.layer_overrides[str(layer)] = target
        return self.refresh()

    def clear_layer_override(self, layer: str) -> "RoomReviewSession":
        self.layer_overrides.pop(str(layer), None)
        return self.refresh()

    def wall_layers(self) -> list[str]:
        return sorted(self.layer_mapping.wall_layers())

    def label_layers(self) -> list[str]:
        return sorted(self.layer_mapping.label_layers())

    # -- units and thresholds --------------------------------------------
    def set_units(self, units: str | int) -> "RoomReviewSession":
        if unit_scale_to_meters(units) is None:
            raise ValueError(
                f"Cannot confirm {units!r} as drawing units; use a DXF unit code or a "
                "unit name such as 'meters', 'millimeters', 'centimeters', 'inches', 'feet'."
            )
        code = unit_code_for(units)
        self.units_override = unit_name(code) if code is not None else str(units)
        return self.refresh()

    def clear_units(self) -> "RoomReviewSession":
        self.units_override = None
        return self.refresh()

    def set_min_area_m2(self, value: float) -> "RoomReviewSession":
        if value < 0:
            raise ValueError("min_area_m2 must be >= 0")
        self.min_area_m2 = float(value)
        return self.refresh()

    # -- per-room decisions ----------------------------------------------
    def _room_by_id(self, room_id: str) -> RoomSpace:
        for room in self.rooms:
            if room.room_id == room_id:
                return room
        raise KeyError(f"No detected room with id {room_id!r}")

    def _rule_status(self, room: RoomSpace) -> CandidateStatus:
        return CandidateStatus(room.candidate_status)

    def accept(self, room_id: str) -> "RoomReviewSession":
        """Accept a candidate that the rules sent to review.

        Raises ``ValueError`` for a rule-rejected candidate: the reviewer must
        fix the layer classification instead of overriding the rules.
        """

        room = self._room_by_id(room_id)
        if self._rule_status(room) is CandidateStatus.REJECTED:
            raise ValueError(
                f"{room_id} was rejected by the detection rules "
                f"({'; '.join(room.candidate_reasons) or 'no reason recorded'}). "
                "It cannot be accepted from the review table. If the layer is "
                "genuinely a wall/room-boundary layer, re-classify that layer and "
                "the rules will run again."
            )
        self.room_decisions[room_key(room)] = ReviewAction.ACCEPT
        return self

    def reject(self, room_id: str) -> "RoomReviewSession":
        room = self._room_by_id(room_id)
        self.room_decisions[room_key(room)] = ReviewAction.REJECT
        return self

    def clear_decision(self, room_id: str) -> "RoomReviewSession":
        room = self._room_by_id(room_id)
        self.room_decisions.pop(room_key(room), None)
        return self

    def accept_all_reviewable(self) -> "RoomReviewSession":
        """Accept every candidate waiting for review (never blocked ones)."""

        for room in self.rooms:
            if self._rule_status(room) is CandidateStatus.NEEDS_REVIEW:
                self.room_decisions[room_key(room)] = ReviewAction.ACCEPT
        return self

    def reject_all_reviewable(self) -> "RoomReviewSession":
        for room in self.rooms:
            if self._rule_status(room) is CandidateStatus.NEEDS_REVIEW:
                self.room_decisions[room_key(room)] = ReviewAction.REJECT
        return self

    # -- derived state ---------------------------------------------------
    def _status_for(self, room: RoomSpace) -> ReviewStatus:
        rule = self._rule_status(room)
        action = self.room_decisions.get(room_key(room), ReviewAction.PENDING)
        if rule is CandidateStatus.REJECTED:
            return ReviewStatus.BLOCKED
        if action is ReviewAction.REJECT:
            return ReviewStatus.REJECTED
        if action is ReviewAction.ACCEPT:
            return ReviewStatus.ACCEPTED
        if rule is CandidateStatus.CONFIRMED and self.auto_accept_confirmed:
            return ReviewStatus.AUTO_ACCEPTED
        return ReviewStatus.NEEDS_REVIEW

    def rows(self) -> list[RoomReviewRow]:
        rows: list[RoomReviewRow] = []
        for room in self.rooms:
            status = self._status_for(room)
            rows.append(
                RoomReviewRow(
                    room_id=room.room_id,
                    room_key=room_key(room),
                    label=room.label or "",
                    layer=room.effective_layer or "",
                    layer_class=room.layer_class,
                    source=room.source,
                    area_m2=room.area if self.units_verified else None,
                    rule_status=room.candidate_status,
                    reasons=room.candidate_reasons,
                    action=self.room_decisions.get(room_key(room), ReviewAction.PENDING).value,
                    status=status,
                    usable=status in ACCEPTED_STATUSES and self.units_verified,
                    boundary_layers=room.boundary_layers,
                )
            )
        return rows

    def rooms_with_status(self, *statuses: ReviewStatus) -> list[RoomSpace]:
        wanted = set(statuses)
        return [room for room in self.rooms if self._status_for(room) in wanted]

    @property
    def accepted_rooms(self) -> list[RoomSpace]:
        """Rooms the reviewer (or the rules) accepted, ignoring units."""

        return self.rooms_with_status(*ACCEPTED_STATUSES)

    @property
    def usable_rooms(self) -> list[RoomSpace]:
        """Rooms that may enter engineering: accepted *and* units known."""

        if not self.units_verified:
            return []
        return self.accepted_rooms

    @property
    def pending_rooms(self) -> list[RoomSpace]:
        return self.rooms_with_status(ReviewStatus.NEEDS_REVIEW)

    @property
    def blocked_rooms(self) -> list[RoomSpace]:
        return self.rooms_with_status(ReviewStatus.BLOCKED)

    @property
    def rejected_rooms(self) -> list[RoomSpace]:
        return self.rooms_with_status(ReviewStatus.REJECTED)

    @property
    def ready_for_engineering(self) -> bool:
        return bool(self.usable_rooms) and not self.pending_rooms

    def blockers(self) -> list[str]:
        """Human-readable reasons the review is not finished yet."""

        messages: list[str] = []
        if not self.units_verified:
            messages.append(
                "Drawing units are not confirmed. Areas are raw drawing units and "
                "cannot be used for engineering until the units are set."
            )
        if self.pending_rooms:
            messages.append(
                f"{len(self.pending_rooms)} room candidate(s) still need a decision: "
                + ", ".join(room.room_id for room in self.pending_rooms)
            )
        if not self.usable_rooms:
            messages.append("No room is available for engineering yet.")
        return messages

    def summary(self) -> dict[str, int]:
        counts = {status.value: 0 for status in ReviewStatus}
        for row in self.rows():
            counts[row.status.value] += 1
        counts["total"] = len(self.rooms)
        counts["usable"] = len(self.usable_rooms)
        return counts

    def status_line(self) -> str:
        counts = self.summary()
        return (
            f"{counts['total']} candidate(s): "
            f"{counts['AUTO_ACCEPTED']} auto-accepted, "
            f"{counts['ACCEPTED']} accepted, "
            f"{counts['NEEDS_REVIEW']} awaiting review, "
            f"{counts['REJECTED']} rejected by reviewer, "
            f"{counts['BLOCKED']} blocked by rules"
            + ("" if self.units_verified else " \u2014 units NOT confirmed")
        )

    # -- serialisation (the Project Database link point) ------------------
    def to_dict(self) -> dict[str, Any]:
        """JSON-safe review state.  Nothing else needs to be stored."""

        return {
            "layer_overrides": {
                layer: layer_class.value
                for layer, layer_class in sorted(self.layer_overrides.items())
            },
            "units_override": self.units_override,
            "min_area_m2": self.min_area_m2,
            "max_area_m2": self.max_area_m2,
            "allow_circular_curves": self.allow_circular_curves,
            "require_label": self.require_label,
            "room_decisions": {
                key: action.value for key, action in sorted(self.room_decisions.items())
            },
        }

    def apply_dict(self, data: Mapping[str, Any]) -> "RoomReviewSession":
        """Restore review state produced by :meth:`to_dict`."""

        overrides = data.get("layer_overrides") or {}
        self.layer_overrides = {
            str(layer): LayerClass(str(value)) for layer, value in overrides.items()
        }
        self.units_override = data.get("units_override") or None
        if data.get("min_area_m2") is not None:
            self.min_area_m2 = float(data["min_area_m2"])
        self.max_area_m2 = data.get("max_area_m2")
        self.allow_circular_curves = bool(data.get("allow_circular_curves", False))
        self.require_label = bool(data.get("require_label", False))
        self.room_decisions = {
            str(key): ReviewAction(str(value))
            for key, value in (data.get("room_decisions") or {}).items()
        }
        return self.refresh()

    def reset(self) -> "RoomReviewSession":
        """Forget every reviewer decision but keep the drawing."""

        self.layer_overrides.clear()
        self.units_override = None
        self.room_decisions.clear()
        self.min_area_m2 = 1.0
        self.max_area_m2 = None
        self.allow_circular_curves = False
        self.require_label = False
        return self.refresh()
