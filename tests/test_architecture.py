"""Phase 1 architecture guards: layering, facade compatibility, GUI-free logic."""
import ast
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import core
from engineering import units
from equipment import best_pick, catalog_reader, indoor_selector
from hap import parser
from reports import export_frames, file_io

# Packages/modules that must be importable and testable without Qt.
HEADLESS_PACKAGES = ("engineering", "equipment", "hap", "reports", "ui", "cad")
HEADLESS_FILES = ("core.py", "electrical_engine.py")


def _headless_sources():
    for pkg in HEADLESS_PACKAGES:
        yield from sorted((ROOT / pkg).glob("*.py"))
    for name in HEADLESS_FILES:
        yield ROOT / name


@pytest.mark.parametrize("path", list(_headless_sources()), ids=lambda p: str(p.relative_to(ROOT)))
def test_logic_modules_do_not_import_qt(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            assert not name.startswith(("PySide6", "PyQt")), f"{path.name} imports {name}"


def test_logic_packages_import_with_qt_blocked():
    code = (
        "import sys; sys.modules['PySide6'] = None\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "import core, equipment.best_pick, reports.export_frames, reports.file_io\n"
        "import reports.electrical_report, reports.excel_export, ui.layout, hap.parser\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_logic_modules_do_not_import_main_or_core_back():
    """Dependencies point one way: main -> core -> focused modules."""
    for pkg in ("engineering", "equipment", "hap", "reports", "ui"):
        for path in (ROOT / pkg).glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert node.module.split(".")[0] not in {"main", "core"}, path.name
                if isinstance(node, ast.Import):
                    assert all(a.name.split(".")[0] not in {"main", "core"} for a in node.names), path.name


@pytest.mark.parametrize("module,names", [
    (units, ["BTU_PER_TON", "BTU_PER_KW", "to_float", "parse_user_number"]),
    (catalog_reader, ["EXPECTED_COLUMNS", "find_database", "load_database"]),
    (indoor_selector, ["SENSIBLE_RATIO", "MAX_MULTI_UNITS", "select_units",
                       "multi_unit_options", "batch_select", "cover_factor"]),
    (parser, ["HapResult", "SystemLoad", "parse_hap_text", "parse_all_systems"]),
])
def test_core_facade_reexports_the_same_objects(module, names):
    for name in names:
        assert getattr(core, name) is getattr(module, name), name


def test_best_pick_criteria_and_notices():
    df = pd.DataFrame({
        "System Model": ["A", "B", "C"],
        "Excess %": [30.0, 5.0, 12.0],
        "EER (Btu/Wh)": [10.0, 9.0, 12.0],
        "CFM Coverage %": [95.0, 100.0, 140.0],
    })
    assert best_pick.pick_best(df, "size").index == 1
    assert best_pick.pick_best(df, "eer").index == 2
    assert best_pick.pick_best(df, "cfm").index == 1
    assert best_pick.pick_best(df, "overall").ok

    no_eer = df.assign(**{"EER (Btu/Wh)": float("nan")})
    miss = best_pick.pick_best(no_eer, "eer")
    assert not miss.ok and miss.notice_title == "No EER data"
    no_cfm = df.assign(**{"CFM Coverage %": float("nan")})
    assert best_pick.pick_best(no_cfm, "cfm").notice_title == "No CFM data"


def test_file_io_helpers_are_pure():
    assert file_io.normalize_save_path("G01.xlsx", "Word Report (*.docx)") == "G01.docx"
    assert file_io.normalize_save_path("G01", "no extension filter") == "G01"
    assert file_io.default_export_name("G/01", "src", "csv", "_sel") == "G_01_sel.csv"
    assert file_io.default_export_name("", "Report", "xlsx") == "Report.xlsx"
    assert file_io.default_export_name("", "", "docx") == "Carrier_AC_Selection.docx"


def test_prepare_export_df_matches_legacy_behaviour():
    df = pd.DataFrame({
        "System Model": ["X"], "Excess %": [12.345], "CFM Coverage %": [101.6],
        "Effective Btu": [24000.4], "CFM Approx": [True], "Notes": ["✔ ok 😀"],
    })
    out = export_frames.prepare_export_df(df, "AHU-1")
    assert list(out.columns)[0] == "System" and out.loc[0, "System"] == "AHU-1"
    assert out.loc[0, "Excess %"] == 12.3 and out.loc[0, "CFM Coverage %"] == 102
    assert "CFM Approx" not in out.columns
    assert out.loc[0, "Notes"] == "ok"
    assert "System" not in export_frames.prepare_export_df(df).columns
