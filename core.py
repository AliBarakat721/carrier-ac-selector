"""Pure-Python logic for the Carrier AC Selector (no Qt imports).

Everything here is unit-testable: database loading, HAP text parsing,
single-unit selection and multi-unit combinations.
"""
from __future__ import annotations

import math
import os
import re
from collections import Counter
from dataclasses import dataclass, field

import pandas as pd

BTU_PER_TON = 12_000.0
BTU_PER_KW = 3_412.14
# Typical sensible share of a split unit's total cooling capacity.
# Used only for an informational "Sensible OK" check, never to filter.
SENSIBLE_RATIO = 0.75
MAX_MULTI_UNITS = 4

EXPECTED_COLUMNS = [
    'Category', 'Series', 'System Model', 'Indoor Unit Model',
    'Outdoor Unit Model', 'Type', 'Cooling Capacity (Btu/hr)',
    'Cooling Capacity (kW)', 'Heating Capacity (Btu/hr)',
    'Heating Capacity (kW)', 'Airflow (CFM) L/M/H', 'Airflow (m3/h)',
    'EER (Btu/Wh)', 'Power Supply', 'Indoor Unit Dimensions (mm)',
    'Indoor Unit Weight (kg)', 'Approx. Area (m2)*', 'Notes',
    'High CFM (numeric)',
]
_NUMERIC_NA = {"", "—", "-", "N/A", "nan", "None"}


