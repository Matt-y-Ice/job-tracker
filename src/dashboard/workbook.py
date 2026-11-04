"""Read jobs from the workbook, and write dashboard edits back to it.

The workbook is the single source of truth. Edits are queued, then written by Job ID under
the shared workbook lock. If Excel has the file open (Windows locks it), the edits stay
queued (also on disk, so a restart loses nothing) and are retried every few seconds.
"""
import json
import logging
import threading
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook

from ..config import save_json
from ..excel_builder import (DATE_FORMAT, JOB_ID, LINK, STATUSES, SUBSCORES, TRACKED_SHEETS,
                             WorkbookLocked, save)
from ..workbook_lock import WorkbookBusy, workbook_lock

log = logging.getLogger("jobtracker.dashboard")

EDITABLE = {"status": "Status", "date_applied": "Date Applied", "notes": "Notes"}
FIELDS = {  # dashboard field -> workbook header
    "rank": "Rank", "score": "Score", "title": "Title", "company": "Company", "location": "Location",
    "remote_type": "Remote Type", "salary": "Salary", "source": "Source", "date_posted": "Date Posted",
    "why": "Why It Matched", "missing": "Missing Skills", "red_flags": "Red Flags",
    "location_check": "Location Check", "last_seen": "Last Seen", **EDITABLE,
}
LOCKED_MESSAGE = "Close the workbook in Excel to save changes. They are queued and will save automatically."


def file_version(path: Path) -> int:
    """Changes whenever the file is written (by us, by Excel, or by a refresh)."""
    try:
        return path.stat().st_mtime_ns
    except FileNotFoundError:
        return 0


def as_text(value) -> str | int | float:
    """JSON-friendly cell value: dates as YYYY-MM-DD, empty as ''."""
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    return "" if value is None else value


def read_jobs(path: Path) -> list[dict]:
    """Every row of the tracked sheets as a dict for the dashboard."""
    if not path.exists():
        return []
    wb = load_workbook(path)
    jobs = []
    for sheet in TRACKED_SHEETS:
        if sheet not in wb.sheetnames:
            continue
        ws = wb[sheet]
        headers = [c.value for c in ws[1]]
        col = {h: i for i, h in enumerate(headers) if h}
        for cells in ws.iter_rows(min_row=2):
            get = lambda h: cells[col[h]].value if h in col and col[h] < len(cells) else None
            if not any(c.value not in (None, "") for c in cells):
                continue
            link_cell = cells[col[LINK]] if LINK in col else None
            link = link_cell.hyperlink.target if link_cell is not None and link_cell.hyperlink else ""
            job = {key: as_text(get(header)) for key, header in FIELDS.items()}
            job.update({
                "id": str(get(JOB_ID) or ""),
                "sheet": sheet,
                "link": link if str(link).startswith(("http://", "https://")) else "",
                "subscores": [[header, get(header)] for header in SUBSCORES],  # list keeps rubric order
            })
            job["status"] = job["status"] or "New"
            jobs.append(job)
    wb.close()
    return jobs


class JobCache:
    """Re-reads the workbook only when its file version changes."""

    def __init__(self, path: Path):
        self.path, self.version, self.jobs = path, None, []
        self.lock = threading.Lock()

    def get(self) -> tuple[int, list[dict]]:
        with self.lock:
            version = file_version(self.path)
            if version != self.version:
                self.jobs, self.version = read_jobs(self.path), version
            return self.version, self.jobs


def clean_changes(changes: dict) -> dict:
    """Validate an edit from the browser. Raises ValueError with a readable message."""
    out = {}
    for key, value in changes.items():
        if key not in EDITABLE:
            raise ValueError(f"'{key}' cannot be edited from the dashboard")
        value = "" if value is None else str(value).strip()
        if key == "status" and value not in STATUSES:
            raise ValueError(f"Status must be one of: {', '.join(STATUSES)}")
        if key == "date_applied" and value:
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("Date Applied must look like 2026-10-29") from exc
        if key == "notes" and len(value) > 5000:
            raise ValueError("Notes are limited to 5,000 characters")
        out[key] = value
    if not out:
        raise ValueError("Nothing to change")
    return out


