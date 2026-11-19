#!/usr/bin/env bash
# SalesFlow one-click start (macOS / Linux / WSL)
# Creates a local virtual environment, installs dependencies, starts the app,
# and opens http://localhost:8000 in your browser.
set -e
cd "$(dirname "$0")"

echo "=============================================="
echo "  SalesFlow - starting"
echo "=============================================="

# 1. Find a Python 3 interpreter
PY=""
for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(0 if sys.version_info[0]==3 else 1)' 2>/dev/null; then
    PY="$cand"; break
  fi
done
if [ -z "$PY" ]; then
  echo ""
  echo "ERROR: Python 3 was not found."
  echo "  Install Python 3.10 or newer from https://www.python.org/downloads/"
  echo "  (Windows users: double-click start-windows.bat instead of this file)"
  echo ""
  exit 1
fi
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info[1]>=10 else 1)' 2>/dev/null; then
  echo "ERROR: Python 3.10+ is required, found: $("$PY" --version 2>&1)."
  exit 1
fi
echo "[1/3] Python found: $("$PY" --version 2>&1)"

# 2. Virtual environment + dependencies
if [ ! -x .venv/bin/python ]; then
  echo "[2/3] Creating virtual environment (.venv)..."
  "$PY" -m venv .venv || {
    echo "ERROR: could not create a venv."
    echo "  Debian/Ubuntu: sudo apt install python3-venv"
    echo "  Fedora:        sudo dnf install python3"
    exit 1
  }
fi
echo "[2/3] Installing dependencies (first run only, ~30 seconds)..."
./.venv/bin/pip install -q --disable-pip-version-check -r backend/requirements.txt || {
  echo "ERROR: pip install failed."
  echo "  - Check your internet connection / corporate proxy."
  echo "  - Retry manually:  ./.venv/bin/pip install -r backend/requirements.txt"
  exit 1
}

# 3. Start
echo "[3/3] Starting server on http://localhost:8000"
echo "      Login: admin@salesflow.app / Demo@123  (or use the demo buttons)"
echo "      Press Ctrl+C to stop."
sleep 2 && { xdg-open http://localhost:8000 >/dev/null 2>&1 || open http://localhost:8000 >/dev/null 2>&1 || true; } &
exec ./.venv/bin/python -m uvicorn app.main:app --port 8000 --app-dir backend
