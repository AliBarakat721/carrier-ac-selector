import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import core


def _summary(name: str, btu_mbh: int, cfm: int) -> str:
    return (
        f"Air System Sizing Summary for {name}\n"
        "Central Cooling Coil\n"
        f"Total coil load {btu_mbh} MBH {btu_mbh / 12:.2f} Tons\n"
        f"Coil CFM at Design {cfm} CFM\n"
    )


def test_parse_all_systems_supports_pdf_sizing_summary_headers():
    systems = core.parse_all_systems(
        _summary("G01", 100, 2000) + _summary("G02", 80, 1500)
    )

    assert [(s.name, s.btu, s.cfm) for s in systems] == [
        ("G01", 100_000.0, 2000.0),
        ("G02", 80_000.0, 1500.0),
    ]


def test_parse_all_systems_supports_mixed_header_layouts():
    rtf_style = (
        "Air System Information\n"
        "Air System Name G01\n"
        "Central Cooling Coil\n"
        "Total coil load 100 MBH 8.33 Tons\n"
        "Coil CFM at Design 2000 CFM\n"
    )
    systems = core.parse_all_systems(rtf_style + _summary("G02", 80, 1500))

    assert [s.name for s in systems] == ["G01", "G02"]
    assert [(s.btu, s.cfm) for s in systems] == [
        (100_000.0, 2000.0),
        (80_000.0, 1500.0),
    ]


def test_parse_all_systems_supports_summary_names_without_digits():
    text = (
        "Air System Sizing Summary for AHU-MAIN\n"
        "Central Cooling Coil\n"
        "Total coil load 60 MBH 5.00 Tons\n"
        "Coil CFM at Design 1200 CFM\n"
    )

    systems = core.parse_all_systems(text)

    assert len(systems) == 1
    assert systems[0].name == "AHU-MAIN"
    assert systems[0].btu == 60_000.0
    assert systems[0].cfm == 1200.0
