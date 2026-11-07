"""Launcher names and the few sentences that name a Windows-only UI."""
import sys


def setup_hint() -> str:
    if sys.platform == "win32":
        return "setup.bat"
    if sys.platform == "darwin":
        return "setup.command"
    return "setup"


def run_hint() -> str:
    if sys.platform == "win32":
        return "run.bat"
    if sys.platform == "darwin":
        return "run.command"
    return "run.py"


def dashboard_hint() -> str:
    if sys.platform == "win32":
        return "double-click dashboard.bat"
    if sys.platform == "darwin":
        return "double-click dashboard.command"
    return "run python -m src.dashboard"


def ollama_not_running() -> str:
    if sys.platform == "win32":
        return ("Ollama is not running. Open the Ollama app from the Start menu "
                "(or run 'ollama serve'), then try again.")
    if sys.platform == "darwin":
        return ("Ollama is not running. Open the Ollama app "
                "(or run 'ollama serve'), then try again.")
    return "Ollama is not running. Run 'ollama serve', then try again."
