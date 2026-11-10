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

On Windows, install Tesseract using the UB Mannheim installer and add its installation directory (commonly `C:\\Program Files\\Tesseract-OCR`) to `PATH`. Restart PowerShell after changing `PATH`.

## Install

### Windows — easiest method for non-technical users

1. Download the repository using **Code → Download ZIP**, then extract the ZIP file.
2. Install Python 3.10 or newer from https://www.python.org/downloads/windows/ and enable **Add Python to PATH** during installation.
3. Open the extracted project folder and double-click **Start_Carrier.bat**.
4. On the first launch, the launcher creates the Windows virtual environment, installs the required Python packages, and creates a **Carrier AC Selector** shortcut on the Desktop.
5. After setup finishes, the application starts. For future use, double-click the **Carrier AC Selector** icon on the Desktop. You can also double-click `Start_Carrier.bat` inside the project folder.\n6. To update later, open the application and choose **Help → Check for Updates**. If a newer release is available, confirm the download; the app verifies its SHA-256 checksum, closes, applies the update, and restarts.

Keep the extracted project folder in its original location after setup. The Desktop shortcut points to that folder, so moving or deleting it will break the shortcut. An internet connection is required for the first-time package installation.

### Windows (PowerShell)

Open PowerShell in the project directory:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\\setup.ps1
```

The script creates a Windows-specific `.venv\\` and installs all runtime and test dependencies. Then run:

```powershell
.\\run.ps1
```

To execute tests on Windows:

```powershell
.\\test.ps1
```

If PowerShell blocks scripts, the `Set-ExecutionPolicy` command above changes the policy only for the current PowerShell process.

### Linux / macOS

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
python -m py_compile core.py main.py electrical_engine.py ocr_engine.py roi_tab.py updater.py version.py
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
setup.ps1                     Create the Windows virtual environment and install dependencies\nversion.py                    Application version used by the updater\nupdater.py                    GitHub Releases check, download, and verification\napply_update.ps1              Safe Windows file replacement and restart\n.github/workflows/release.yml Build and publish tagged Windows update packages
run.ps1                       Start the GUI using the Windows virtual environment
test.ps1                      Run tests and syntax checks on Windows
requirements.txt              Runtime dependencies
requirements-dev.txt          Test/development dependencies
```

## Application updates and releases\n\n- The installed application version is defined in `version.py` (`APP_VERSION`).\n- The **Help → Check for Updates** command checks the latest published GitHub Release. It does not install arbitrary commits or unreviewed changes from the `main` branch.\n- Windows update packages and their `SHA256SUMS.txt` checksum are generated by `.github/workflows/release.yml` whenever a `v*` tag is pushed.\n- To publish an update: bump `APP_VERSION` (for example, `2.0.0` → `2.0.1`), commit the change, create and push the matching tag (`v2.0.1`). GitHub Actions then creates a Release with the Windows package.\n- The updater backs up files it replaces during the update operation, preserves `.venv` and user-generated exports, verifies the package checksum, and restarts the app. The in-app installer currently targets Windows; Linux/macOS users can download source updates from the Releases page and follow the installation instructions.\n- Updates require internet access and a published Release. If no Release exists yet, the app reports that no published release is available.\n\n## Adding a new AC model

1. Open `data/Carrier_AC_Database.xlsx`.
2. Add a row with the required catalogue columns. Column order does not matter; columns are matched by name.
3. If the same `System Model` appears more than once, the loader merges the entries and joins their series.
4. Blank or em-dash heating values are treated as not applicable.
5. Values prefixed with `≈` are accepted and marked as approximate where applicable.

## Notes

- The electrical schedule is an engineering estimate and must be reviewed against the applicable local electrical code and project design data.
- Tesseract is an external system package; installing the Python package `pytesseract` alone is not enough for image OCR.
- A Python `.venv` is operating-system-specific. Create it separately on Windows with `setup.ps1`; do not copy the Linux `.venv` into Windows or commit either environment.
- Windows and Linux are both covered by the GitHub Actions test matrix. The automated checks validate dependency imports, Python compilation, and unit tests; OCR with the external Tesseract executable still requires a local Windows installation and a real scanned-file test.
- Generated Excel, Word, virtual-environment, cache, and temporary files are excluded by `.gitignore` where appropriate.
