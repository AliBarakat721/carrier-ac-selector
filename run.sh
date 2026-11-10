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
exec python main.py "$@"
