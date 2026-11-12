"""HAP report text parsing (single-system and multi-system reports).

Moved verbatim from ``core.py`` (Phase 1). ``core`` re-exports every name.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from engineering.units import BTU_PER_TON

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


def parse_all_systems(text: str) -> list:
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

        r = parse_hap_text(chunk)
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
