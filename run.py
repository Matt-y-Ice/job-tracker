"""Find remote jobs, rank them against your resume on this computer, and update the Excel tracker.

    python run.py                      everything: resume, preferences, search, rank, Excel
    python run.py --skip-scrape        re-rank the jobs from the last search
    python run.py --rescore-all        review every job again (after editing the prompt or weights)
    python run.py --reset-preferences  answer the preference questions again
    python run.py --model qwen2.5:7b   use a different Ollama model for this run

Double-clicking run.bat does the same as the first line.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def in_project_venv() -> bool:
    """True when this Python is the project's own .venv."""
    return Path(sys.prefix).resolve() == (ROOT / ".venv").resolve()


def main() -> int:
    if sys.version_info < (3, 10):
        print("This needs Python 3.10 or newer. Get it from https://www.python.org/downloads/")
        return 1
    if not in_project_venv():
        if not VENV_PYTHON.exists():
            print("The project is not set up yet. Double-click setup.bat first.")
            return 1
        # Started with another Python (e.g. "python run.py"): rerun with the project's packages.
        return subprocess.call([str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])
    sys.path.insert(0, str(ROOT))
    sys.stdout.reconfigure(errors="replace", line_buffering=True)
    from src.pipeline import main as pipeline_main
    return pipeline_main()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:  # Ctrl+C before the pipeline had started
        print("\nStopped.")
        sys.exit(130)
