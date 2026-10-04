# Carrier AC Selector — HAP Report Analyzer

Desktop tool (PySide6) that reads a HAP *Air System Sizing Summary*
screenshot / PDF / RTF via OCR, extracts cooling load & coil CFM, then
filters the Carrier AC catalogue for matching indoor/outdoor combinations.

## Features

- **Input**: PNG / JPG / TIFF / PDF / RTF. PDF text layer is used first
  (exact); scanned PDFs and images fall back to Tesseract with multi-PSM
  voting.
- **Robust parser**: MBH is the reference load — unreadable labels produce
  an explicit warning instead of a silently wrong value.
- **Multi-system reports**: a full HAP *Systems Design Report* is split per
  air system automatically; one best-match row per system.
- **Selection knobs**: safety factor, derating, CFM coverage %, sensible
  check (informational), Category / Type filters.
- **Multi-unit**: minimum number of identical units (≤ 4) when no single
  unit covers the load.
- **Best-unit picker**: balanced score, least oversized, most efficient,
  best airflow fit — plus manual multi-row selection.
- **Export**: Excel / CSV / formatted Word report.
- **UI**: dark theme, background OCR thread, image preview, OCR debug tab.

## Requirements

- Python ≥ 3.10
- System: **Tesseract OCR** (only for image / scanned-PDF input)
  - Fedora/RHEL : `sudo dnf install -y tesseract tesseract-langpack-eng`
  - Debian/Ubuntu: `sudo apt install -y tesseract-ocr`
  - macOS       : `brew install tesseract`

## Install

    ./setup.sh

Creates a virtualenv at `.venv/` and installs everything from
`requirements.txt` + `requirements-dev.txt`.

## Run

    ./run.sh

(or `source .venv/bin/activate && python main.py`)

## Test

    ./test.sh

`tests/test_ocr.py` auto-skips when Tesseract is not installed.

With coverage:

    pytest -v --cov=core --cov=ocr_engine --cov-report=term-missing

## Project layout

    core.py                       pure logic: DB loading, HAP parsing, selection
    ocr_engine.py                 OCR front-end (PDF text layer → Tesseract)
    main.py                       PySide6 GUI
    data/
      Carrier_AC_Database.xlsx    the catalogue
      samples/                    example HAP reports (RTF)
    tests/
      test_core.py                unit tests (no OCR dependency)
      test_ocr.py                 regression tests (skipped if no Tesseract)
      hap_sample.png, G11.png     reference screenshots
    setup.sh / run.sh / test.sh   convenience scripts
    requirements.txt              runtime dependencies
    requirements-dev.txt          test / coverage dependencies
    pytest.ini                    pytest configuration

## Adding a new AC model

1. Open `data/Carrier_AC_Database.xlsx`.
2. Add a row with all columns (column order doesn't matter — columns are
   matched by name).
3. If the same `System Model` appears twice (listed under two series), the
   loader merges them automatically.
4. `—` / blank values in heating columns are fine (interpreted as N/A).
5. The `≈` prefix on any value is accepted and flagged as approximate.
