#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/backend/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Backend virtual environment not found: $PYTHON_BIN" >&2
  echo "Create it first: python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt pytest" >&2
  exit 1
fi

cd "$ROOT_DIR/backend"
exec "$PYTHON_BIN" -m pytest -q
