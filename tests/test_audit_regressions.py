import math
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import core
import electrical_engine as electrical
import main


def _catalogue_frame():
    return pd.DataFrame({
        "Category": ["Split", "Split"],
        "Series": ["X", "X"],
        "System Model": ["MODEL-A", "MODEL-B"],
        "Indoor Unit Model": ["I-A", "I-B"],
        "Outdoor Unit Model": ["O-A", "O-B"],
        "Type": ["Wall", "Wall"],
        "Cooling Capacity (Btu/hr)": [36000.0, 24000.0],
        "High CFM (numeric)": [float("nan"), 1200.0],
        "EER (Btu/Wh)": [10.0, 11.0],
        "Power Supply": ["220V / 1Ph"] * 2,
    })


def _hap_summary(name, mbh, cfm=None):
    airflow = f"Coil CFM at Design {cfm} CFM\n" if cfm is not None else ""
    return (
        f"Air System Sizing Summary for {name}\n"
        "Central Cooling Coil\n"
        f"Total coil load {mbh} MBH {mbh / 12:.2f} Tons\n"
        + airflow
    )


def test_selection_rejects_out_of_range_percentages_and_multi_count():
    frame = _catalogue_frame()
    with pytest.raises(ValueError, match="safety_pct"):
        core.select_units(frame, 10000, 0, safety_pct=120)
    with pytest.raises(ValueError, match="derate_pct"):
        core.select_units(frame, 10000, 0, derate_pct=100)
    with pytest.raises(ValueError, match="max_units"):
        core.multi_unit_options(frame, 10000, 0, max_units=0)


def test_capacity_only_selection_keeps_models_without_catalogue_airflow():
    found = core.select_units(_catalogue_frame(), 30000, 0)
    assert "MODEL-A" in set(found["System Model"])


def test_duplicate_batch_sections_merge_missing_cfm():
    text = _hap_summary("AHU-1", 100) + _hap_summary("AHU-1", 100, 1800)
    systems = core.parse_all_systems(text)
    assert len(systems) == 1
    assert systems[0].btu == 100000
    assert systems[0].cfm == 1800


def test_electrical_sizing_flags_exhausted_breaker_and_cable_tables():
    result = electrical.calculate_unit_electrical(
        cooling_btu=30000,
        cooling_kw=8.79,
        eer=10,
        power_supply_str="220V / 1Ph",
        current_a_catalog=1000,
        distance_m=0,
        ambient_temp_c=30,
    )
    assert result["mcb_size"] == electrical.STANDARD_MCB_SIZES[-1]
    assert result["mcb_ok"] is False
    assert result["cable_size"] == electrical.STANDARD_CABLE_SIZES[-1]
    assert result["cable_ampacity_ok"] is False
    assert result["design_verified"] is False
    assert result["warnings"]


def test_main_feeder_uses_selected_installation_method():
    rows = [{"kw_elec": 50.0, "phase": 3, "qty": 1}]
    conduit = electrical.compute_schedule_summary(
        rows, install_method="Conduit", grouped_circuits=1
    )
    buried = electrical.compute_schedule_summary(
        rows, install_method="Buried", grouped_circuits=1
    )
    assert conduit["install_method"] == "Conduit"
    assert buried["install_method"] == "Buried"
    assert conduit["main_cable"] != buried["main_cable"]


def test_export_extension_replaces_text_extension_but_keeps_numeric_suffix():
    assert main._replace_export_extension("G01.docs", "docx") == "G01.docx"
    assert main._replace_export_extension("AHU 1.2", "docx") == "AHU 1.2.docx"
