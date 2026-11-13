from integration.hap_zone_parser import parse_hap_zone_summaries


SAMPLE = """
Zone Sizing Summary for AHU-East
Air System Information
Air System Name
AHU-East
Number of zones
1
Zone Sizing Data
Zone Name
(MBH)
Airflow
Zone 1
21.3
995
Space Loads and Airflows
Cooling
Time
Air
Heating
Floor
Zone Name /
Sensible
of
Flow
Load
Area
Space
Space Name
Mult.
(MBH)
Load
(CFM)
(MBH)
(ft²)
CFM/ft²
Zone 1
Office-East
1
21.3
Sep 1400
995
6.3
693.1
1.44
Ventilation Sizing Summary for AHU-East
"""


def test_zone_parser_extracts_space_sensible_load_and_airflow_not_total_load():
    rows = parse_hap_zone_summaries(SAMPLE)
    assert len(rows) == 1
    assert rows[0].system_name == "AHU-East"
    assert rows[0].space_name == "Office-East"
    assert rows[0].zone_count == 1
    assert rows[0].space_count == 1
    assert rows[0].multiplier == 1
    assert rows[0].sensible_load_btu == 21_300
    assert rows[0].airflow_cfm == 995


def test_zone_parser_keeps_each_space_row_distinct():
    text = SAMPLE.replace(
        "Ventilation Sizing Summary for AHU-East",
        "Meeting-East\n1\n14.0\nSep 1500\n620\n4.0\n300.0\n2.07\nVentilation Sizing Summary for AHU-East",
    )
    rows = parse_hap_zone_summaries(text)
    assert [row.space_name for row in rows] == ["Office-East", "Meeting-East"]
    assert {row.space_count for row in rows} == {2}
