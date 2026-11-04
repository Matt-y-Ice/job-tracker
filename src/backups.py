"""Timestamped backups of the workbook in output/backups (only the newest few are kept)."""
import shutil
from datetime import datetime
from pathlib import Path

from .paths import OUTPUT_DIR

BACKUP_DIR = OUTPUT_DIR / "backups"
KEEP = 10


def make_backup(path: Path, folder: Path = BACKUP_DIR, keep: int = KEEP) -> Path | None:
    """Copy `path` to folder/<name>_<timestamp><ext> and delete all but the newest `keep`."""
    if not path.exists():
        return None
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    target = folder / f"{path.stem}_{stamp}{path.suffix}"
    shutil.copy2(path, target)
    backups = sorted(folder.glob(f"{path.stem}_*{path.suffix}"))
    for old in backups[:-keep]:
        old.unlink(missing_ok=True)
    return target
