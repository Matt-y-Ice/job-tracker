"""Phase 6 tests: status lines, per-step error messages, flags, and run.py itself."""
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from src import pipeline
from src import ranker
from src.excel_builder import EXCLUDED, LOW, TOP, UNSURE, WorkbookLocked
from src.ollama_client import OllamaError
from src.resume_parser import ResumeError
from src.scrapers.base import SourceError

ROOT = Path(__file__).resolve().parent.parent
RESUME = SimpleNamespace(path="resume/me.pdf", skills=["Python"] * 42, titles=["Software Engineer"])


def outcome(errors=(None, None, None)):
    labels = ["RemoteOK", "Remotive", "WWR"]
    sources = [SimpleNamespace(label=l, jobs=[{}] * n, error=e) for l, n, e in zip(labels, (34, 21, 18), errors)]
    return SimpleNamespace(sources=sources, duplicates_removed=12, jobs=[{}] * 61, unsure=[{}] * 5,
                           excluded=[{}] * 9)


RANKED = SimpleNamespace(new=61, cached=9, model="qwen2.5:14b", processor="GPU", failed=0)
BUILT = SimpleNamespace(counts={TOP: 28, LOW: 33, UNSURE: 5, EXCLUDED: 9},
                        path=ROOT / "output" / "Job_Rankings.xlsx")


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.lines = []
        self.calls = {"scrape": 0, "rank": 0, "build": 0}
        patches = {
            "parse_resume": mock.Mock(return_value=RESUME),
            "load_or_ask": mock.Mock(return_value=({"days_back": 25}, "saved")),
            "scrape": mock.Mock(side_effect=lambda **kw: self.count("scrape", outcome())),
            "build": mock.Mock(side_effect=lambda **kw: self.count("build", BUILT)),
        }
        for name, fake in patches.items():
            p = mock.patch.object(pipeline, name, fake)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(ranker, "rank", side_effect=lambda *a, **kw: self.count("rank", RANKED))
        p.start()
        self.addCleanup(p.stop)

    def count(self, name, value):
        self.calls[name] += 1
        return value

    def run_pipeline(self, **opts):
        return pipeline.run(pipeline.Options(interactive=False, **opts), report=self.lines.append)

    def test_status_lines_match_the_spec(self):
        self.assertEqual(self.run_pipeline(), 0)
        self.assertEqual(self.lines[:6], [
            "[1/5] Reading resume... found me.pdf (42 skills detected)",
            "[2/5] Loading preferences... using saved answers",
            "[3/5] Scraping jobs... RemoteOK 34, Remotive 21, WWR 18 (12 duplicates removed)",
            "      61 remote US, 5 unsure (kept separately), 9 not remote US (kept with reasons)",
            "[4/5] Ranking... 61 new jobs, 9 cached | model qwen2.5:14b on GPU",
            "[5/5] Building Excel... 28 top matches, 33 low matches -> output/Job_Rankings.xlsx",
        ])
        self.assertTrue(self.lines[-1].startswith("Done in "))

    def test_skip_scrape(self):
        self.run_pipeline(skip_scrape=True)
        self.assertEqual(self.calls["scrape"], 0)
        self.assertIn("skipped (--skip-scrape)", self.lines[2])

    def test_flags_reach_the_ranker(self):
        self.run_pipeline(rescore_all=True, model="qwen2.5:7b")
        args = ranker.rank.call_args
        self.assertEqual((args.args[2], args.args[3]), ("qwen2.5:7b", True))

    def test_reset_preferences_is_passed_on(self):
        self.run_pipeline(reset_preferences=True)
        self.assertTrue(pipeline.load_or_ask.call_args.kwargs["reset"])

    def test_interactive_prints_header_before_questions(self):
        pipeline.run(pipeline.Options(interactive=True), report=self.lines.append)
        self.assertEqual(self.lines[1], "[2/5] Loading preferences...")

    def assert_stopped(self, code, step_line, *phrases):
        self.assertEqual(code, 1)
        self.assertIn(step_line, self.lines)
        for phrase in phrases:
            self.assertTrue(any(phrase in line for line in self.lines), phrase)

    def test_resume_problem_stops_with_its_message(self):
        pipeline.parse_resume.side_effect = ResumeError("No resume found in resume/. Put ONE resume file")
        code = self.run_pipeline()
        self.assert_stopped(code, "[1/5] Reading resume... STOPPED", "Put ONE resume file")
        self.assertEqual(self.calls["scrape"], 0)

    def test_all_sources_failing_means_no_internet(self):
        pipeline.scrape.side_effect = lambda **kw: outcome(("HTTP 503", "network error", "timeout"))
        self.assert_stopped(self.run_pipeline(), "[3/5] Scraping jobs... STOPPED", "internet connection")

    def test_one_failed_source_is_a_warning_not_a_stop(self):
        def scrape_with_failure(report, **kw):
            report("Lever: FAILED (HTTP 500 from api.lever.co). Other sources continue.")
            report("RemoteOK: 34 jobs, 1s")
            return outcome(("HTTP 500", None, None))
        pipeline.scrape.side_effect = scrape_with_failure
        self.assertEqual(self.run_pipeline(), 0)
        self.assertIn("      ! Lever: FAILED (HTTP 500 from api.lever.co). Other sources continue.", self.lines)
        self.assertNotIn("RemoteOK: 34 jobs, 1s", " ".join(self.lines))  # routine lines go to the log only

    def test_missing_sources_config(self):
        pipeline.scrape.side_effect = SourceError("config/sources.json is missing. Restore it from the download.")
        self.assert_stopped(self.run_pipeline(), "[3/5] Scraping jobs... STOPPED", "sources.json is missing")

    def test_ollama_not_running(self):
        ranker.rank.side_effect = OllamaError("Ollama is not running. Open the Ollama app from the Start menu")
        self.assert_stopped(self.run_pipeline(), "[4/5] Ranking... STOPPED", "Open the Ollama app")
        self.assertEqual(self.calls["build"], 0)

    def test_no_scraped_jobs_with_skip_scrape(self):
        ranker.rank.side_effect = FileNotFoundError("no jobs")
        self.assert_stopped(self.run_pipeline(skip_scrape=True), "[4/5] Ranking... STOPPED",
                            "Run again without --skip-scrape")

    def test_excel_open_in_excel(self):
        pipeline.build.side_effect = WorkbookLocked("Job_Rankings.xlsx is open in Excel. Close the workbook in Excel and run again to save the new rankings.")
        self.assert_stopped(self.run_pipeline(), "[5/5] Building Excel... STOPPED",
                            "Close the workbook in Excel", "--skip-scrape")

    def test_ctrl_c_says_progress_is_kept(self):
        ranker.rank.side_effect = KeyboardInterrupt
        self.assertEqual(self.run_pipeline(), 130)
        self.assertIn("Jobs already ranked are saved", self.lines[-1])

    def test_unexpected_error_points_to_the_log(self):
        pipeline.build.side_effect = RuntimeError("disk full")
        self.assert_stopped(self.run_pipeline(), "[5/5] Building Excel... STOPPED",
                            "RuntimeError: disk full", "logs/run.log")

    def test_llm_failures_are_mentioned(self):
        ranker.rank.side_effect = lambda *a, **kw: SimpleNamespace(**{**vars(RANKED), "failed": 3})
        self.run_pipeline()
        self.assertIn("(3 could not be reviewed; they will be retried next run)", self.lines[4])


class RunPyTests(unittest.TestCase):
    def test_run_py_switches_to_the_project_venv(self):
        """Started with any Python, run.py hands over to .venv (which has the packages)."""
        result = subprocess.run([sys.executable, str(ROOT / "run.py"), "--help"], capture_output=True,
                                text=True, timeout=60, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        for flag in ("--skip-scrape", "--rescore-all", "--reset-preferences", "--model"):
            self.assertIn(flag, result.stdout)

    def test_input_from_nul_is_not_a_console(self):
        """Windows reports NUL as a terminal; has_console() must still say no, so nothing waits for typing."""
        result = subprocess.run([sys.executable, "-c", "from src.questionnaire import has_console; print(has_console())"],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60, cwd=ROOT)
        self.assertEqual(result.stdout.strip(), "False")

    def test_estimate_wording(self):
        self.assertEqual(ranker.about(30), "about 30 seconds")
        self.assertEqual(ranker.about(29 * 60), "about 29 min")


if __name__ == "__main__":
    unittest.main()
