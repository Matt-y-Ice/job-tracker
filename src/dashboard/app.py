"""Flask app: the page, the jobs API, edits, workbook status, and the refresh stream.

Only reachable from this computer. Requests must come from the dashboard page itself:
the Host must be localhost, and changes need the X-Job-Tracker header, which other
websites cannot send without the browser blocking them.
"""
import json
import time
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request, send_file, send_from_directory

from ..excel_builder import OUTPUT_FILE, STATUSES
from ..paths import DATA_DIR
from ..documents.service import DocumentService
from .refresh import RefreshRunner
from .workbook import JobCache, WorkbookWriter, clean_changes, file_version

STATIC = Path(__file__).resolve().parent / "static"
ALLOWED_HOSTS = {"localhost", "127.0.0.1"}


def create_app(workbook: Path = OUTPUT_FILE, pending_file: Path = DATA_DIR / "pending_writes.json",
               refresh_command: list[str] | None = None, retry_every: float | None = 5,
               save_fn=None, documents: DocumentService | None = None) -> Flask:
    """Build the app. Tests pass their own paths, command, save function, and document service."""
    app = Flask(__name__, static_folder=None)
    documents = documents or DocumentService()
    cache = JobCache(workbook)
    writer = WorkbookWriter(workbook, pending_file, **({"save_fn": save_fn} if save_fn else {}))
    runner = RefreshRunner(refresh_command)
    app.config.update(cache=cache, writer=writer, runner=runner, documents=documents)
    if retry_every:
        writer.start_retry_loop(retry_every)

    @app.before_request
    def only_this_dashboard():
        if request.host.split(":")[0] not in ALLOWED_HOSTS:
            abort(403)
        if request.method == "POST" and request.headers.get("X-Job-Tracker") != "1":
            abort(403)

    @app.after_request
    def no_cache(resp):
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    def status_payload() -> dict:
        return {"version": file_version(workbook), "pending": len(writer.pending),
                "message": writer.message, "refresh": runner.state(),
                "workbook_exists": workbook.exists()}

    @app.get("/")
    def index():
        return send_from_directory(STATIC, "index.html")

    @app.get("/static/<path:name>")
    def static_files(name):
        return send_from_directory(STATIC, name)

    @app.get("/api/jobs")
    def jobs():
        version, rows = cache.get()
        return jsonify({**status_payload(), "version": version, "statuses": STATUSES,
                        "jobs": writer.overlay(rows)})

    @app.get("/api/status")
    def status():
        return jsonify(status_payload())

    @app.post("/api/jobs/<job_id>")
    def update(job_id):
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "Send the changes as JSON."}), 400
        try:
            changes = clean_changes(body)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        _, rows = cache.get()
        if not any(job["id"] == job_id for job in rows):
            return jsonify({"error": "That job is not in the workbook (it may have been removed)."}), 404
        saved = writer.queue(job_id, changes)
        return jsonify({**status_payload(), "saved": saved})

    @app.post("/api/refresh")
    def start_refresh():
        if not runner.start():
            return jsonify({"error": "A refresh is already running."}), 409
        return jsonify(runner.state())

    @app.get("/api/refresh/stream")
    def stream():
        start = request.args.get("from", 0, type=int)

        def events():
            index, last_beat = start, time.monotonic()
            while True:
                batch = runner.since(index)
                for event in batch:
                    yield f"data: {json.dumps(event)}\n\n"
                    index = event["i"] + 1
                    if event["kind"] == "done":
                        return
                if not batch and not runner.state()["running"] and runner.events:
                    return
                if time.monotonic() - last_beat > 15:
                    yield ": keep-alive\n\n"
                    last_beat = time.monotonic()
                time.sleep(0.25)

        return Response(events(), mimetype="text/event-stream",
                        headers={"X-Accel-Buffering": "no"})

    # ------------------------------------------------------------ tailored documents

    @app.get("/api/documents")
    def documents_list():
        return jsonify(documents.all_statuses())

    @app.post("/api/documents/<job_id>")
    def documents_start(job_id):
        _, rows = cache.get()
        if not any(job["id"] == job_id for job in rows):
            return jsonify({"error": "That job is not in the workbook (it may have been removed)."}), 404
        return jsonify(documents.start(job_id))

    @app.get("/api/documents/<job_id>")
    def documents_status(job_id):
        return jsonify(documents.status(job_id))

    @app.get("/api/documents/<job_id>/<name>")
    def documents_file(job_id, name):
        path = documents.file_path(job_id, name)  # only files listed for that job, never other paths
        if path is None:
            abort(404)
        return send_file(path, as_attachment=True, download_name=name)

    return app
