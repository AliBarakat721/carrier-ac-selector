import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import core
import ocr_engine


def test_rtf_to_text_removes_controls_and_preserves_hap_values():
    raw = (
        r"{\rtf1\ansi\deff0 "
        r"Central Cooling Coil\par "
        r"Total coil load 100 MBH 8.33 Tons\par "
        r"Coil CFM at Design 2000 CFM\par}"
    )

    text = ocr_engine.rtf_to_text(raw)
    result = core.parse_hap_text(text)

    assert "Central Cooling Coil" in text
    assert "\\rtf1" not in text
    assert result.btu == 100_000.0
    assert result.cfm == 2000.0
    assert result.warnings == []


def test_extract_text_reads_native_pdf_text_layer(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    pdf_path = tmp_path / "hap.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text(
        (72, 72),
        "Central Cooling Coil\n"
        "Total coil load 80 MBH 6.67 Tons\n"
        "Coil CFM at Design 1500 CFM",
    )
    document.save(pdf_path)
    document.close()

    text = ocr_engine.extract_text(str(pdf_path))
    result = core.parse_hap_text(text)

    assert "Total coil load" in text
    assert result.btu == 80_000.0
    assert result.cfm == 1500.0
    assert result.warnings == []


def test_image_ocr_reports_missing_tesseract_cleanly_when_unavailable(tmp_path):
    image = pytest.importorskip("PIL.Image")
    image_path = tmp_path / "blank.png"
    image.new("RGB", (100, 100), "white").save(image_path)

    try:
        text = ocr_engine.extract_text(str(image_path))
    except ocr_engine.OCRUnavailable as exc:
        assert "Tesseract" in str(exc)
    else:
        assert isinstance(text, str)
