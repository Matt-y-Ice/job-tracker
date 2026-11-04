"""Project paths, so every module agrees on where things live."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CONFIG_DIR = ROOT / "config"
RESUME_DIR = ROOT / "resume"
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
LOGS_DIR = ROOT / "logs"
SAMPLES_DIR = ROOT / "samples"

PREFERENCES_FILE = CONFIG_DIR / "preferences.json"
SOURCES_FILE = CONFIG_DIR / "sources.json"
MODELS_FILE = CONFIG_DIR / "models.json"
