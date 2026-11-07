#!/bin/bash
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "The project is not set up yet. Double-click setup.command first."
  read -r -p "Press Enter to close..."
  exit 1
fi
exec .venv/bin/python -m src.dashboard "$@"
