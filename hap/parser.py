"""HAP report parsing with room-level extraction, provenance, and legacy compatibility."""
from __future__ import annotations
import math
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from engineering.units import BTU_PER_TON, area_to_m2

@dataclass(frozen=True)
class SourceRef:
    """Traceability for an extracted value in a report."""
    source_file: str | None = None
    page: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    excerpt: str | None = None

    @property
    def location(self) -> str:
        bits = []
        if self.source_file:
            bits.append(os.path.basename(self.source_file))
        if self.page is not None:
            bits.append(f"page {self.page}")
        if self.line_start is not None:
            bits.append(f"lines {self.line_start}-{self.line_end or self.line_start}")
        return ", ".join(bits) or "NOT_VERIFIED"


@dataclass
class ExtractedValue:
    value: object = None
    unit: str | None = None
    status: str = "MISSING"
    source: SourceRef = field(default_factory=SourceRef)

    @property
    def verified(self) -> bool:
        return self.status == "EXTRACTED" and self.value is not None


@dataclass
class ThermalValidation:
    status: str = "NOT_VERIFIED"
    total_btu: float | None = None
    sensible_btu: float | None = None
    latent_btu: float | None = None
    difference_btu: float | None = None
    difference_pct: float | None = None
    message: str = "Thermal balance was not verified."


@dataclass
class HapRoom:
    room_name: ExtractedValue = field(default_factory=ExtractedValue)
    room_number: ExtractedValue = field(default_factory=ExtractedValue)
    total_cooling_load: ExtractedValue = field(default_factory=ExtractedValue)
    sensible_load: ExtractedValue = field(default_factory=ExtractedValue)
    latent_load: ExtractedValue = field(default_factory=ExtractedValue)
    heating_load: ExtractedValue = field(default_factory=ExtractedValue)
    supply_airflow: ExtractedValue = field(default_factory=ExtractedValue)
    room_area: ExtractedValue = field(default_factory=ExtractedValue)
    zone: ExtractedValue = field(default_factory=ExtractedValue)
    design_conditions: ExtractedValue = field(default_factory=ExtractedValue)
    thermal_validation: ThermalValidation = field(default_factory=ThermalValidation)

    @property
    def name(self) -> str:
        return str(self.room_name.value or self.room_number.value or "")

    @property
    def status(self) -> str:
        required = (self.total_cooling_load, self.sensible_load, self.supply_airflow)
        return "EXTRACTED" if all(v.verified for v in required) else "NOT_VERIFIED"


@dataclass
class HapResult:
    """Legacy single-result view of a HAP text report.

    For reports containing multiple rooms, use :func:`parse_hap_rooms` rather
    than treating ``btu``/``cfm`` as a sum of room loads.
    """
    # Legacy fields retained for existing selection/UI callers.
    btu: float = 0.0
    cfm: float = 0.0
    sensible_btu: float = 0.0
    warnings: list = field(default_factory=list)
    room: HapRoom | None = None
    source: SourceRef = field(default_factory=SourceRef)
    thermal_validation: ThermalValidation = field(default_factory=ThermalValidation)

    @property
    def ok(self) -> bool:
        return self.btu > 0 and self.cfm > 0 and not self.warnings

    @property
    def has_required_loads(self) -> bool:
        """Whether BTU and CFM were extracted, regardless of warnings."""
        return self.btu > 0 and self.cfm > 0


_NUM = r'(?<!\d)(?<!\d\.)(\d+(?:[.,]\d+)?)'
_LEADER = r'[^\n]{0,160}?'


def _source_for(text: str, start: int, end: int, source_file=None) -> SourceRef:
    before = text[:start]
    page_matches = list(re.finditer(r'---\s*page\s+(\d+)', before, re.I))
    page = int(page_matches[-1].group(1)) if page_matches else None
    line_start = before.count("\n") + 1
    line_end = line_start + text[start:end].count("\n")
    excerpt = text[start:end].strip().replace("\n", " ")[:240]
    return SourceRef(source_file, page, line_start, line_end, excerpt)


def _float_token(value: str) -> float:
    if "," in value and "." not in value:
        value = value.replace(",", "") if re.fullmatch(r"\d{1,3}(,\d{3})+", value) else value.replace(",", ".")
    elif "," in value and "." in value:
        value = value.replace(",", "") if value.rfind(".") > value.rfind(",") else value.replace(".", "").replace(",", ".")
    return float(value)


