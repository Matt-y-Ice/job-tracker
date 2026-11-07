#!/bin/bash
# Reset this folder to a fresh download. Only touches files inside this folder.
cd "$(dirname "$0")"
set -u

ASSUME_YES=0
WITH_MODELS=0
for arg in "$@"; do
  case "$arg" in
    --yes) ASSUME_YES=1 ;;
    --with-models) WITH_MODELS=1 ;;
  esac
done

if [ ! -f run.py ] || [ ! -f src/pipeline.py ]; then
  echo "scrub.command must stay in the Job Tracker folder (next to run.py). Nothing was deleted."
  [ "$ASSUME_YES" -eq 1 ] || read -r -p "Press Enter to close..."
  exit 1
fi

echo "============================================"
echo "  Job Tracker scrub: reset to a fresh copy"
echo "============================================"
echo
echo "This PERMANENTLY deletes, inside $(pwd) only:"
echo "  resume/                  your resume (the empty placeholder file stays)"
echo "  config/preferences.json  your answers to the questions"
echo "  config/api_keys.json     optional API keys"
echo "  config/skill_catalog.json  your GitHub projects and skills"
echo "  data/                    job searches, rankings, the AI score cache, queued edits"
echo "  output/                  the Excel workbook, its backups, generated resumes and cover letters"
echo "  logs/                    run logs"
echo "  .venv/                   the Python environment (setup.command rebuilds it)"
echo "  __pycache__/             Python's compiled caches"
if [ "$WITH_MODELS" -eq 1 ]; then
  echo "  Ollama models            the AI models listed in config/models.json"
fi
echo
echo "Nothing outside this folder is touched. Program files and default settings stay."
echo "To keep your data, copy this whole folder somewhere else FIRST."
echo "Close Excel, the dashboard window, and any running job search before continuing."
echo

if [ "$ASSUME_YES" -ne 1 ]; then
  read -r -p "Type SCRUB to delete everything listed above, or press Enter to cancel: " ANSWER
  if [ "${ANSWER:-}" != "SCRUB" ]; then
    echo "Cancelled. Nothing was deleted."
    exit 0
  fi
fi

echo
PROBLEMS=0

for f in config/preferences.json config/api_keys.json config/skill_catalog.json; do
  [ -f "$f" ] && rm -f -- "$f"
done

if [ -d resume ]; then
  find resume -mindepth 1 ! -name .gitkeep -exec rm -rf -- {} +
fi
mkdir -p resume
[ -f resume/.gitkeep ] || : > resume/.gitkeep
echo "  Removed your resume and personal settings."

remove_dir() {
  if [ -d "$1" ]; then
    rm -rf -- "$1"
  fi
}
remove_dir data
remove_dir output
remove_dir logs
echo "  Removed job searches, rankings, the workbook, generated documents, and logs."

if [ "$WITH_MODELS" -eq 1 ] && [ -f config/models.json ]; then
  PY="python3"
  [ -x .venv/bin/python ] && PY=".venv/bin/python"
  "$PY" -c 'import json
m=json.load(open("config/models.json"))
names=[]
for key in ("llm","embedding","documents"):
    name=(m.get(key) or {}).get("model")
    if name and name not in names:
        names.append(name)
print("\n".join(names))' | while IFS= read -r model; do
    [ -n "$model" ] || continue
    if command -v ollama >/dev/null 2>&1; then
      ollama rm "$model" >/dev/null 2>&1 && echo "  Removed Ollama model $model"
    fi
  done
fi

remove_dir .venv
find . -type d -name __pycache__ -print0 | while IFS= read -r -d '' dir; do
  rm -rf -- "$dir"
done
find . -type f -name '*.pyc' -delete
echo "  Removed the Python environment and compiled caches."

echo
for p in data output logs .venv config/preferences.json config/api_keys.json config/skill_catalog.json; do
  if [ -e "$p" ]; then
    echo "  [!] Still here: $p"
    PROBLEMS=1
  fi
done
if [ -d resume ]; then
  while IFS= read -r leftover; do
    [ -n "$leftover" ] || continue
    echo "  [!] Still here: $leftover"
    PROBLEMS=1
  done < <(find resume -mindepth 1 ! -name .gitkeep)
fi

if [ "$PROBLEMS" -eq 1 ]; then
  echo "Some items could not be deleted, usually because a program still has them open."
  echo "Close Excel and the dashboard window, then run scrub.command again."
  [ "$ASSUME_YES" -eq 1 ] || read -r -p "Press Enter to close..."
  exit 1
fi

echo "Done. This folder is back to a fresh copy and is safe to share."
echo "  To use it again:  double-click setup.command"
[ "$ASSUME_YES" -eq 1 ] || read -r -p "Press Enter to close..."
exit 0
