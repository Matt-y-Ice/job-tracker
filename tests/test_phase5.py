"""Phase 5 tests: dashboard API, writing edits to Excel, locked-file queueing, refresh streaming."""
import json
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

from src import excel_builder as xb
from src.dashboard.app import create_app
from src.dashboard.refresh import RefreshRunner
from tests.test_phase4 import ranked_job

HEADERS = {"X-Job-Tracker": "1"}


class DashboardTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.book = self.dir / "Job_Rankings.xlsx"
        self.pending = self.dir / "pending.json"
        ranked = self.dir / "ranked.json"
        ranked.write_text(json.dumps({"ranked_at": "2026-10-29T08:00:00", "jobs": [
            ranked_job("a", 74, title="Backend Engineer", red_flags=["On-call rotation"]),
            ranked_job("b", 30), ranked_job("u", 60, bucket="unsure"),
            ranked_job("x", 0, bucket="excluded")]}), encoding="utf-8")
        xb.build(ranked, self.book)
        self.client = self.app().test_client()

    def app(self, **kwargs):
        return create_app(self.book, self.pending, retry_every=None, **kwargs)

    def cell(self, job_id, header):
        wb = load_workbook(self.book)
        for sheet in xb.TRACKED_SHEETS:
            ws = wb[sheet]
            headers = [c.value for c in ws[1]]
            for row in ws.iter_rows(min_row=2, values_only=True):
                if row[headers.index("Job ID")] == job_id:
                    return row[headers.index(header)]
        raise KeyError(job_id)

    def post(self, job_id, body, client=None):
        return (client or self.client).post(f"/api/jobs/{job_id}", json=body, headers=HEADERS)

    def test_page_and_jobs(self):
        with self.client.get("/") as page, self.client.get("/static/app.js") as script:
            self.assertIn(b"Job Tracker", page.data)
            self.assertEqual(script.status_code, 200)
        data = self.client.get("/api/jobs").get_json()
        jobs = {j["id"]: j for j in data["jobs"]}
        self.assertEqual(set(jobs), {"a", "b", "u"})            # excluded jobs are not tracked
        self.assertEqual(jobs["a"]["sheet"], "Top Matches")
        self.assertEqual(jobs["u"]["sheet"], "Unsure Location")
        self.assertEqual(jobs["a"]["link"], "https://jobs.example.com/a")
        self.assertEqual(jobs["a"]["status"], "New")
        self.assertIn("On-call rotation", jobs["a"]["red_flags"])
        self.assertEqual(jobs["a"]["subscores"][0], ["Skill (40)", 30])
        self.assertEqual(data["statuses"], xb.STATUSES)

    def test_requests_from_other_sites_are_refused(self):
        self.assertEqual(self.client.get("/api/jobs", headers={"Host": "evil.example"}).status_code, 403)
        self.assertEqual(self.client.post("/api/jobs/a", json={"status": "Applied"}).status_code, 403)
        self.assertEqual(self.client.post("/api/refresh").status_code, 403)

    def test_edit_writes_to_excel_with_backup(self):
        before = self.client.get("/api/status").get_json()["version"]
        resp = self.post("a", {"status": "Applied", "date_applied": "2026-10-25", "notes": "Sent via referral"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["saved"])
        self.assertEqual(self.cell("a", "Status"), "Applied")
        self.assertEqual(self.cell("a", "Date Applied"), datetime(2026, 10, 25))
        self.assertEqual(self.cell("a", "Notes"), "Sent via referral")
        self.assertEqual(self.cell("b", "Status"), "New")        # other rows untouched
        self.assertNotEqual(resp.get_json()["version"], before)
        self.assertTrue(list((self.dir / "backups").glob("*.xlsx")))
        self.post("a", {"date_applied": ""})
        self.assertIsNone(self.cell("a", "Date Applied"))         # clearing works too

    def test_bad_edits_are_rejected(self):
        self.assertEqual(self.post("a", {"status": "Maybe"}).status_code, 400)
        self.assertEqual(self.post("a", {"date_applied": "next week"}).status_code, 400)
        self.assertEqual(self.post("a", {"score": 100}).status_code, 400)
        self.assertEqual(self.post("a", {}).status_code, 400)
        self.assertEqual(self.post("nope", {"status": "Applied"}).status_code, 404)

    def test_locked_workbook_queues_then_saves(self):
        calls = {"n": 0}

        def flaky_save(wb, path):
            calls["n"] += 1
            if calls["n"] == 1:
                raise xb.WorkbookLocked("open in Excel")
            return xb.save(wb, path)

        app = self.app(save_fn=flaky_save)
        client = app.test_client()
        data = self.post("a", {"status": "Interested"}, client).get_json()
        self.assertFalse(data["saved"])
        self.assertIn("Close the workbook in Excel", data["message"])
        self.assertEqual(data["pending"], 1)
        self.assertTrue(self.pending.exists())                    # survives a restart
        job = next(j for j in client.get("/api/jobs").get_json()["jobs"] if j["id"] == "a")
        self.assertEqual((job["status"], job["pending"]), ("Interested", True))
        self.assertEqual(self.cell("a", "Status"), "New")

        self.assertTrue(app.config["writer"].flush())             # what the retry thread does
        self.assertEqual(self.cell("a", "Status"), "Interested")
        self.assertFalse(self.pending.exists())
        self.assertEqual(client.get("/api/status").get_json()["message"], "")

    def test_queued_edits_survive_a_restart(self):
        self.pending.write_text(json.dumps({"b": {"notes": "From before the restart"}}), encoding="utf-8")
        app = self.app()
        self.assertEqual(app.config["writer"].pending, {"b": {"notes": "From before the restart"}})
        app.config["writer"].flush()
        self.assertEqual(self.cell("b", "Notes"), "From before the restart")

    def test_retry_loop_saves_when_excel_closes(self):
        locked = {"on": True}

        def save_when_unlocked(wb, path):
            if locked["on"]:
                raise xb.WorkbookLocked("open")
            return xb.save(wb, path)

        app = create_app(self.book, self.pending, retry_every=0.2, save_fn=save_when_unlocked)
        self.post("a", {"notes": "retry me"}, app.test_client())
        locked["on"] = False                                       # "the user closes Excel"
        deadline = time.time() + 5
        while app.config["writer"].pending and time.time() < deadline:
            time.sleep(0.1)
        self.assertEqual(self.cell("a", "Notes"), "retry me")

    def test_changes_made_in_excel_show_up(self):
        version = self.client.get("/api/status").get_json()["version"]
        time.sleep(0.05)
        wb = load_workbook(self.book)
        wb[xb.TOP]["AA2"] = "Typed in Excel"
        wb.save(self.book)
        self.assertNotEqual(self.client.get("/api/status").get_json()["version"], version)
        job = next(j for j in self.client.get("/api/jobs").get_json()["jobs"] if j["id"] == "a")
        self.assertEqual(job["notes"], "Typed in Excel")

    def test_concurrent_edits_all_land(self):
        app = self.app()  # one server, several requests at the same moment
        jobs = ["a", "b", "u"]
        threads = [threading.Thread(target=self.post, args=(jid, {"notes": f"note {jid}"}, app.test_client()))
                   for jid in jobs]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for jid in jobs:
            self.assertEqual(self.cell(jid, "Notes"), f"note {jid}")

    def test_refresh_streams_lines_and_progress(self):
        script = ("import sys,time\n"
                  "print('[1/5] Reading resume...', flush=True)\n"
                  "for i in range(3):\n"
                  "    sys.stdout.write(f'\\rRanking: {i}/3'); sys.stdout.flush(); time.sleep(0.05)\n"
                  "print()\nprint('Done.')\n")
        client = self.app(refresh_command=[sys.executable, "-c", script]).test_client()
        self.assertEqual(client.post("/api/refresh", headers=HEADERS).status_code, 200)
        self.assertEqual(client.post("/api/refresh", headers=HEADERS).status_code, 409)
        body = client.get("/api/refresh/stream").get_data(as_text=True)
        events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
        kinds = [(e["kind"], e["text"]) for e in events]
        self.assertEqual(kinds[0], ("line", "[1/5] Reading resume..."))
        self.assertIn(("progress", "Ranking: 1/3"), kinds)
        self.assertIn(("line", "Done."), kinds)
        self.assertEqual(kinds[-1], ("done", "0"))
        self.assertEqual(client.post("/api/refresh", headers=HEADERS).status_code, 200)  # can run again


class RefreshRunnerTests(unittest.TestCase):
    def test_failing_command_reports_exit_code(self):
        runner = RefreshRunner([sys.executable, "-c", "print('STOPPED: no resume'); raise SystemExit(1)"])
        runner.start()
        deadline = time.time() + 10
        while runner.state()["running"] and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(runner.state()["exit_code"], 1)
        self.assertEqual(runner.since(0)[0]["text"], "STOPPED: no resume")


if __name__ == "__main__":
    unittest.main()
