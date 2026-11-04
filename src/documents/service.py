"""Runs document generation in the background, one job at a time (they share the GPU)."""
import logging
import threading
from functools import partial
from collections import deque
from pathlib import Path
from typing import Callable

from ..config import load_models
from ..ollama_client import OllamaError
from ..resume_parser import ResumeError, parse_resume
from .generator import DocumentError, find_job, generate, header_info
from .writer import DOCS_DIR, load_meta, save_documents

log = logging.getLogger("jobtracker.documents")


def produce_documents(job_id: str, progress: Callable[[str], None], root: Path = DOCS_DIR) -> dict:
    """Everything for one job: read inputs, write with the model, fact-check, save files."""
    progress("Reading your resume and the job posting…")
    resume = parse_resume()
    job = find_job(job_id)
    draft = generate(job, resume, progress=progress)
    progress("Saving the Word files and making PDFs…")
    name, _, contact = header_info(resume)
    return save_documents(draft, job, name, contact, draft.model or load_models()["documents"]["model"], root)


class DocumentService:
    """Queue of generation requests with per-job status for the dashboard."""

    def __init__(self, root: Path = DOCS_DIR, run: Callable | None = None):
        self.root = root
        self.run = run or partial(produce_documents, root=root)  # files go where this service looks
        self.lock = threading.Lock()
        self.active: dict[str, dict] = {}   # job_id -> {"state", "step", "error"}
        self.queue: deque[str] = deque()
        self.worker: threading.Thread | None = None

    def start(self, job_id: str) -> dict:
        """Queue a job (no-op if it is already queued or running)."""
        with self.lock:
            current = self.active.get(job_id, {})
            if current.get("state") not in ("queued", "running"):
                waiting = bool(self.queue) or any(a["state"] == "running" for a in self.active.values())
                self.active[job_id] = {"state": "queued", "error": "",
                                       "step": "Waiting for the documents in progress to finish…"
                                       if waiting else "Starting…"}
                self.queue.append(job_id)
            if self.worker is None or not self.worker.is_alive():
                self.worker = threading.Thread(target=self._work, name="documents", daemon=True)
                self.worker.start()
        return self.status(job_id)

    def _set(self, job_id: str, **values) -> None:
        with self.lock:
            self.active.setdefault(job_id, {}).update(values)

    def _work(self) -> None:
        while True:
            with self.lock:
                if not self.queue:
                    return
                job_id = self.queue.popleft()
            self._set(job_id, state="running", step="Starting…", error="")
            try:
                self.run(job_id, lambda text: self._set(job_id, step=text))
                self._set(job_id, state="done", step="")
            except (DocumentError, ResumeError, OllamaError) as exc:
                self._set(job_id, state="failed", step="", error=str(exc))
            except Exception as exc:  # keep the worker alive; details go to the log
                log.exception("Document generation crashed for %s", job_id)
                self._set(job_id, state="failed", step="",
                          error=f"Something unexpected went wrong ({exc.__class__.__name__}). See logs/run.log.")

    def status(self, job_id: str) -> dict:
        """State plus the newest saved files for one job."""
        with self.lock:
            live = dict(self.active.get(job_id, {}))
        saved = load_meta(job_id, self.root)
        out = {"state": live.get("state", "done" if saved else "none"), "step": live.get("step", ""),
               "error": live.get("error", ""), "files": [], "notes": [], "pdf_note": "", "generated_at": ""}
        if saved:
            _, meta = saved
            out.update(files=[{**f, "url": f"/api/documents/{job_id}/{f['name']}"} for f in meta["files"]],
                       notes=meta.get("notes", []), pdf_note=meta.get("pdf_note", ""),
                       generated_at=meta.get("generated_at", ""), model=meta.get("model", ""))
        return out

    def all_statuses(self) -> dict[str, dict]:
        """Every job that has documents or is being worked on."""
        ids = {p.name.rsplit("_", 1)[-1] for p in self.root.glob("*_*") if (p / "meta.json").exists()}
        with self.lock:
            ids |= set(self.active)
        return {job_id: self.status(job_id) for job_id in ids}

    def file_path(self, job_id: str, name: str) -> Path | None:
        """Only files listed in that job's meta.json can be downloaded."""
        saved = load_meta(job_id, self.root)
        if not saved:
            return None
        folder, meta = saved
        if name in {f["name"] for f in meta["files"]} and (folder / name).is_file():
            return folder / name
        return None
