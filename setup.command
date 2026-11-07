#!/bin/bash
cd "$(dirname "$0")"
set -u

find_python() {
  local candidate version
  for candidate in python3.14 python3.13 python3.12 python3.11 python3.10 \
                   /opt/homebrew/bin/python3 /usr/local/bin/python3 python3 python; do
    command -v "$candidate" >/dev/null 2>&1 || [ -x "$candidate" ] || continue
    version=$("$candidate" -c 'import sys; print("%d.%d"%sys.version_info[:2])' 2>/dev/null) || continue
    "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' || continue
    echo "$candidate"
    return 0
  done
  return 1
}

PY=$(find_python) || {
  echo "[X] Python 3.10 or newer was not found."
  echo "    Download it from https://www.python.org/downloads/"
  echo "    Or: brew install python@3.12"
  echo "    Then double-click setup.command again."
  [ "${1:-}" = "--no-pause" ] || read -r -p "Press Enter to close..."
  exit 1
}

echo "[1/4] Python $("$PY" -c 'import sys; print(sys.version.split()[0])') found."
if [ ! -x .venv/bin/python ]; then
  echo "[2/4] Creating virtual environment in .venv ..."
  "$PY" -m venv .venv || { echo "[X] Could not create the virtual environment."; exit 1; }
else
  echo "[2/4] Virtual environment already exists."
fi
if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
  echo "      This environment has no pip (a uv-created .venv does this). Bootstrapping it ..."
  if ! .venv/bin/python -m ensurepip --upgrade; then
    echo "[X] Could not add pip to .venv."
    echo "    Delete the .venv folder and run setup.command again."
    exit 1
  fi
fi
echo "[3/4] Installing Python packages ..."
.venv/bin/python -m pip install --upgrade pip --quiet --disable-pip-version-check
.venv/bin/python -m pip install -r requirements.txt --quiet --disable-pip-version-check || {
  echo "[X] Package install failed. Check your internet connection and run setup.command again."
  exit 1
}
echo "      Packages installed."
echo "[4/4] Checking hardware and Ollama ..."
echo
.venv/bin/python -m src.system_check gpu
echo
.venv/bin/python -m src.system_check ollama
status=$?
if [ "$status" -eq 3 ]; then
  echo
  echo "Python setup is done. Install Ollama, then run setup.command again to finish."
  [ "${1:-}" = "--no-pause" ] || read -r -p "Press Enter to close..."
  exit 0
fi
if [ "$status" -ne 0 ]; then
  echo
  echo "Setup did not finish. Fix the problem above and run setup.command again."
  [ "${1:-}" = "--no-pause" ] || read -r -p "Press Enter to close..."
  exit 1
fi
echo
echo "Setup finished. Put your resume in the \"resume\" folder, then double-click run.command"
[ "${1:-}" = "--no-pause" ] || read -r -p "Press Enter to close..."
exit 0
