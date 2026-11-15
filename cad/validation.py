"""Pre-export validation for CAD/HVAC drawings.

The validator is deliberately independent of Qt so it can be used by the GUI,
batch exports, and automated tests with the same rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class ValidationIssue:
    severity: str  # ERROR or WARNING
    code: str
    message: str


@dataclass
class CadValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)
    room_statuses: dict[str, str] = field(default_factory=dict)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "ERROR"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "WARNING"]

    @property
    def blocking(self) -> bool:
        return bool(self.errors)

    def add(self, severity: str, code: str, message: str) -> None:
        self.issues.append(ValidationIssue(severity, code, message))

    def to_text(self) -> str:
        if not self.issues:
            return "✓ Drawing validation passed with no errors or warnings."
        lines = ["Drawing validation"]
        for issue in self.issues:
            marker = "✗" if issue.severity == "ERROR" else "⚠"
            lines.append(f"{marker} [{issue.code}] {issue.message}")
        return "\n".join(lines)


def validate_cad_design(document, rooms: Iterable[object], *, indoor_units=(), diagnostics=()) -> CadValidationReport:
    """Validate the minimum safety contract before an HVAC DXF export."""

    report = CadValidationReport()
    room_list = list(rooms or [])
    if not room_list:
        report.add("ERROR", "NO_ROOMS", "No approved rooms are available for export.")

    units_known = bool(getattr(document, "units_verified", False)) or all(
        getattr(room, "unit_status", "") == "VERIFIED" for room in room_list
    )
    if not units_known:
        report.add("ERROR", "UNCONFIRMED_UNITS", "Drawing units are not confirmed.")

    ids: set[str] = set()
    valid_rooms: list[object] = []
    for room in room_list:
        room_id = str(getattr(room, "room_id", ""))
        report.room_statuses.setdefault(room_id, "PASS")
        if room_id in ids:
            report.add("ERROR", "DUPLICATE_ROOM_ID", f"Room ID {room_id!r} appears more than once.")
            report.room_statuses[room_id] = "ERROR"
        ids.add(room_id)
        polygon = getattr(room, "polygon", None)
        if polygon is None or polygon.is_empty or polygon.area <= 0:
            report.add("ERROR", "INVALID_ROOM_GEOMETRY", f"Room {room_id or '(unnamed)'} has no positive area.")
            report.room_statuses[room_id] = "ERROR"
            continue
        if not polygon.is_valid:
            report.add("ERROR", "INVALID_ROOM_GEOMETRY", f"Room {room_id or '(unnamed)'} has invalid geometry.")
            report.room_statuses[room_id] = "ERROR"
            continue
        valid_rooms.append(room)

    for index, left in enumerate(valid_rooms):
        for right in valid_rooms[index + 1 :]:
            intersection = left.polygon.intersection(right.polygon)
            if intersection.area > max(1e-9, min(left.polygon.area, right.polygon.area) * 1e-6):
                report.add(
                    "ERROR",
                    "OVERLAPPING_ROOMS",
                    f"Rooms {left.room_id} and {right.room_id} overlap by {intersection.area:.3f} drawing units².",
                )
                report.room_statuses[left.room_id] = "ERROR"
                report.room_statuses[right.room_id] = "ERROR"

    supported = {"LINE", "LWPOLYLINE", "POLYLINE", "SPLINE", "SOLID", "CIRCLE", "ARC", "TEXT", "MTEXT", "ATTRIB", "HATCH"}
    unsupported = sorted({str(getattr(entity, "entity_type", "")).upper() for entity in getattr(document, "entities", [])} - supported)
    if unsupported:
        report.add("WARNING", "UNSUPPORTED_ENTITIES", "Unsupported entity types were skipped: " + ", ".join(unsupported))

    for item in diagnostics or ():
        if item.get("type") == "door_opening_gap_tolerance":
            report.add("WARNING", "AUTO_CLOSED_DOOR_GAP", str(item.get("message", "A doorway gap was auto-closed.")))

    if not list(indoor_units or ()) and room_list:
        report.add("WARNING", "NO_INDOOR_UNITS", "No indoor-unit placements are present yet.")
    if report.warnings:
        for room_id, status in report.room_statuses.items():
            if status == "PASS":
                report.room_statuses[room_id] = "WARNING"
    return report
