# Carrier AC Selector — HAP Report Analyzer

Desktop tool built with PySide6 that reads a HAP *Air System Sizing Summary* screenshot, PDF, or RTF report, extracts the cooling load and coil airflow, and filters the Carrier AC catalogue for matching indoor/outdoor combinations.

## Features

- **Input:** PNG / JPG / TIFF / PDF / RTF.
- **PDF handling:** native PDF text is used first; scanned PDFs and images use Tesseract OCR.
- **HAP parsing:** extracts total coil load in MBH and coil CFM, with explicit warnings when a value cannot be read.
- **Multi-system reports:** detects both `Air System Information ... Air System Name ...` and `Air System Sizing Summary for ...` layouts, including mixed reports.
- **Selection controls:** safety factor, derating, CFM coverage, sensible-load check, Category / Type / Series / Power Supply filters.
- **Multi-unit selection:** suggests the minimum number of identical units, up to four, when one unit cannot cover the load.
- **Best-unit picker:** balanced score, least oversized, highest EER, and best airflow fit.
- **Selection cart:** collect units for multiple systems and export them together.
- **Electrical schedule:** estimated FLA, breaker, cable sizing, voltage drop, feeder, panel, generator, and UPS values.
- **Inverter ROI:** energy, cost, payback, lifecycle, and CO₂ comparison.
- **Export:** Excel, CSV, and formatted Word reports.
- **UI:** dark PySide6 interface, background OCR worker, image/PDF preview, and OCR debug tab.

## Requirements

- Python 3.10 or newer.
- Linux, macOS, or Windows with a working PySide6 installation.
- **Tesseract OCR** is required only for image input and scanned PDFs. RTF files and PDFs with a text layer do not require it.

### Install Tesseract

Ubuntu/Debian:

```bash
sudo apt update
sudo apt install -y tesseract-ocr
```

Fedora/RHEL:

```bash
sudo dnf install -y tesseract tesseract-langpack-eng
```

macOS:

```bash
brew install tesseract
```

On Windows, install Tesseract from the UB Mannheim distribution and ensure the Tesseract installation directory is on `PATH`.

## Install

From the project directory:

```bash
./setup.sh
```

The script creates `.venv/`, installs runtime dependencies from `requirements.txt`, and installs test/development dependencies from `requirements-dev.txt`.

To install manually:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-dev.txt
```

## Run

```bash
./run.sh
```

The script activates `.venv` and starts the PySide6 application. To run manually:

```bash
. .venv/bin/activate
python main.py
```

## Test

```bash
./test.sh
```

The test script runs the available unit tests with `pytest`. It also performs a Python bytecode compilation check for the application modules.

To run tests manually:

```bash
. .venv/bin/activate
pytest -q
python -m py_compile core.py main.py electrical_engine.py ocr_engine.py roi_tab.py
```

The current regression suite covers database-independent HAP parsing, including multi-system `Sizing Summary` headers, the legacy information layout, mixed layouts, and system names without digits.

## Project layout

```text
core.py                       Pure logic: database loading, HAP parsing, selection
ocr_engine.py                 OCR front-end: RTF/PDF text layer/Tesseract
main.py                       PySide6 GUI and report export
electrical_engine.py          Electrical load and cable-sizing calculations
roi_tab.py                    Inverter ROI calculations and export helpers
data/Carrier_AC_Database.xlsx Carrier catalogue

tests/test_core.py            Parser regression tests
setup.sh                      Create the virtual environment and install dependencies
run.sh                        Activate the environment and start the GUI
test.sh                        Run tests and syntax checks
requirements.txt              Runtime dependencies
requirements-dev.txt          Test/development dependencies
```

## Adding a new AC model

1. Open `data/Carrier_AC_Database.xlsx`.
2. Add a row with the required catalogue columns. Column order does not matter; columns are matched by name.
3. If the same `System Model` appears more than once, the loader merges the entries and joins their series.
4. Blank or em-dash heating values are treated as not applicable.
5. Values prefixed with `≈` are accepted and marked as approximate where applicable.

## Notes

- The electrical schedule is an engineering estimate and must be reviewed against the applicable local electrical code and project design data.
- Tesseract is an external system package; installing the Python package `pytesseract` alone is not enough for image OCR.
- Generated Excel, Word, virtual-environment, cache, and temporary files are excluded by `.gitignore` where appropriate.
