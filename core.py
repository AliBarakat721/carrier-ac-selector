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
        return None if pd.isna(value) else float(value)
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
        return float(s)
    except ValueError:
        return None


def parse_user_number(text):
    """Safe user number parsing: handles inf/nan, commas as decimals or thousands."""
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
    df = df.dropna(subset=["Cooling Capacity (Btu/hr)", "High CFM (numeric)"])

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
@dataclass
class HapResult:
    btu: float = 0.0              # total cooling coil load, Btu/hr
    cfm: float = 0.0              # cooling coil CFM
    sensible_btu: float = 0.0     # sensible coil load, Btu/hr (0 = unknown)
    warnings: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.btu > 0 and self.cfm > 0 and not self.warnings


_NUM = r'(?<!\d)(?<!\d\.)(\d+(?:\.\d+)?)'
_LEADER = r'[^\n]{0,120}?'            # junk between label and value (dot leaders)


def _cooling_section(t: str) -> str:
    # يتوقف عند بداية أي قسم تالٍ (تسخين، مروحة، هواء خارجي، أو بداية نظام جديد)
    m = re.search(
        r'Cooling\s+Co[a-z0-9]{0,3}l.*?(?=(?:Heating\s+Co[a-z0-9]{0,3}l|Supply\s+Fan|Outdoor\s+Air|Terminal\s+Unit|\bAir\s+System\s+Information|\bAir\s+System\s+Sizing\s+Summary)\b|\Z)',
        t, re.I | re.S
    )
    return m.group(0) if m else ""  # إرجاع نص فارغ بدلاً من t لمنع تسريب أقسام أخرى


def _normalize_rtf_ws(s: str) -> str:
    """HAP's RTF output puts labels and their values on separate lines
    joined by TAB+NEWLINE. Collapse those separators into double spaces
    so the single-line leader patterns keep working unchanged across OSes.
    """
    if not s:
        return ""
    # Normalize Windows/Mac line endings (CRLF / CR -> LF)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("\t\n", "  ").replace("\n\t", "  ")
    return s


def parse_hap_text(text: str) -> HapResult:
    """Extract cooling load (Btu/hr) and coil CFM from HAP summary text.

    Rules (learned from real OCR output):
      * only the *Central Cooling Coil* section is searched, so heating /
        fan / ventilation numbers (CFM/person, Max coil load...) can't leak in
      * the load comes from the labelled 'Total coil load' line; MBH is the
        reference (Tons is rounded and only used to cross-check)
      * there are NO generic fallbacks: if the labelled value can't be read we
        return 0 plus a warning instead of guessing (a silent wrong value is
        worse than an explicit failure)
    """
    res = HapResult()
    t = re.sub(r'(\d),(\d{3})', r'\1\2', text or "")
    t = _normalize_rtf_ws(t)         # ← RTF label-value join
    cool = _cooling_section(t)
    if not cool:
        res.warnings.append("Cooling coil section not found in report.")
        return res

    # --- Total coil load (فحص جميع الأسطر المطابقة مع دعم الكلمات الملتصقة مثل coilload) ---
    total_lines = [
        line for line in cool.splitlines()
        if re.search(r'\bTotal\b', line, re.I) and (
            re.search(r'(?:coil|load|co[a-z0-9]{1,6}|loa[a-z0-9]{1,6})', line, re.I)
            or re.search(r'\b(?:MBH|Tons?)\b', line, re.I)
        ) and not re.search(r'\bcapacity\b', line, re.I)
    ]
    if not total_lines:
        total_lines = [
            line for line in cool.splitlines()
            if re.search(r'\bTotal\b', line, re.I) and re.search(r'\bMBH\b', line, re.I)
        ]

    mbh = tons = None
    for ln in total_lines:
        if mbh is None:
            m = re.search(_NUM + r'\s*MBH', ln, re.I)
            if m:
                mbh = float(m.group(1))
        if tons is None:
            m = re.search(_NUM + r'\s*Tons?', ln, re.I)
            if m:
                tons = float(m.group(1))
        if mbh is not None and tons is not None:
            break

    if mbh is None and tons is None:
        # label unreadable: do NOT guess from other lines
        any_mbh = re.findall(_NUM + r'\s*MBH', cool, re.I)
        if any_mbh:
            res.warnings.append(
                "Could not read the 'Total coil load' line "
                "(other MBH values were seen but ignored). Enter it manually."
            )
        else:
            res.warnings.append("Cooling load not found. Enter it manually.")
    else:
        if mbh is not None and tons is not None:
            if abs(mbh / 12.0 - tons) > 0.06 * max(tons, 1.0) + 0.05:
                # disagreement -> keep the larger (safe) one and warn
                res.warnings.append(
                    f"Tons ({tons:g}) and MBH ({mbh:g}) disagree - "
                    "using the larger; please verify."
                )
                res.btu = max(mbh * 1000.0, tons * BTU_PER_TON)
            else:
                res.btu = mbh * 1000.0          # MBH is the precise value
        elif mbh is not None:
            res.btu = mbh * 1000.0
        else:
            res.btu = tons * BTU_PER_TON
            res.warnings.append(
                "Only the rounded Tons value was readable; "
                "Btu/hr is approximate."
            )

    # --- Sensible load (مطابقة سطر الحمل المحسوس من نفس السطر) ---
    m_sens_line = re.search(
        r'(?m)^[^\n]*\bSensible\b[^\n]{0,40}?\b(?:coil|load|co[a-z0-9]{1,4}|loa[a-z0-9]{1,4})\b[^\n]*',
        cool, re.I
    )
    if not m_sens_line:
        m_sens_line = re.search(r'(?m)^[^\n]*\bSensible\b[^\n]*\bMBH\b[^\n]*', cool, re.I)
    if m_sens_line:
        m_s = re.search(_NUM + r'\s*MBH', m_sens_line.group(0), re.I)
        if m_s:
            res.sensible_btu = float(m_s.group(1)) * 1000.0
            if res.btu and res.sensible_btu > res.btu * 1.02:
                res.warnings.append("Sensible load exceeds total load - OCR error?")

    # --- coil CFM ---
    patterns = [
        r'Coil\s*CFM\s+at\s+[A-Za-z]{3}\s*\d{0,4}',
        r'Max\s+block\s+CFM',
        r'Sum\s+of\s+peak\s+zone\s+CFM',
    ]
    vals = []
    for p in patterns:
        m = re.search(p + _LEADER + r'(?<!\d)(?<!\d\.)(\d{2,6})\D{0,3}CFM\b(?!\s*/)', cool, re.I)
        if m:
            v = float(m.group(1))
            if 50 <= v <= 200_000:
                vals.append(v)
    if vals:
        res.cfm = Counter(vals).most_common(1)[0][0]
        if len(set(vals)) > 1:
            res.warnings.append(
                f"CFM lines disagree ({', '.join(f'{v:g}' for v in vals)}) - "
                "please verify."
            )
    else:
        res.warnings.append("Coil CFM not found. Enter it manually.")
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
        if unit_btu <= 0 or unit_cfm <= 0:
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
            "Unit CFM": unit_cfm, "Total CFM": n * unit_cfm,
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


