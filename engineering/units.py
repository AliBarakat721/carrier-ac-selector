"""Shared unit constants and tolerant numeric parsing (no Qt, no pandas).

Moved verbatim from ``core.py`` (Phase 1). ``core`` re-exports every name.
"""
from __future__ import annotations

import math
import re

import pandas as pd

BTU_PER_TON = 12_000.0
BTU_PER_KW = 3_412.14
_NUMERIC_NA = {"", "—", "-", "N/A", "nan", "None"}


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
