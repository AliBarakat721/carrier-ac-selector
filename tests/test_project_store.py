from __future__ import annotations

import json
from pathlib import Path

import pytest
import ezdxf

from project_store import ProjectStore, ProjectStoreError


def test_project_creation_copies_inputs_and_restores_json_state(tmp_path):
    source_cad = tmp_path / "outside" / "plan.dxf"
    source_hap = tmp_path / "outside" / "hap.pdf"
    source_cad.parent.mkdir()
    source_cad.write_text("cad source", encoding="utf-8")
    source_hap.write_bytes(b"hap report")
    project = ProjectStore.create(tmp_path / "project", "Office")

    owned_cad = project.add_source("cad", source_cad)
    owned_hap = project.add_source("hap", source_hap)
    project.update_state({
        "room_review": {"units_override": "meters"},
        "verified": {"ROOM-1"},
        "cart": {"AHU-1": {"row": {"capacity": 36000}, "qty": 1}},
        "non_finite": float("nan"),
    })

    assert owned_cad.read_text(encoding="utf-8") == "cad source"
    assert owned_hap.read_bytes() == b"hap report"
    assert source_cad.read_text(encoding="utf-8") == "cad source"
    manifest = json.loads((project.root / "project.json").read_text(encoding="utf-8"))
    assert manifest["state"]["non_finite"] is None
    assert not Path(manifest["files"]["cad_original"]).is_absolute()

    reopened = ProjectStore.open(project.root)
    assert reopened.name == "Office"
    assert reopened.asset("cad_original").read_text(encoding="utf-8") == "cad source"
    assert reopened.state["room_review"]["units_override"] == "meters"


def test_project_refuses_paths_that_escape_project_root(tmp_path):
    project = ProjectStore.create(tmp_path / "project")
    with pytest.raises(ProjectStoreError, match="escapes"):
        project.resolve("../outside.dxf")


def test_open_recovers_from_corrupt_manifest_using_last_good_backup(tmp_path):
    project = ProjectStore.create(tmp_path / "project", "Recovered")
    project.update_state({"generation": 2})
    (project.root / "project.json").write_text("{broken", encoding="utf-8")

    recovered = ProjectStore.open(project.root)
    assert recovered.name == "Recovered"
    assert recovered.state == {}


def test_create_refuses_to_take_over_a_nonempty_directory(tmp_path):
    root = tmp_path / "existing"
    root.mkdir()
    (root / "user-data.txt").write_text("do not overwrite", encoding="utf-8")
    with pytest.raises(ProjectStoreError, match="not empty"):
        ProjectStore.create(root)
    assert (root / "user-data.txt").read_text(encoding="utf-8") == "do not overwrite"


def test_reimported_sources_are_versioned_instead_of_overwriting_project_originals(tmp_path):
    project = ProjectStore.create(tmp_path / "project")
    first = tmp_path / "first.dxf"
    second = tmp_path / "second.dxf"
    first.write_bytes(b"first original")
    second.write_bytes(b"second original")

    owned_first = project.add_source("cad", first)
    owned_second = project.add_source("cad", second)

    assert owned_first != owned_second
    assert owned_first.read_bytes() == b"first original"
    assert project.asset("cad_original") == owned_second


def test_working_dxf_replacement_keeps_a_valid_previous_revision(tmp_path):
    project = ProjectStore.create(tmp_path / "project")
    first = ezdxf.new("R2018")
    first.modelspace().add_line((0, 0), (1, 0))
    project.set_working_dxf(first)

    staging = project.root / "cad" / ".next-working.dxf"
    second = ezdxf.new("R2018")
    second.modelspace().add_line((0, 0), (2, 0))
    second.saveas(staging)
    project.commit_working_dxf(staging)

    backups = list((project.root / "backups").glob("working-*.dxf"))
    assert len(backups) == 1
    assert len(ezdxf.readfile(backups[0]).modelspace().query("LINE")) == 1
    active = ezdxf.readfile(project.asset("cad_working"))
    assert tuple(active.modelspace().query("LINE")[0].dxf.end)[:2] == (2, 0)