# ── Database ──────────────────────────────────────────────────────────────────
def to_float(value):
    """Return value as float, or None for blanks / em-dash / unparsable.

    Tolerates the '≈' prefix used for approximate catalogue values and
    distinguishes thousands separators from European decimal commas:
        '1,234.5' -> 1234.5    '1.234,5' -> 1234.5    '10,11' -> 10.11
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if pd.isna(value):
            return None
        result = float(value)
        return result if math.isfinite(result) else None
    s = str(value).replace("≈", "").strip()
    if s in _NUMERIC_NA:
        return None
    # Mirror parse_user_number's separator logic (decimal comma vs thousands).
    if ',' in s and '.' in s:
        if s.rfind('.') > s.rfind(','):
            s = s.replace(',', '')                     # 1,234.5 -> US
        else:
            s = s.replace('.', '').replace(',', '.')   # 1.234,5 -> EU
    elif ',' in s:
        if re.fullmatch(r'-?\d{1,3}(,\d{3})+', s):
            s = s.replace(',', '')                     # 1,234,567 -> thousands
        else:
            s = s.replace(',', '.')                    # 10,11 -> 10.11
    try:
        result = float(s)
        return result if math.isfinite(result) else None
    except ValueError:
        return None


def parse_user_number(text):
    """Parse finite user numbers with comma or dot thousands/decimal separators."""
    if text is None:
        return 0.0
    s = str(text).strip().replace(" ", "")
    if not s:
        return 0.0
    # استبعاد الكلمات غير الرقمية مثل nan, inf
    if re.search(r'[a-zA-Z]', s):
        return None

    if ',' in s and '.' in s:
        if s.rfind('.') > s.rfind(','):
            s = s.replace(',', '')
        else:
            s = s.replace('.', '').replace(',', '.')
    elif ',' in s:
        # إذا كانت الصيغة فاصل آلاف مثل 12,500 أو 1,000,000
        if re.fullmatch(r'\d{1,3}(,\d{3})+', s):
            s = s.replace(',', '')
        else:
            # فاصلة عشرية مثل 12,5
            s = s.replace(',', '.')
    elif re.fullmatch(r'\d{1,3}(?:\.\d{3})+', s):
        # A lone dot between three-digit groups is a thousands separator in
        # common European/Egyptian notation (e.g. 82.800 -> 82800).
        s = s.replace('.', '')

    try:
        val = float(s)
        if not math.isfinite(val) or val < 0:
            return None
        return val
    except ValueError:
        return None


def find_database(base_dir: str) -> str | None:
    """Look for the database in data/ and next to the script (xlsx or csv)."""
    names = ("Carrier_AC_Database.xlsx", "data_Carrier_AC_Database.xlsx",
             "Carrier_AC_Database.csv", "data_Carrier_AC_Database.csv")
    for folder in (os.path.join(base_dir, "data"), base_dir):
        for n in names:
            p = os.path.join(folder, n)
            if os.path.exists(p):
                return p
    return None


def load_database(path: str) -> pd.DataFrame:
    """Load + validate + normalise the catalogue.

    * columns are matched BY NAME (reordering a column no longer corrupts data)
    * numeric columns are converted (heating '—' -> NaN, EER '≈10.5' -> 10.5)
    * a 'CFM Approx' flag is derived from the '≈' marker
    * duplicate system models are merged into one row (series joined)
    """
    if path.lower().endswith(".csv"):
        raw = pd.read_csv(path, encoding="utf-8-sig")
    else:
        raw = pd.read_excel(path, sheet_name=0)
    raw.columns = [str(c).strip() for c in raw.columns]

    missing = [c for c in EXPECTED_COLUMNS if c not in raw.columns]
    if missing:
        raise RuntimeError(
            "Unexpected database format. Missing column(s): " + ", ".join(missing)
        )
    # الحفاظ على الأعمدة الأساسية وأي أعمدة إضافية موجودة في الإكسل (Outdoor specs)
    all_cols = EXPECTED_COLUMNS + [c for c in raw.columns if c not in EXPECTED_COLUMNS]
    df = raw[all_cols].copy()

    numeric_cols = [
        "Cooling Capacity (Btu/hr)", "Cooling Capacity (kW)",
        "Heating Capacity (Btu/hr)", "Heating Capacity (kW)",
        "EER (Btu/Wh)", "Indoor Unit Weight (kg)",
        "Approx. Area (m2)*", "High CFM (numeric)",
        "Outdoor Unit Weight (kg)", "Outdoor Sound dB(A)",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = df[col].map(to_float)

    df["CFM Approx"] = df["Airflow (CFM) L/M/H"].astype(str).str.contains("≈")
    # CFM is optional for capacity-only searches; filter missing airflow only when needed.
    df = df.dropna(subset=["Cooling Capacity (Btu/hr)"])

    # Merge duplicate models (same System Model listed under two series)
    dup = df.duplicated("System Model", keep=False)
    if dup.any():
        for model, grp in df[dup].groupby("System Model"):
            series = " / ".join(dict.fromkeys(grp["Series"].astype(str)))
            idx0 = grp.index[0]
            df.loc[idx0, "Series"] = series
            for col in grp.columns:
                if col == "Series":
                    continue
                v0 = grp[col].iloc[0]
                if pd.isna(v0) or str(v0).strip() in ("", "—", "-"):
                    non_nulls = [v for v in grp[col].dropna() if str(v).strip() not in ("", "—", "-")]
                    if non_nulls:
                        df.loc[idx0, col] = non_nulls[0]
        df = df.drop_duplicates("System Model", keep="first")
    return df.reset_index(drop=True)


# ── HAP parsing ───────────────────────────────────────────────────────────────
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
    from units import cooling_capacity_to_btu_h
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
    from units import airflow_to_l_s, l_s_to_airflow
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
    area = _value(r'(?:Room\s+Area|Area)\s*[:#]?\s*' + _NUM + r'\s*(m2|m²|ft2|ft²)', text, source_file=source_file, unit="m2")
    if area.verified:
        from units import area_to_m2
        area.value = area_to_m2(area.value, "ft2" if area.unit.lower().startswith("ft") else "m2")
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

# ── Selection ─────────────────────────────────────────────────────────────────
def _apply_category_type(df, category, type_, power_supply=None, series=None):
    """Apply every optional filter. None / "All ..." means "no filter"."""
    if category and category != "All Categories":
        df = df[df["Category"] == category]
    if type_ and type_ != "All Types":
        df = df[df["Type"] == type_]
    if series and series != "All Series":
        # مطابقة السلسلة حتى لو كان الموديل مسجلاً تحت أكثر من سلسلة مفصولة بـ /
        df = df[df["Series"].astype(str).apply(
            lambda s: series in [x.strip() for x in s.split("/")]
        )]
    if power_supply and power_supply != "All Supplies":
        df = df[df["Power Supply"] == power_supply]
    return df


def _validate_selection_inputs(btu, cfm, safety_pct, cfm_cover_pct,
                               derate_pct, sensible_btu=0.0):
    """Prevent NaN/inf inputs from turning filter comparisons into all-match results."""
    for name, value in (("btu", btu), ("cfm", cfm),
                        ("safety_pct", safety_pct),
                        ("cfm_cover_pct", cfm_cover_pct),
                        ("derate_pct", derate_pct),
                        ("sensible_btu", sensible_btu)):
        try:
            finite = math.isfinite(value)
        except TypeError:
            finite = False
        if not finite:
            raise ValueError(f"{name} must be a finite number.")
    if btu < 0 or cfm < 0 or sensible_btu < 0:
        raise ValueError("btu, cfm, and sensible_btu cannot be negative.")
    if not 0.0 <= safety_pct <= 100.0:
        raise ValueError("safety_pct must be between 0 and 100.")
    if not 0.0 <= cfm_cover_pct <= 100.0:
        raise ValueError("cfm_cover_pct must be between 0 and 100.")
    if not 0.0 <= derate_pct < 100.0:
        raise ValueError("derate_pct must be in the range [0, 100).")


def select_units(df, btu, cfm, *, safety_pct=0.0, cfm_cover_pct=100.0,
                 derate_pct=0.0, category=None, type_=None,
                 power_supply=None, series=None,
                 sensible_btu=0.0) -> pd.DataFrame:
    """Single units meeting the load.

    safety_pct     extra margin on the required load
    cfm_cover_pct  required share of the HAP coil CFM the unit's HIGH speed
                   must deliver (HAP CFM is a design value at 55 F supply, so
                   100 % is optional strictness)
    derate_pct     capacity loss at design ambient vs catalogue rating
    Adds columns: Effective Btu, Excess %, CFM Coverage %, Sensible OK.
    Ranked by smallest excess, then highest EER, then highest CFM.
    """
    _validate_selection_inputs(btu, cfm, safety_pct, cfm_cover_pct,
                               derate_pct, sensible_btu)
    target_btu = btu * (1.0 + safety_pct / 100.0)
    need_cfm = cfm * cover_factor(cfm_cover_pct)

    out = _apply_category_type(df, category, type_,
                               power_supply=power_supply, series=series).copy()
    out["Effective Btu"] = out["Cooling Capacity (Btu/hr)"] * (1 - derate_pct / 100.0)
    mask = pd.Series(True, index=out.index)
    if target_btu > 0:
        mask &= out["Effective Btu"] >= target_btu
    if need_cfm > 0:
        mask &= out["High CFM (numeric)"] >= need_cfm
    out = out[mask].copy()

    base = btu if btu > 0 else None
    out["Excess %"] = ((out["Effective Btu"] - base) / base * 100.0) if base else 0.0
    out["CFM Coverage %"] = (out["High CFM (numeric)"] / cfm * 100.0) if cfm > 0 else float("nan")
    if sensible_btu > 0:
        out["Sensible OK"] = (out["Effective Btu"] * SENSIBLE_RATIO >= sensible_btu
                              ).map({True: "✔", False: "✘"})
    else:
        out["Sensible OK"] = "—"

    out["_eer"] = out["EER (Btu/Wh)"].fillna(0)
    if base:
        sort_cols, asc = ["Excess %", "_eer", "High CFM (numeric)"], [True, False, False]
    else:
        # CFM-only search: pick the unit whose airflow is closest to the
        # requested CFM (smallest coverage >= cover_pct) instead of the
        # arbitrary "highest CFM wins" tie-break.
        sort_cols, asc = ["CFM Coverage %", "_eer"], [True, False]
    out = out.sort_values(sort_cols, ascending=asc) \
             .drop(columns="_eer").reset_index(drop=True)
    return out


def cover_factor(cover_pct: float) -> float:
    return max(0.0, min(cover_pct, 100.0)) / 100.0


def multi_unit_options(df, btu, cfm, *, safety_pct=0.0, cfm_cover_pct=100.0,
                       derate_pct=0.0, category=None, type_=None,
                       power_supply=None, series=None,
                       max_units=MAX_MULTI_UNITS) -> pd.DataFrame:
    """Minimum number of identical units to cover load and airflow.

    Respects the Category / Type filters, the safety factor, derating and the
    CFM coverage; options needing more than `max_units` units are dropped.
    """
    _validate_selection_inputs(btu, cfm, safety_pct, cfm_cover_pct, derate_pct)
    if isinstance(max_units, bool) or not isinstance(max_units, int) or max_units < 1:
        raise ValueError("max_units must be a positive integer.")
    target_btu = btu * (1.0 + safety_pct / 100.0)
    need_cfm = cfm * cover_factor(cfm_cover_pct)
    if target_btu <= 0 and need_cfm <= 0:
        return pd.DataFrame()

    rows = []
    filtered_df = _apply_category_type(df, category, type_,
                                       power_supply=power_supply, series=series)
    for _, r in filtered_df.iterrows():
        unit_btu = r["Cooling Capacity (Btu/hr)"] * (1 - derate_pct / 100.0)
        unit_cfm = r["High CFM (numeric)"]
        if not math.isfinite(unit_btu) or unit_btu <= 0:
            continue
        has_airflow = math.isfinite(unit_cfm) and unit_cfm > 0
        if need_cfm > 0 and not has_airflow:
            continue
        n = max(math.ceil(target_btu / unit_btu) if target_btu > 0 else 1,
                math.ceil(need_cfm / unit_cfm) if need_cfm > 0 else 1)
        if n > max_units:
            continue
        total_btu = n * unit_btu
        rows.append({
            "Units": n,
            "Category": r["Category"], "Series": r["Series"],
            "System Model": r["System Model"],
            "Unit Btu": unit_btu, "Total Btu": total_btu,
            "Excess %": ((total_btu - btu) / btu * 100.0) if btu > 0 else 0.0,
            "Unit CFM": unit_cfm if has_airflow else float("nan"),
            "Total CFM": n * unit_cfm if has_airflow else float("nan"),
            "EER": r["EER (Btu/Wh)"], "Type": r["Type"],
        })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["Units", "Excess %"]).reset_index(drop=True)


# ── Multi-system (batch) parsing ─────────────────────────────────────────────
@dataclass
class SystemLoad:
    """Cooling requirements of ONE air system in a multi-system report."""
    name: str = ""
    btu: float = 0.0
    cfm: float = 0.0
    sensible_btu: float = 0.0
    warnings: list = field(default_factory=list)

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
        load = SystemLoad(name, r.btu, r.cfm, r.sensible_btu, r.warnings)

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
            )
            idx = next(j for j, item in enumerate(systems) if item.name == name)
            systems[idx] = merged
            by_name[name] = merged
    return systems


def batch_select(df, systems, *, safety_pct=0.0, cfm_cover_pct=100.0,
                 derate_pct=0.0, category=None, type_=None,
                 power_supply=None, series=None) -> pd.DataFrame:
    """Best unit option for every parsed system, in one summary table.

    Single unit first -> fallback: minimum identical multi-unit combo ->
    otherwise 'No match' / 'Not parsed'.
    """
    rows = []
    for s in systems:
        base = {
            "System": s.name,
            "Load (Btu/hr)": s.btu,
            "Load (Tons)": s.btu / BTU_PER_TON,
            "Sensible (Btu/hr)": s.sensible_btu,
            "Required CFM": s.cfm,
            "Notes": "; ".join(s.warnings),
        }
        empty = {"Units": 0, "System Model": "", "Indoor Unit": "",
                 "Outdoor Unit": "", "Unit Btu": float("nan"),
                 "Total Btu": float("nan"), "Excess %": float("nan"),
                 "Unit CFM": float("nan"), "Total CFM": float("nan"),
                 "CFM Cover %": float("nan"), "Sensible OK": "\u2014",
                 "EER": float("nan"), "Alternatives": 0}

        # أي سيستم بدون حمل تبريد صالح يُصنف Not parsed ولا تُرشح له وحدات
        if s.btu <= 0:
            rows.append({**base, "Status": "Not parsed", **empty})
            continue

        kw = dict(safety_pct=safety_pct, cfm_cover_pct=cfm_cover_pct,
                  derate_pct=derate_pct, category=category, type_=type_,
                  power_supply=power_supply, series=series)
        single = select_units(df, s.btu, s.cfm,
                              sensible_btu=s.sensible_btu, **kw)
        if len(single) > 0:
            b = single.iloc[0]
            rows.append({**base,
                "Status": "Single unit", "Units": 1,
                "System Model": b["System Model"],
                "Indoor Unit": b["Indoor Unit Model"],
                "Outdoor Unit": b["Outdoor Unit Model"],
                "Unit Btu": b["Effective Btu"], "Total Btu": b["Effective Btu"],
                "Excess %": b["Excess %"],
                "Unit CFM": b["High CFM (numeric)"],
                "Total CFM": b["High CFM (numeric)"],
                "CFM Cover %": b["CFM Coverage %"],
                "Sensible OK": b["Sensible OK"],
                "EER": b["EER (Btu/Wh)"],
                "Alternatives": len(single) - 1})
            continue

        multi = multi_unit_options(df, s.btu, s.cfm, **kw)
        if len(multi) > 0:
            m = multi.iloc[0]
            rows.append({**base,
                "Status": f"Multi-unit ({int(m['Units'])}x)",
                "Units": int(m["Units"]),
                "System Model": m["System Model"],
                "Indoor Unit": "", "Outdoor Unit": "",
                "Unit Btu": m["Unit Btu"], "Total Btu": m["Total Btu"],
                "Excess %": m["Excess %"],
                "Unit CFM": m["Unit CFM"], "Total CFM": m["Total CFM"],
                "CFM Cover %": (m["Total CFM"] / s.cfm * 100.0
                                if s.cfm > 0 else float("nan")),
                "Sensible OK": "\u2014", "EER": m["EER"],
                "Alternatives": len(multi) - 1})
        else:
            rows.append({**base, "Status": "No match", **empty})
    return pd.DataFrame(rows)
