"""Native-text vs OCR reconciliation (regression tests for the OCR review note)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import core
import ocr_engine


def _block(name, mbh, cfm=None):
    airflow = f"Coil CFM at Des {cfm} CFM\n" if cfm is not None else ""
    return (f"Air System Sizing Summary for {name}\n"
            "Central Cooling Coil\n"
            f"Total coil load {mbh / 12:.1f} Tons {mbh} MBH\n"
            + airflow + "Supply Fan\n")


def _marked(layer, ocr):
    return ("--- page 1 of 1 (text layer) ---\n" + layer + "\n"
            "--- page 1 of 1 (OCR) ---\n" + ocr)


def test_batch_conflict_keeps_native_value_and_warns():
    systems = core.parse_all_systems(
        _marked(_block("AHU-1", 120, 4000), _block("AHU-1", 129, 4000)))
    assert len(systems) == 1
    assert systems[0].btu == 120_000
    assert any("conflict" in w and "129,000" in w for w in systems[0].warnings)


def test_batch_rounding_level_difference_is_not_a_conflict():
    systems = core.parse_all_systems(
        _marked(_block("AHU-1", 120, 4000), _block("AHU-1", 120.4, 4000)))
    assert systems[0].btu == 120_000
    assert not any("conflict" in w for w in systems[0].warnings)


def test_batch_fill_removes_stale_warning_and_flags_source():
    systems = core.parse_all_systems(
        _marked(_block("AHU-1", 120), _block("AHU-1", 120, 4000)))
    s = systems[0]
    assert s.cfm == 4000
    assert not any(w.startswith("Coil CFM not found") for w in s.warnings)
    assert any("read only from" in w for w in s.warnings)


def test_main_entry_point_parse_hap_text_is_reconciled():
    res = core.parse_hap_text(
        _marked(_block("AHU-1", 120, 4000), _block("AHU-1", 96, 4000)))
    assert res.btu == 120_000 and any("conflict" in w for w in res.warnings)


def test_single_system_uses_ocr_to_fill_missing_cfm():
    res = core.parse_hap_text_reconciled(
        _marked(_block("AHU-1", 120), _block("AHU-1", 120, 4000)))
    assert (res.btu, res.cfm) == (120_000, 4000)
    assert not any(w.startswith("Coil CFM not found") for w in res.warnings)
    assert any("OCR" in w for w in res.warnings)   # flagged for verification


def test_single_system_conflict_keeps_native_and_warns():
    res = core.parse_hap_text_reconciled(
        _marked(_block("AHU-1", 120, 4000), _block("AHU-1", 96, 4000)))
    assert res.btu == 120_000
    assert any("conflict" in w for w in res.warnings)
    assert not res.ok


def test_unmarked_text_is_parsed_exactly_as_before():
    text = _block("AHU-1", 120, 4000)
    assert core.parse_hap_text(text) == core._parse_hap_chunk(text)


def test_both_missing_still_reports_both_warnings():
    res = core.parse_hap_text_reconciled(_marked("nothing", "nothing"))
    assert res.btu == 0 and res.cfm == 0
    assert any("Cooling" in w for w in res.warnings)


# ── extract_text completeness rule (real PDF, OCR mocked) ────────────────────
fitz = pytest.importorskip("fitz")


def _pdf(tmp_path, pages):
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((40, 60), text, fontsize=9)
    path = tmp_path / "hap.pdf"
    doc.save(path)
    doc.close()
    return str(path)


def test_complete_native_layer_skips_ocr(tmp_path, monkeypatch):
    path = _pdf(tmp_path, [_block("AHU-1", 120, 4000)])
    monkeypatch.setattr(ocr_engine, "ocr_image",
                        lambda *_: pytest.fail("OCR must not run"))
    assert "(OCR)" not in ocr_engine.extract_text(path)


def test_multi_system_with_one_incomplete_system_triggers_ocr(tmp_path, monkeypatch):
    # AHU-1 complete, AHU-2 missing CFM: the old single/batch split could
    # return early on AHU-1 alone. Now ALL systems must be complete.
    path = _pdf(tmp_path, [_block("AHU-1", 120, 4000), _block("AHU-2", 60)])
    calls = []
    monkeypatch.setattr(ocr_engine, "ocr_image",
                        lambda *_: calls.append(1) or _block("AHU-2", 60, 2000))
    out = ocr_engine.extract_text(path)
    assert calls and "(OCR)" in out
    systems = {s.name: s for s in core.parse_all_systems(out)}
    assert systems["AHU-2"].cfm == 2000


def test_ocr_unavailable_keeps_partial_native_text(tmp_path, monkeypatch):
    path = _pdf(tmp_path, [_block("AHU-1", 120)])

    def _boom(*_):
        raise ocr_engine.OCRUnavailable("no tesseract")
    monkeypatch.setattr(ocr_engine, "ocr_image", _boom)
    out = ocr_engine.extract_text(path)
    assert "(text layer)" in out and "(OCR)" not in out
