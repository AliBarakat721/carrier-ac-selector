#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

if [[ ! -x .venv/bin/python ]]; then
    echo "Virtual environment not found. Run ./setup.sh first." >&2
    exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

pytest -q
python -m py_compile core.py main.py electrical_engine.py ocr_engine.py roi_tab.py updater.py version.py cad/*.py engineering/*.py equipment/*.py hap/*.py reports/*.py ui/*.py

echo "All tests and syntax checks passed."
