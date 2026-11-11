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
- **CAD + HAP workflow:** upload DWG or DXF floor plans. DWG files are converted temporarily to DXF with ODA File Converter; the original drawing is never overwritten. Detect any number of rooms,
  map rooms to parsed HAP systems, choose Carrier units, and export placed
  indoor-unit tags plus an equipment schedule. Outdoor-unit placement remains
  a separate user-approved step because room geometry alone is not a safe
  outdoor-unit location.
- **UI:** dark PySide6 interface, background OCR worker, image/PDF preview, and OCR debug tab.

For the complete end-user workflow, see the [detailed User Guide](docs/USER_GUIDE.md).

## Requirements

- Python 3.10 or newer.
- Linux, macOS, or Windows with a working PySide6 installation.
- DWG import requires the separate ODA File Converter application from https://www.opendesign.com/guestfiles/oda_file_converter. DXF import does not require ODA. On Linux, xvfb may be required when running the converter without a display.
- **Tesseract OCR** is required only for image input and scanned PDFs. RTF files and PDFs with a text layer do not require it.

### DWG import (CAD Workflow)

The CAD Workflow accepts both DXF and DWG floor plans. DXF files are read directly. DWG files require the separate **ODA File Converter** application; the Python package ezdxf does not read DWG natively.

1. Download and install ODA File Converter from the official [Open Design Alliance download page](https://www.opendesign.com/guestfiles/oda_file_converter).
2. Restart Carrier AC Selector after installation.
3. Open **CAD Workflow → Upload DWG / DXF** and select your drawing.
4. The application converts the DWG to a temporary R2018 DXF, validates it with the existing DXF reader, and then detects rooms. The temporary file is deleted after loading; your original DWG is not changed.

If the converter is installed in a non-standard location, set the environment variable `ODA_FILE_CONVERTER` to its full executable path before starting the application. On Windows, the default ODA installation path is detected by ezdxf. On Linux, ODA's command-line conversion may need a virtual display; install and configure `xvfb` if the converter does not start in your desktop/session environment.

DWG import is optional: users who only open DXF files do not need ODA File Converter.

## Install Tesseract

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

1. Open the [latest GitHub Release](https://github.com/AliBarakat721/carrier-ac-selector/releases/latest), expand **Assets**, and download **CarrierACSelector-Windows.zip**. Extract it to a permanent folder.
2. Install Python 3.10 or newer from https://www.python.org/downloads/windows/ and enable **Add Python to PATH** during installation.
3. Open the extracted project folder and double-click **Start_Carrier.bat**.
4. On the first launch, the launcher creates the Windows virtual environment, verifies the required Python packages, and creates a **Carrier AC Selector** shortcut on the Desktop when Windows allows it.
5. After setup finishes, the application starts. For future use, double-click the **Carrier AC Selector** icon on the Desktop. You can also double-click `Start_Carrier.bat` inside the project folder.
6. To update later, open the application and choose **Help → Check for Updates**. If a newer release is available, confirm the download; the app verifies its SHA-256 checksum, closes, applies the update, and restarts.

Keep the extracted project folder in its original location after setup. The Desktop shortcut points to that folder, so moving or deleting it will break the shortcut. An internet connection is required for the first-time package installation. If Windows blocks shortcut creation, the application can still be started with `Start_Carrier.bat` or `run.ps1`.

### Windows installation walkthrough

Follow these steps to install the latest published Windows release:

#### 1. Download the Windows release

Open the [latest GitHub Release](https://github.com/AliBarakat721/carrier-ac-selector/releases/latest). Under **Assets**, download `CarrierACSelector-Windows.zip` (not the source-code ZIP files). Save it to your computer.

#### 2. Extract the ZIP file

Open the Downloads folder, right-click `CarrierACSelector-Windows.zip`, choose **Extract All...**, and select a permanent folder. Do not run the program from inside the ZIP preview.

![Windows context menu showing Extract All](docs/images/installation/02-extract-zip.png)

#### 3. Install Python

If Python is not already installed, download Python 3.10 or newer from the [official Windows download page](https://www.python.org/downloads/windows/). During installation, enable **Add Python to PATH**.

#### 4. Start the automatic installer

Open the extracted release folder and double-click `Start_Carrier.bat`. The first run creates `.venv`, installs the Python packages, checks the installation, and starts the application. Keep the computer connected to the internet during this step.

![Extracted project folder with Start_Carrier.bat selected](docs/images/installation/03-start-installer.png)

#### 5. Use the Desktop shortcut

When setup succeeds, Windows creates a **Carrier AC Selector** shortcut on the Desktop. Keep the extracted project folder in place because the shortcut uses that folder as the application working directory.

If Windows cannot create the shortcut, this does not prevent the application from working. Run `Start_Carrier.bat` again or use `run.ps1` from PowerShell.

#### 6. Optional OCR setup

Tesseract OCR is only needed for image files and scanned PDFs. RTF files and PDFs that already contain selectable text do not require Tesseract. See [Install Tesseract](#install-tesseract) above for the Windows setup note.

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
setup.ps1                     Create the Windows virtual environment and install dependencies
version.py                    Application version used by the updater
updater.py                    GitHub Releases check, download, and verification
apply_update.ps1              Safe Windows file replacement and restart
.github/workflows/release.yml Build and publish tagged Windows update packages
run.ps1                       Start the GUI using the Windows virtual environment
test.ps1                      Run tests and syntax checks on Windows
requirements.txt              Runtime dependencies
requirements-dev.txt          Test/development dependencies
```

## Application updates and releases

- The installed application version is defined in `version.py` (`APP_VERSION`).
- The **Help → Check for Updates** command checks the latest published GitHub Release. It does not install arbitrary commits or unreviewed changes from the `main` branch.
- Windows update packages and their `SHA256SUMS.txt` checksum are generated by `.github/workflows/release.yml` when a valid SemVer tag is pushed and matches `APP_VERSION` exactly.
- To publish an update: bump `APP_VERSION` (for example, `2.0.0` → `2.0.1`), commit the change, create and push the matching tag (`v2.0.1`). GitHub Actions then creates a Release with the Windows package.
- The updater verifies the package SHA-256 checksum and validates ZIP paths, file types, duplicate names, links, and expanded size before extraction. It preserves existing `data/Carrier_AC_Database.xlsx` and `data/Carrier_AC_Database.csv` files instead of overwriting user data. The previous `.venv` and replaced application files are backed up under `%LOCALAPPDATA%\CarrierACSelector\update-backups`; the replacement virtual environment is created directly at its final `.venv` path because Python virtual environments must not be moved after creation. If dependency installation, import checks, or the post-update application startup check fails, the updater attempts to restore the previous files and environment. Update details are written to `%LOCALAPPDATA%\CarrierACSelector\update.log`.
- Updates require internet access and a published Release. If no Release exists yet, the app reports that no published release is available.
- SHA-256 detects package corruption or a mismatch with the published checksum; because both files are hosted in the same Release, it is not a publisher-authenticity signature. Protect release permissions and version tags, and use GitHub's protected-tag/release controls for production distribution.

## Adding a new AC model

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
