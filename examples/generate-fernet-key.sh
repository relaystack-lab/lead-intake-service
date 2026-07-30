#!/usr/bin/env sh

set -eu

PYTHON_PATH="${PYTHON_PATH:-./.venv/bin/python}"
exec "$PYTHON_PATH" -m lead_intake.cli generate-fernet-key