def _value(pattern: str, text: str, *, source_file=None, unit=None,
           flags=re.I | re.M, converter=None) -> ExtractedValue:
    match = re.search(pattern, text, flags)
    if not match:
        return ExtractedValue(status="MISSING")
    raw = match.group(1).strip()
    try:
        value = converter(raw) if converter else raw
    except (TypeError, ValueError):
        return ExtractedValue(raw, unit, "NOT_VERIFIED", _source_for(text, match.start(), match.end(), source_file))
    return ExtractedValue(value, unit, "EXTRACTED", _source_for(text, match.start(), match.end(), source_file))


def thermal_validate(total_btu: float | None, sensible_btu: float | None,
                     latent_btu: float | None, *, tolerance_pct: float = 2.0) -> ThermalValidation:
    values = (total_btu, sensible_btu, latent_btu)
    if any(v is None or not math.isfinite(float(v)) for v in values):
        return ThermalValidation(message="Total, sensible, and latent loads are required.")
    difference = float(total_btu) - (float(sensible_btu) + float(latent_btu))
    pct = abs(difference) / max(abs(float(total_btu)), 1.0) * 100.0
    status = "VALID" if pct <= tolerance_pct else "WARNING"
    message = ("QTotal = QSensible + QLatent verified within tolerance."
               if status == "VALID" else
               f"Thermal loads are inconsistent by {difference:g} Btu/h ({pct:.2f}%).")
    return ThermalValidation(status, float(total_btu), float(sensible_btu),
                             float(latent_btu), difference, pct, message)


def _cooling_section(t: str) -> str:
    m = re.search(
        r'Cooling\s+Co[a-z0-9]{0,3}l.*?(?=(?:Heating\s+Co[a-z0-9]{0,3}l|Supply\s+Fan|Outdoor\s+Air|Terminal\s+Unit|\bAir\s+System\s+Information|\bAir\s+System\s+Sizing\s+Summary)\b|\Z)',
        t, re.I | re.S)
    return m.group(0) if m else ""


