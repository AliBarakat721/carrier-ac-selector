from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"


def _release_root_files() -> set[str]:
    text = WORKFLOW.read_text(encoding="utf-8")
    build_step = text.split("- name: Build Windows update package", 1)[1]
    match = re.search(r"\$files\s*=\s*@\((.*?)\n\s*\)", build_step, re.DOTALL)
    assert match, "Could not locate the Windows release root-file list."
    return set(re.findall(r'"([^"\r\n]+)"', match.group(1)))


def _release_package_dirs() -> set[str]:
    text = WORKFLOW.read_text(encoding="utf-8")
    build_step = text.split("- name: Build Windows update package", 1)[1]
    match = re.search(r'foreach\s*\(\$directory\s+in\s+@\(([^)]*)\)\)', build_step)
    assert match, "Could not locate the Windows package-directory list."
    return set(re.findall(r'"([^"\r\n]+)"', match.group(1)))


def test_release_staging_includes_all_root_python_modules_and_runtime_packages():
    root_files = _release_root_files()
    staged_python = {name for name in root_files if name.endswith(".py")}
    expected_root_modules = {path.name for path in REPO_ROOT.glob("*.py")}
    missing_root = expected_root_modules - staged_python
    assert not missing_root, f"Release omits root Python modules: {sorted(missing_root)}"

    package_dirs = _release_package_dirs()
    expected_dirs = {"cad", "ui"}
    assert expected_dirs <= package_dirs, f"Release omits runtime packages: {sorted(expected_dirs - package_dirs)}"
    for directory in package_dirs:
        source = REPO_ROOT / directory
        if source.is_dir():
            assert any(source.rglob("*.py")), f"Configured release package is empty: {directory}"


def test_release_stages_icons_and_smoke_tests_the_real_payload():
    workflow_text = WORKFLOW.read_text(encoding="utf-8")
    assert 'Copy-Item -LiteralPath "icons" -Destination $stage -Recurse -Force' in workflow_text
    root_files = _release_root_files()
    package_dirs = _release_package_dirs()
    stage = Path(tempfile.mkdtemp(prefix="carrier-release-payload-"))
    try:
        for name in root_files:
            source = REPO_ROOT / name
            if source.is_file():
                shutil.copy2(source, stage / source.name)
        for directory in package_dirs:
            source = REPO_ROOT / directory
            if source.is_dir():
                shutil.copytree(
                    source,
                    stage / directory,
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                )
        shutil.copytree(REPO_ROOT / "data", stage / "data", dirs_exist_ok=True)
        shutil.copytree(REPO_ROOT / "icons", stage / "icons", dirs_exist_ok=True)
        for icon in ("move_rotate.svg", "scale.svg"):
            assert (stage / "icons" / icon).is_file(), f"Missing staged CAD icon: {icon}"

        smoke = (
            "from pathlib import Path; "
            "from PySide6.QtWidgets import QApplication; "
            "import main; "
            "from ui.cad_workspace import _icon; "
            "app = QApplication([]); "
            "assert not _icon(Path.cwd(), 'move_rotate').isNull(); "
            "assert not _icon(Path.cwd(), 'scale').isNull()"
        )
        environment = os.environ.copy()
        environment["QT_QPA_PLATFORM"] = "offscreen"
        result = subprocess.run(
            [sys.executable, "-c", smoke],
            cwd=stage,
            env=environment,
            capture_output=True,
            text=True,
            timeout=45,
        )
        assert result.returncode == 0, (
            "Staged release import/icon smoke test failed.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    finally:
        shutil.rmtree(stage, ignore_errors=True)
