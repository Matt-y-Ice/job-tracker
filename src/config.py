"""Small helpers for reading and writing the JSON config files."""
import json
import os
import tempfile
from pathlib import Path

from .paths import MODELS_FILE

DEFAULT_MODELS = {
    "llm": {
        "provider": "ollama",
        "model": "qwen2.5:14b",
        "host": "http://localhost:11434",
        "temperature": 0.1,
        "num_ctx": 8192,
        "max_description_chars": 6000,
    },
    "embedding": {"provider": "ollama", "model": "nomic-embed-text"},
    "documents": {
        "model": "qwen2.5:32b",
        "temperature": 0.4,
        "num_ctx": 8192,
        "max_description_chars": 5000,
        "keep_alive": "5m",
    },
}


class ConfigError(Exception):
    """Raised when a config file exists but cannot be read."""


def load_json(path: Path, default=None):
    """Read a JSON file, returning `default` if it does not exist."""
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"{path.name} is not valid JSON (line {exc.lineno}, column {exc.colno}). "
            f"Fix the typo or delete the file to recreate it."
        ) from exc


def save_json(path: Path, data) -> None:
    """Write JSON atomically so a crash never leaves a half-written file.

    The temporary file gets a unique name, so two threads saving at once cannot collide.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=2, ensure_ascii=False))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def load_models() -> dict:
    """Return models.json merged over the built-in defaults."""
    user = load_json(MODELS_FILE, {}) or {}
    merged = {key: dict(value) for key, value in DEFAULT_MODELS.items()}
    for section in merged:
        merged[section].update(user.get(section, {}))
    return merged
