"""One lock for every process that writes the workbook (Excel builder, dashboard, refresh runs),
so two writes never happen at once. The lock file lives in data/, out of the way."""
from contextlib import contextmanager

from filelock import FileLock, Timeout

from .paths import DATA_DIR

LOCK_FILE = DATA_DIR / "workbook.lock"


class WorkbookBusy(Exception):
    """Another write held the lock for too long."""


@contextmanager
def workbook_lock(timeout: float = 120):
    """Hold the cross-process workbook lock, waiting up to `timeout` seconds."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(LOCK_FILE))
    try:
        lock.acquire(timeout=timeout)
    except Timeout as exc:
        raise WorkbookBusy("Another save to the workbook is still running. Try again in a minute.") from exc
    try:
        yield
    finally:
        lock.release()