# HAP emits TWO header layouts for the SAME system:
#   PNG/PDF: 'Air System Sizing Summary for <name>'
#   RTF:     'Air System Information ... Air System Name <name>'
# The info-block header appears in BOTH formats, so we anchor on it.
# Name = letter+digit token (skips OCR noise like 'ooo ccc... s').
_SYSTEM_HEADER = re.compile(
    r'Air\s+System\s+Information'
    r'[\s\S]{0,300}?Air\s+System\s+Name\b'
    r'[\s\S]{0,200}?\b([A-Za-z][A-Za-z0-9_\-]*\d[A-Za-z0-9_\-]*)\b',
    re.I)

# Lenient fallback: names WITHOUT digits (e.g. AHU-MAIN, ZONE-EAST).
# Only used when the strict pattern finds nothing — so noisy OCR text
# (which the strict \d-guard protects against) can never reach this path.
_SYSTEM_HEADER_LENIENT = re.compile(
    r'Air\s+System\s+Information'
    r'[\s\S]{0,300}?Air\s+System\s+Name\b[\s.\-_]*'
    r'([A-Za-z0-9][A-Za-z0-9_\-]*)\b',
    re.I)


def parse_all_systems(text: str) -> list:
    """Parse EVERY air system in a full HAP "Systems Design Report".

    The header 'Air System Sizing Summary for <name>' repeats once per
    system (and again per page for multi-page systems). We split on it,
    apply the same strict parse_hap_text rules to each chunk, and keep
    the first chunk per name that yields a cooling load.
    """
    t = re.sub(r'(\d),(\d{3})', r'\1\2', text or "")
    matches = (list(_SYSTEM_HEADER.finditer(t))
               or list(_SYSTEM_HEADER_LENIENT.finditer(t)))
    systems: list = []
    by_name: dict = {}

    for i, m in enumerate(matches):
        name = m.group(1).strip()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(t)
        chunk = t[m.start():end]

        r = parse_hap_text(chunk)
        load = SystemLoad(name, r.btu, r.cfm, r.sensible_btu, r.warnings)

        prev = by_name.get(name)
        if prev is None:
            by_name[name] = load
            systems.append(load)
        elif prev.btu <= 0 < load.btu:
            idx = next(j for j, s in enumerate(systems) if s.name == name)
            systems[idx] = load
            by_name[name] = load
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
