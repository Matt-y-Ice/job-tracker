"""Logging setup: detailed log file in logs/, nothing extra on the console."""
import logging
from pathlib import Path

from .paths import LOGS_DIR


def setup_logging(filename: str = "run.log", level: int = logging.INFO) -> Path:
    """Send all 'jobtracker' logs to logs/<filename> and return its path."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    path = LOGS_DIR / filename
    logger = logging.getLogger("jobtracker")
    logger.setLevel(level)
    if not any(getattr(h, "baseFilename", None) == str(path) for h in logger.handlers):
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        logger.addHandler(handler)
    return path
