#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "Python 3 was not found. Install Python 3.10 or newer first." >&2
    exit 1
fi

"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
    echo "Python 3.10 or newer is required." >&2
    exit 1
}

if ! "$PYTHON" -c 'import tkinter' >/dev/null 2>&1; then
    echo "This Python installation does not include Tkinter. Install a Python distribution with Tcl/Tk support." >&2
    exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
    echo "Creating the app's private Python environment..."
    "$PYTHON" -m venv .venv
fi

REQUIREMENTS_HASH="$(shasum -a 256 requirements.txt | awk '{print $1}')"
INSTALLED_HASH=""
if [ -f ".venv/requirements.sha256" ]; then
    INSTALLED_HASH="$(cat .venv/requirements.sha256)"
fi

if [ "$INSTALLED_HASH" != "$REQUIREMENTS_HASH" ]; then
    echo "Installing or updating app dependencies..."
    .venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
    printf '%s\n' "$REQUIREMENTS_HASH" > .venv/requirements.sha256
fi