"""SQLite cache (data/cache.db) so repeat runs only score new jobs.

  embeddings  vector per (text hash, model)
  reviews     LLM review per job, tagged with the context it was made in
              (resume + preferences + model + prompt version). A different context
              means the stored review is stale and the job is scored again.
  meta        small key/value facts, e.g. the last context used
"""
import hashlib
import json
import sqlite3
from array import array
from datetime import datetime
from pathlib import Path

from .paths import DATA_DIR

CACHE_FILE = DATA_DIR / "cache.db"


def digest(*parts) -> str:
    """Stable short hash of any JSON-serializable values."""
    raw = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


class Cache:
    """Thin wrapper around the SQLite file."""

    def __init__(self, path: Path = CACHE_FILE):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS embeddings (
                text_hash TEXT, model TEXT, vector BLOB, PRIMARY KEY (text_hash, model));
            CREATE TABLE IF NOT EXISTS reviews (
                job_id TEXT PRIMARY KEY, context TEXT, review TEXT, scored_at TEXT);
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        """)

    def close(self) -> None:
        self.db.commit()
        self.db.close()

    # ------------------------------------------------------------ embeddings
    def get_vector(self, text: str, model: str) -> list[float] | None:
        row = self.db.execute("SELECT vector FROM embeddings WHERE text_hash=? AND model=?",
                              (digest(text), model)).fetchone()
        return array("f", row[0]).tolist() if row else None

    def put_vector(self, text: str, model: str, vector: list[float]) -> None:
        self.db.execute("INSERT OR REPLACE INTO embeddings VALUES (?, ?, ?)",
                        (digest(text), model, array("f", vector).tobytes()))

    # ------------------------------------------------------------ LLM reviews
    def get_review(self, job_id: str, context: str) -> dict | None:
        """The stored review, only if it was made in the same context."""
        row = self.db.execute("SELECT review, scored_at FROM reviews WHERE job_id=? AND context=?",
                              (job_id, context)).fetchone()
        return {**json.loads(row[0]), "scored_at": row[1]} if row else None

    def put_review(self, job_id: str, context: str, review: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO reviews VALUES (?, ?, ?, ?)",
                        (job_id, context, json.dumps(review, ensure_ascii=False),
                         datetime.now().isoformat(timespec="seconds")))
        self.db.commit()  # commit each review so an interrupted run keeps its progress

    # ------------------------------------------------------------ meta
    def get_meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))
