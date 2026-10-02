#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
bash ./setup-macos.sh
exec .venv/bin/python main.py