def _normalize_rtf_ws(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    return s.replace("\t\n", "  ").replace("\n\t", "  ")


def _load_value(text: str, label: str, *, source_file=None) -> ExtractedValue:
    pattern = rf'{label}[^\n]*?{_NUM}\s*(MBH|Tons?|kW|Btu/?h|Btu/hr)'
    m = re.search(pattern, text, re.I)
    if not m:
        return ExtractedValue(status="MISSING")
    number = _float_token(m.group(1))
    unit = m.group(2).lower()
    from engineering.units import cooling_capacity_to_btu_h
    normalized = cooling_capacity_to_btu_h(number, "tr" if unit.startswith("ton") else
                                           "kw" if unit == "kw" else "btu/h")
    if unit == "mbh":
        normalized = number * 1000.0
    return ExtractedValue(normalized, "Btu/h", "EXTRACTED",
                          _source_for(text, m.start(), m.end(), source_file))


def _airflow_value(text: str, *, source_file=None) -> ExtractedValue:
    m = re.search(r'(?:Coil\s*CFM[^\n]*?|Supply\s+Air[^\n]*?|Airflow[^\n]*?)'
                  rf'{_NUM}\s*(CFM|L/s|m3/h|m³/h)', text, re.I)
    if not m:
        return ExtractedValue(status="MISSING")
    from engineering.units import airflow_to_l_s, l_s_to_airflow
    unit = m.group(2).lower().replace("³", "3")
    raw = _float_token(m.group(1))
    cfm = raw if unit == "cfm" else l_s_to_airflow(
        airflow_to_l_s(raw, "l/s" if unit == "l/s" else "m3/h"), "cfm"
    )
    return ExtractedValue(cfm, "CFM", "EXTRACTED", _source_for(text, m.start(), m.end(), source_file))


def _room_blocks(text: str) -> list[str]:
    lines = text.splitlines()
    # Prefer a room/space number as the record boundary.  Room Name and Zone
    # Name are fields inside that record, not new records.
    starts = [i for i, line in enumerate(lines) if re.search(
        r'\b(?:Room|Space)\s+(?:Number|No\.?)\b', line, re.I)]
    if not starts:
        starts = [i for i, line in enumerate(lines) if re.search(
            r'\b(?:Room|Space)\s+Name\b', line, re.I)]
    if not starts:
        return [text]
    blocks = []
    for j, i in enumerate(starts):
        page_marker = next(
            (lines[k] for k in range(i - 1, -1, -1)
             if re.search(r'---\s*page\s+\d+', lines[k], re.I)),
            None,
        )
        end = starts[j + 1] if j + 1 < len(starts) else len(lines)
        block = lines[i:end]
        if page_marker and (not block or block[0] != page_marker):
            block = [page_marker] + block
        blocks.append("\n".join(block))
    return blocks


def parse_hap_room(text: str, *, source_file: str | None = None) -> HapRoom:
    text = _normalize_rtf_ws(text or "")
    room_name = _value(r'(?:Room\s+Name|Space\s+Name)\s*[:#]?\s*([^\n]+)', text, source_file=source_file)
    room_number = _value(r'(?:Room\s+Number|Space\s+Number|Room\s+No\.?)\s*[:#]?\s*([^\n]+)', text, source_file=source_file)
    total = _load_value(text, r'(?:Total\s+(?:cooling|coil)\s+load|Cooling\s+load)', source_file=source_file)
    sensible = _load_value(text, r'Sensible\s+(?:cooling\s+)?(?:load|coil\s+load)', source_file=source_file)
    latent = _load_value(text, r'Latent\s+(?:cooling\s+)?(?:load|coil\s+load)', source_file=source_file)
    heating = _load_value(text, r'(?:Total\s+)?Heating\s+(?:load|coil\s+load)', source_file=source_file)
    airflow = _airflow_value(text, source_file=source_file)
    area_match = re.search(r'(?:Room\s+Area|Area)\s*[:#]?\s*' + _NUM + r'\s*(m2|m²|ft2|ft²)', text, re.I)
    if area_match:
        raw_area = _float_token(area_match.group(1))
        raw_unit = area_match.group(2).lower().replace("²", "2")
        area = ExtractedValue(raw_area, raw_unit, "EXTRACTED", _source_for(text, area_match.start(), area_match.end(), source_file))
        area.value = area_to_m2(area.value, raw_unit)
        area.unit = "m2"
    else:
        area = ExtractedValue(status="MISSING")
    zone = _value(r'(?:Zone\s+(?:Name|Number)|Zone)\s*[:#]?\s*([^\n]+)', text, source_file=source_file)
    design = _value(r'(Design\s+Conditions?[^\n]*)', text, source_file=source_file)
    validation = thermal_validate(total.value if total.verified else None,
                                  sensible.value if sensible.verified else None,
                                  latent.value if latent.verified else None)
    return HapRoom(room_name, room_number, total, sensible, latent, heating,
                   airflow, area, zone, design, validation)


def parse_hap_rooms(text: str, *, source_file: str | None = None) -> list[HapRoom]:
    """Extract room-level loads from common HAP room/space report layouts."""
    rooms = [parse_hap_room(block, source_file=source_file) for block in _room_blocks(text or "")]
    return [room for room in rooms if room.room_name.verified or room.room_number.verified]


def parse_hap_text(text: str, *, source_file: str | None = None) -> HapResult:
    """Extract legacy coil values and expose room/thermal/provenance data."""
    res = HapResult(source=SourceRef(source_file))
    t = re.sub(r'(\d),(\d{3})', r'\1\2', _normalize_rtf_ws(text or ""))
    cool = _cooling_section(t) or t
    room = parse_hap_room(t, source_file=source_file)
    res.room = room
    if room.total_cooling_load.verified:
        res.btu = float(room.total_cooling_load.value)
    if room.sensible_load.verified:
        res.sensible_btu = float(room.sensible_load.value)
    if room.supply_airflow.verified:
        res.cfm = float(room.supply_airflow.value)
    else:
        for pattern in (r'Max\s+block\s+CFM', r'Sum\s+of\s+peak\s+zone\s+CFM'):
            m = re.search(pattern + _LEADER + rf'(\d{{2,6}})\D{{0,3}}CFM\b', cool, re.I)
            if m:
                res.cfm = float(m.group(1)); break
    res.thermal_validation = room.thermal_validation
    if res.btu <= 0:
        res.warnings.append("Cooling load not found. Enter it manually.")
    if res.cfm <= 0:
        res.warnings.append("Coil CFM not found. Enter it manually.")
    if res.sensible_btu > res.btu > 0:
        res.warnings.append("Sensible load exceeds total load - OCR error?")
    if res.thermal_validation.status == "WARNING":
        res.warnings.append(res.thermal_validation.message)
    return res

@dataclass
class SystemLoad:
    """Cooling requirements of ONE air system in a multi-system report."""
    name: str = ""
    btu: float = 0.0
    cfm: float = 0.0
    sensible_btu: float = 0.0
    warnings: list = field(default_factory=list)
    source: SourceRef = field(default_factory=SourceRef)

    @property
    def ok(self) -> bool:
        return self.btu > 0 and self.cfm > 0


# HAP emits two header layouts for the SAME system:
#   PNG/PDF: 'Air System Sizing Summary for <name>'
#   RTF:     'Air System Information ... Air System Name <name>'
# Keep both patterns because PDF/PNG exports may not contain the information
# block at all.  Name = letter+digit token (skips OCR noise like 'ooo ccc... s').
_SYSTEM_HEADER = re.compile(
    r'Air\s+System\s+Information'
    r'[\s\S]{0,300}?Air\s+System\s+Name\b'
    r'[\s\S]{0,200}?\b([A-Za-z][A-Za-z0-9_\-]*\d[A-Za-z0-9_\-]*)\b',
    re.I)

_SYSTEM_SUMMARY_HEADER = re.compile(
    r'Air\s+System\s+Sizing\s+Summary\s+for\s+'
    r'\b([A-Za-z][A-Za-z0-9_\-]*\d[A-Za-z0-9_\-]*)\b',
    re.I)

# Lenient fallback: names WITHOUT digits (e.g. AHU-MAIN, ZONE-EAST).  These
# are used only when no strict match exists for the same header layout, so
# noisy OCR text cannot normally reach this path.
_SYSTEM_HEADER_LENIENT = re.compile(
    r'Air\s+System\s+Information'
    r'[\s\S]{0,300}?Air\s+System\s+Name\b[\s.\-_]*'
    r'([A-Za-z0-9][A-Za-z0-9_\-]*)\b',
    re.I)

_SYSTEM_SUMMARY_HEADER_LENIENT = re.compile(
    r'Air\s+System\s+Sizing\s+Summary\s+for\s+'
    r'([A-Za-z0-9][A-Za-z0-9_\-]*)\b',
    re.I)


def parse_all_systems(text: str, *, source_file: str | None = None) -> list:
    """Parse EVERY air system in a full HAP "Systems Design Report".

    The header 'Air System Sizing Summary for <name>' repeats once per
    system (and again per page for multi-page systems). We split on it,
    apply the same strict parse_hap_text rules to each chunk, and keep
    the first chunk per name that yields a cooling load.
    """
    t = re.sub(r'(\d),(\d{3})', r'\1\2', text or "")
    # Collect both layouts and preserve their order.  Do not use ``or`` here:
    # a report can contain a mixture of RTF-style and PDF-style headers.
    def _merge_layout_matches(strict_pattern, lenient_pattern):
        strict = list(strict_pattern.finditer(t))
        strict_starts = {m.start() for m in strict}
        # Keep lenient matches for names without digits even when another
        # system in the same report has a digit-bearing name.  At a strict
        # match's start, prefer the strict match to avoid duplicates.
        return strict + [m for m in lenient_pattern.finditer(t)
                         if m.start() not in strict_starts]

    info_matches = _merge_layout_matches(_SYSTEM_HEADER, _SYSTEM_HEADER_LENIENT)
    summary_matches = _merge_layout_matches(
        _SYSTEM_SUMMARY_HEADER, _SYSTEM_SUMMARY_HEADER_LENIENT)
    matches = sorted(info_matches + summary_matches, key=lambda m: m.start())
    systems: list = []
    by_name: dict = {}

    for i, m in enumerate(matches):
        name = m.group(1).strip()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(t)
        chunk = t[m.start():end]

        r = parse_hap_text(chunk, source_file=source_file)
        load = SystemLoad(
            name, r.btu, r.cfm, r.sensible_btu, r.warnings, r.source
        )

        prev = by_name.get(name)
        if prev is None:
            by_name[name] = load
            systems.append(load)
        else:
            # Merge repeated sections by field completeness, not BTU alone.
            merged = SystemLoad(
                name=name,
                btu=load.btu if load.btu > 0 else prev.btu,
                cfm=load.cfm if load.cfm > 0 else prev.cfm,
                sensible_btu=(load.sensible_btu if load.sensible_btu > 0
                              else prev.sensible_btu),
                warnings=list(dict.fromkeys(prev.warnings + load.warnings)),
                source=load.source if load.btu > 0 or load.cfm > 0 else prev.source,
            )
            idx = next(j for j, item in enumerate(systems) if item.name == name)
            systems[idx] = merged
            by_name[name] = merged
    return systems
