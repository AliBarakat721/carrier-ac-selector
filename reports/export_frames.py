"""Build export-ready DataFrames from selection results (moved from main.py). No Qt."""
from __future__ import annotations

import re

import pandas as pd

from engineering.units import BTU_PER_TON


def prepare_export_df(df, system_name=""):
    """Copy of df for export: helper columns rounded, NaN -> '', reordered."""
    out = df.copy()
    # Prepend the active HAP system name so the file is self-describing
    if system_name:
        out.insert(0, "System", system_name)
    for col, nd in (("Excess %", 1), ("CFM Coverage %", 0), ("Effective Btu", 0)):
        if col in out.columns:
            out[col] = out[col].round(nd)

    preferred = [
        "System",
        "Category", "Series", "System Model",
        "Indoor Unit Model", "Outdoor Unit Model", "Type",
        "Cooling Capacity (Btu/hr)", "Effective Btu", "Excess %",
        "Airflow (CFM) L/M/H", "High CFM (numeric)",
        "CFM Coverage %", "CFM Approx", "Sensible OK",
        "EER (Btu/Wh)", "Power Supply",
        "Cooling Capacity (kW)", "Heating Capacity (Btu/hr)",
        "Heating Capacity (kW)", "Airflow (m3/h)",
        "Indoor Unit Dimensions (mm)", "Indoor Unit Weight (kg)",
        "Outdoor Unit Dimensions (mm)", "Outdoor Unit Weight (kg)",
        "Outdoor Sound dB(A)",
        "Approx. Area (m2)*", "Notes",
    ]
    ordered = [c for c in preferred if c in out.columns and c != "CFM Approx"]
    rest = [c for c in out.columns if c not in ordered and c != "CFM Approx"]
    res_df = out[ordered + rest].fillna("")
    for col in res_df.columns:
        res_df[col] = res_df[col].apply(
            lambda v: re.sub(r'[\U00010000-\U0010ffff]|[\u2600-\u27bf]|✔|✘', '', str(v)).strip()
            if isinstance(v, str) else v
        )
    return res_df


def prepare_cart_export_df(selected_units):
    rows = []
    for sys_name, entry in selected_units.items():
        r         = entry["row"]
        n_units   = entry["units"]
        load_btu  = float(entry.get("load_btu", 0) or 0)
        load_cfm  = float(entry.get("load_cfm", 0) or 0)
        load_sens = float(entry.get("load_sensible", 0) or 0)
        unit_btu  = float(entry.get("effective_btu", r.get("Effective Btu", r['Cooling Capacity (Btu/hr)'])))
        unit_cfm  = float(r['High CFM (numeric)'])
        total_btu = unit_btu * n_units
        total_cfm = unit_cfm * n_units
        excess = ((total_btu - load_btu) / load_btu * 100.0
                  if load_btu > 0 else float("nan"))
        cfm_cover = (total_cfm / load_cfm * 100.0
                     if load_cfm > 0 else float("nan"))

        d = r.to_dict()
        d["System"]                  = sys_name
        d["Load (Btu/hr)"]           = load_btu
        d["Load (Tons)"]             = load_btu / BTU_PER_TON
        d["Load (CFM)"]              = load_cfm
        d["Sensible (Btu/hr)"]       = load_sens
        d["Units"]                   = n_units
        d["Unit Capacity (Btu/hr)"]  = unit_btu
        d["Unit CFM"]                = unit_cfm
        d["Total Capacity (Btu/hr)"] = total_btu
        d["Total CFM"]               = total_cfm
        d["Excess %"]                = excess
        d["CFM Cover %"]             = cfm_cover
        rows.append(d)

    out = pd.DataFrame(rows)
    head = ["System",
            "Load (Btu/hr)", "Load (Tons)", "Load (CFM)",
            "Sensible (Btu/hr)",
            "Units",
            "Unit Capacity (Btu/hr)", "Unit CFM",
            "Total Capacity (Btu/hr)", "Total CFM",
            "Excess %", "CFM Cover %"]

    # ترتيب منظم بدون تكرار واستبعاد الأعمدة التقنية مثل CFM Approx
    clean_specs = [
        "Category", "Series", "System Model", "Indoor Unit Model", "Outdoor Unit Model",
        "Type", "Power Supply", "EER (Btu/Wh)",
        "Heating Capacity (Btu/hr)", "Heating Capacity (kW)", "Airflow (m3/h)",
        "Indoor Unit Dimensions (mm)", "Indoor Unit Weight (kg)",
        "Outdoor Unit Dimensions (mm)", "Outdoor Unit Weight (kg)", "Outdoor Sound dB(A)",
        "Approx. Area (m2)*", "Notes"
    ]
    cols = head + [c for c in clean_specs if c in out.columns]
    out = out[cols]

    for col, nd in (("Excess %", 1), ("CFM Cover %", 0),
                    ("Load (Btu/hr)", 0), ("Load (Tons)", 2),
                    ("Load (CFM)", 0), ("Sensible (Btu/hr)", 0),
                    ("Unit Capacity (Btu/hr)", 0), ("Unit CFM", 0),
                    ("Total Capacity (Btu/hr)", 0), ("Total CFM", 0)):
        if col in out.columns:
            out[col] = out[col].round(nd)

    out = out.fillna("")
    # تنظيف كل النصوص من أي إيموجي أو رموز تماماً
    for col in out.columns:
        out[col] = out[col].apply(
            lambda v: re.sub(r'[\U00010000-\U0010ffff]|[\u2600-\u27bf]', '',
                             str(v).replace('✔', 'OK').replace('✘', 'Low')).strip()
            if isinstance(v, str) else v
        )
    return out
