"""Run the pipeline in the background and keep its output so the page can stream it."""
import os
import subprocess
import sys
import threading
import time

from ..paths import ROOT

DEFAULT_COMMAND = [sys.executable, "-m", "src.pipeline", "--no-input"]


class RefreshRunner:
    """One pipeline run at a time. Output is kept as events the page can read from any point.

    Event kinds: 'line' (a finished line), 'progress' (a progress-bar update that replaces the
    previous one), 'done' (the run ended; text is the exit code).
    """

    def __init__(self, command: list[str] | None = None, cwd=ROOT):
        self.command, self.cwd = command or DEFAULT_COMMAND, cwd
        self.events: list[dict] = []
        self.running = False
        self.exit_code: int | None = None
        self.started_at: float | None = None
        self.lock = threading.Lock()

    def start(self) -> bool:
        """Start a run. False if one is already running."""
        with self.lock:
            if self.running:
                return False
            self.running, self.exit_code, self.events = True, None, []
            self.started_at = time.time()
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            proc = subprocess.Popen(self.command, cwd=self.cwd, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=flags)
        except OSError as exc:
            self._add("line", f"Could not start the refresh: {exc}")
            self._finish(1)
            return True
        threading.Thread(target=self._read, args=(proc,), daemon=True).start()
        return True

    def _add(self, kind: str, text: str) -> None:
        with self.lock:
            self.events.append({"i": len(self.events), "kind": kind, "text": text})

    def _finish(self, code: int) -> None:
        self._add("done", str(code))
        with self.lock:
            self.running, self.exit_code = False, code

    def _read(self, proc: subprocess.Popen) -> None:
        """Split output on newlines (lines) and carriage returns (progress bar redraws)."""
        buffer = ""
        while True:
            chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(1)
            if not chunk:
                break
            buffer += chunk.decode("utf-8", errors="replace")
            while True:
                cut = min((i for i in (buffer.find("\n"), buffer.find("\r")) if i >= 0), default=-1)
                if cut < 0:
                    break
                text, sep, buffer = buffer[:cut], buffer[cut], buffer[cut + 1:]
                if sep == "\r" and buffer.startswith("\n"):
                    sep, buffer = "\n", buffer[1:]
                if text.strip():
                    self._add("progress" if sep == "\r" else "line", text.rstrip())
        if buffer.strip():
            self._add("line", buffer.rstrip())
        proc.stdout.close()
        self._finish(proc.wait())

    def since(self, index: int) -> list[dict]:
        """Events from `index` on."""
        with self.lock:
            return self.events[index:]

    def state(self) -> dict:
        with self.lock:
            return {"running": self.running, "exit_code": self.exit_code, "started_at": self.started_at}
