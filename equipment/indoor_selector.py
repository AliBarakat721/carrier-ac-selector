"""Indoor-unit selection rules (pure pandas, no Qt, no file I/O).

Moved verbatim from ``core.py`` (Phase 1). ``core`` re-exports every name.
"""
from __future__ import annotations

import math

import pandas as pd

from engineering.units import BTU_PER_TON

# Typical sensible share of a split unit's total cooling capacity.
# Used only for an informational "Sensible OK" check, never to filter.
SENSIBLE_RATIO = 0.75
MAX_MULTI_UNITS = 4


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
