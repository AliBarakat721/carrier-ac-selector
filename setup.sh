#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "Error: $PYTHON_BIN was not found. Install Python 3.10+ and try again." >&2
    exit 1
fi

"$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Error: Python 3.10 or newer is required.")
print(f"Using Python {sys.version.split()[0]}")
PY

if [[ ! -d .venv ]]; then
    echo "Creating virtual environment in .venv ..."
    "$PYTHON_BIN" -m venv .venv
else
    echo "Using existing virtual environment in .venv ..."
fi

# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt

echo
echo "Setup complete. Run the application with ./run.sh"
echo "Run the test suite with ./test.sh"
if ! command -v tesseract >/dev/null 2>&1; then
    echo "Note: Tesseract OCR is not on PATH. Image and scanned-PDF OCR will be unavailable."
fi