class WorkbookWriter:
    """Queues edits and writes them to the workbook; retries while Excel has it locked."""

    def __init__(self, path: Path, pending_file: Path, save_fn=save, lock_timeout: float = 10):
        self.path, self.pending_file, self.save_fn = path, pending_file, save_fn
        self.lock_timeout = lock_timeout
        self.lock = threading.Lock()
        self.pending: dict[str, dict] = self._load_pending()
        self.message = ""

    def _load_pending(self) -> dict:
        try:
            return json.loads(self.pending_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _persist(self) -> None:
        if self.pending:
            save_json(self.pending_file, self.pending)
        else:
            self.pending_file.unlink(missing_ok=True)

    def queue(self, job_id: str, changes: dict) -> bool:
        """Add an edit and try to save right away. Returns True if it is in the workbook now."""
        with self.lock:
            self.pending.setdefault(job_id, {}).update(changes)
            self._persist()
        return self.flush()

    def flush(self) -> bool:
        """Write every queued edit. Returns True when nothing is left waiting."""
        with self.lock:
            if not self.pending:
                return True
            try:
                with workbook_lock(timeout=self.lock_timeout):
                    wb = load_workbook(self.path)
                    missing = self._apply(wb)
                    self.save_fn(wb, self.path)  # backs up first; raises WorkbookLocked if open in Excel
            except WorkbookLocked:
                self.message = LOCKED_MESSAGE
                return False
            except WorkbookBusy:
                self.message = "Another save is running (probably a refresh). Your changes are queued."
                return False
            except FileNotFoundError:
                self.message = "The workbook is missing. Run a refresh to create it; your changes are queued."
                return False
            for job_id in missing:
                log.warning("Dropped edit for job %s: not found in the workbook", job_id)
            self.pending.clear()
            self._persist()
            self.message = ""
            return True

    def _apply(self, wb) -> list[str]:
        """Set the queued values on each job's row. Returns ids that were not found."""
        rows = {}
        for sheet in TRACKED_SHEETS:
            if sheet not in wb.sheetnames:
                continue
            ws = wb[sheet]
            headers = {c.value: c.column for c in ws[1] if c.value}
            for r in range(2, ws.max_row + 1):
                job_id = ws.cell(row=r, column=headers[JOB_ID]).value if JOB_ID in headers else None
                if job_id:
                    rows[str(job_id)] = (ws, r, headers)
        missing = []
        for job_id, changes in self.pending.items():
            if job_id not in rows:
                missing.append(job_id)
                continue
            ws, r, headers = rows[job_id]
            for key, value in changes.items():
                cell = ws.cell(row=r, column=headers[EDITABLE[key]])
                if key == "date_applied":
                    cell.value = datetime.fromisoformat(value) if value else None
                    cell.number_format = DATE_FORMAT
                else:
                    cell.value = value or None
        return missing

    def overlay(self, jobs: list[dict]) -> list[dict]:
        """Jobs with queued (not yet saved) edits applied, marked pending."""
        if not self.pending:
            return jobs
        out = []
        for job in jobs:
            changes = self.pending.get(job["id"])
            out.append({**job, **changes, "pending": True} if changes else job)
        return out

    def start_retry_loop(self, every: float = 5) -> threading.Thread:
        """Background thread that keeps retrying queued edits."""
        def loop():
            stop = threading.Event()
            while not stop.wait(every):
                if self.pending:
                    try:
                        self.flush()
                    except Exception:  # never let the retry thread die
                        log.exception("Retrying queued edits failed")
        thread = threading.Thread(target=loop, name="workbook-retry", daemon=True)
        thread.start()
        return thread
