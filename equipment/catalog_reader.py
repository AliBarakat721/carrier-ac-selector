"""Equipment catalog loading (reads the Carrier database; no selection logic).

Moved verbatim from ``core.py`` (Phase 1). ``core`` re-exports every name.
"""
from __future__ import annotations

import os

import pandas as pd

from engineering.units import to_float

EXPECTED_COLUMNS = [
    'Category', 'Series', 'System Model', 'Indoor Unit Model',
    'Outdoor Unit Model', 'Type', 'Cooling Capacity (Btu/hr)',
    'Cooling Capacity (kW)', 'Heating Capacity (Btu/hr)',
    'Heating Capacity (kW)', 'Airflow (CFM) L/M/H', 'Airflow (m3/h)',
    'EER (Btu/Wh)', 'Power Supply', 'Indoor Unit Dimensions (mm)',
    'Indoor Unit Weight (kg)', 'Approx. Area (m2)*', 'Notes',
    'High CFM (numeric)',
]


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
