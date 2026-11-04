"""Phase 7 tests: tailored documents. The fake model misbehaves on purpose (invents a skill, an
employer, a statistic; changes a title and dates; drops metrics; adds filler) and every fact
check has to catch it. Files, the background queue, and the download endpoints are tested too."""
import json
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from docx import Document

from src.dashboard.app import create_app
from src.documents import generator as gen
from src.documents.service import DocumentService
from src.documents.writer import PdfUnavailable, folder_for, load_meta, safe_name, save_documents
from src.excel_builder import build
from src.ollama_client import OllamaError
from src.resume_parser import parse_resume
from tests.test_phase4 import ranked_job

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
_no_catalog = mock.patch.object(gen, "load_catalog", return_value={})


def setUpModule():
    _no_catalog.start()  # never read the real config/skill_catalog.json in tests


def tearDownModule():
    _no_catalog.stop()
RESUME = parse_resume(SAMPLES / "sample_resume.txt", date(2026, 10, 29))
JOB = {"id": "job1", "title": "Backend Engineer", "company": "Acme Pay", "location": "Remote - USA",
       "description": "We need Python, FastAPI, Kafka, and Rust. 5+ years. Serve 10M users."}

BAD_RESUME = {
    "headline": "Principal Architect",                       # not their title -> restored
    "summary": "Experienced backend developer building FastAPI services. Proven track record of excellence. "
               "Cut costs by 37% company-wide.",              # filler + invented number
    "skills": [{"label": "Languages", "items": ["Python", "Go", "Rust"]}],   # Rust is not on the resume
    "experience": [
        {"title": "Senior Backend Developer", "company": "Brightwave Analytics", "location": "Austin, TX",
         "dates": "2019 - Present",                            # wrong title and wrong start year
         "bullets": ["Designed and maintained FastAPI microservices handling heavy daily traffic.",  # lost "12" and "40M"
                     "Reduced latency by 55% with caching.",   # invented number
                     "Wrote pytest suites that raised coverage from 45% to 88%."]},
        {"title": "Staff Engineer", "company": "Globex Corporation", "location": "", "dates": "05/2019 - 08/2019",
         "bullets": ["Built a Django admin tool for support staff."]},
        {"title": "Engineer", "company": "Initrode Systems", "location": "", "dates": "2015 - 2016",
         "bullets": ["Invented job."]},                        # employer not on the resume
    ],
    "projects": [{"name": "Budget Tracker", "tech": "Flask, SQLite", "bullets": ["Personal finance app."]},
                 {"name": "Quantum Compiler", "tech": "Rust", "bullets": ["Made up."]}],
}
GOOD_LETTER = {"greeting": "Dear Hiring Manager,", "closing": "Sincerely,", "paragraphs": [
    "I build Python services for a living. I am confident I will crush it.",
    "At Brightwave I moved batch jobs to Airflow, cutting failed nightly runs by 80%. You serve 10M users.",
    "I also reduced cloud spend by 33% at a past job."]}


class FakeOllama:
    def __init__(self, installed=("qwen2.5:32b", "qwen2.5:14b"), resume=BAD_RESUME, letter=GOOD_LETTER):
        self.installed, self.resume, self.letter, self.models_used = installed, resume, letter, []

    def check(self, *models):
        for m in models:
            if m not in self.installed:
                raise OllamaError(f"The model '{m}' is not downloaded. Run setup.bat")

    def chat_json(self, model, messages, schema, options, keep_alive="15m"):
        self.models_used.append(model)
        return json.loads(json.dumps(self.resume if "experience" in schema["properties"] else self.letter))


class FactCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.draft = gen.generate(JOB, RESUME, prefs={"target_titles": ["Backend Engineer"]}, ollama=FakeOllama())
        cls.resume, cls.notes = cls.draft.resume, " | ".join(cls.draft.notes)

    def test_header_copied_exactly(self):
        name, headline, contact = gen.header_info(RESUME)
        self.assertEqual((name, headline), ("Jordan Rivera", "Backend Developer"))
        self.assertIn("jordan.rivera@example.com", contact)

    def test_headline_must_be_a_real_or_target_title(self):
        self.assertEqual(self.resume["headline"], "Backend Developer")

    def test_unsupported_skill_removed(self):
        items = [i for g in self.resume["skills"] for i in g["items"]]
        self.assertNotIn("Rust", items)
        self.assertIn('Left out skill "Rust"', self.notes)

    def test_skills_topped_up_when_too_few(self):
        items = [i for g in self.resume["skills"] for i in g["items"]]
        self.assertGreaterEqual(len(items), gen.MIN_SKILLS)
        self.assertIn("FastAPI", items)  # the posting asks for it and the resume has it

    def test_invented_employer_and_project_removed(self):
        companies = [e["company"] for e in self.resume["experience"]]
        self.assertNotIn("Initrode Systems", companies)
        self.assertEqual([p["name"] for p in self.resume["projects"]], ["Budget Tracker"])

    def test_titles_and_dates_restored(self):
        first, second = self.resume["experience"][:2]
        self.assertEqual(first["title"], "Backend Developer")
        self.assertEqual(first["dates"], "Jan 2021 - Present")
        self.assertEqual(second["title"], "Software Engineer Intern")
        self.assertIn('Restored your real job title "Backend Developer"', self.notes)

    def test_invented_numbers_removed_everywhere(self):
        bullets = " ".join(self.resume["experience"][0]["bullets"])
        self.assertNotIn("55%", bullets)
        self.assertNotIn("37%", self.resume["summary"])
        letter = " ".join(self.draft.letter["paragraphs"])
        self.assertNotIn("33%", letter)
        self.assertIn("80%", letter)          # on the resume: kept
        self.assertIn("10M users", letter)    # quoted from the posting: allowed in the letter

    def test_lost_metrics_restored_from_original(self):
        bullets = self.resume["experience"][0]["bullets"]
        self.assertIn("Designed and maintained 12 FastAPI microservices handling 40M requests per day.", bullets)
        self.assertIn("Kept your original wording", self.notes)

    def test_filler_removed(self):
        self.assertNotIn("Proven", self.resume["summary"])
        self.assertTrue(self.resume["summary"].startswith("Backend developer"))
        self.assertNotIn("I am confident", " ".join(self.draft.letter["paragraphs"]))

    def test_past_role_dates_keep_their_end(self):
        role = RESUME.roles[1]
        self.assertEqual(gen.role_dates(role, date(2026, 10, 29)), "May 2019 - Aug 2019")

    def test_education_copied_word_for_word(self):
        sections = {s["heading"]: s["items"] for s in self.resume["sections"]}
        self.assertEqual(sections["Education"][0]["text"], "B.S. Computer Science, University of Texas at Austin, 2020")

    def test_falls_back_to_ranking_model_when_writer_missing(self):
        fake = FakeOllama(installed=("qwen2.5:14b",))
        draft = gen.generate(JOB, RESUME, prefs={}, ollama=fake)
        self.assertEqual(set(fake.models_used), {"qwen2.5:14b"})
        self.assertIn("is not downloaded", draft.notes[0])
        self.assertEqual(draft.model, "qwen2.5:14b")

    def test_ollama_not_running_is_not_hidden(self):
        class Down(FakeOllama):
            def check(self, *models):
                raise OllamaError("Ollama is not running.")
        with self.assertRaisesRegex(OllamaError, "not running"):
            gen.generate(JOB, RESUME, prefs={}, ollama=Down())

    def test_find_job_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "jobs_raw.json").write_text(json.dumps({"jobs": [{**JOB, "id": "empty", "description": ""}]}))
            with self.assertRaisesRegex(gen.DocumentError, "full text was not downloaded"):
                gen.find_job("empty", d)
            with self.assertRaisesRegex(gen.DocumentError, "no longer in the saved search"):
                gen.find_job("nope", d)


def fake_pdf(files):
    pdfs = [f.with_suffix(".pdf") for f in files]
    for p in pdfs:
        p.write_bytes(b"%PDF-1.4 test")
    return pdfs


def no_pdf(files):
    raise PdfUnavailable("No PDF: Microsoft Word is not installed. The Word files are ready.")


class WriterTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.draft = gen.generate(JOB, RESUME, prefs={}, ollama=FakeOllama())

    def test_files_and_content(self):
        meta = save_documents(self.draft, JOB, "Jordan Rivera", "Austin, TX | jordan@example.com", "m",
                              self.root, make_pdf=fake_pdf)
        self.assertEqual([(f["kind"], f["format"]) for f in meta["files"]],
                         [("Resume", "DOCX"), ("Resume", "PDF"), ("Cover letter", "DOCX"), ("Cover letter", "PDF")])
        folder = folder_for(JOB, self.root)
        resume_text = "\n".join(p.text for p in Document(folder / meta["files"][0]["name"]).paragraphs)
        for expected in ("Jordan Rivera", "EXPERIENCE", "Backend Developer", "EDUCATION",
                         "University of Texas at Austin"):
            self.assertIn(expected, resume_text)
        letter_text = "\n".join(p.text for p in Document(folder / meta["files"][2]["name"]).paragraphs)
        self.assertIn("Re: Backend Engineer", letter_text)
        self.assertTrue(letter_text.rstrip().endswith("Jordan Rivera"))
        self.assertEqual(load_meta("job1", self.root)[1]["notes"], self.draft.notes)

    def test_word_missing_means_docx_only_with_reason(self):
        meta = save_documents(self.draft, JOB, "Jordan Rivera", "", "m", self.root, make_pdf=no_pdf)
        self.assertEqual({f["format"] for f in meta["files"]}, {"DOCX"})
        self.assertIn("Word is not installed", meta["pdf_note"])

    def test_safe_names(self):
        self.assertEqual(safe_name("Acme, Inc. / R&D <x>"), "Acme-Inc-R-D-x")
        self.assertTrue(folder_for({**JOB, "company": "../../evil"}, self.root).name.startswith("evil_"))


class ServiceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def wait(self, service, job_id):
        deadline = time.time() + 10
        while service.status(job_id)["state"] in ("queued", "running") and time.time() < deadline:
            time.sleep(0.02)
        return service.status(job_id)

    def make_run(self, order):
        def run(job_id, progress):
            order.append(job_id)
            progress("Writing your resume…")
            if job_id == "bad":
                raise gen.DocumentError("This job is no longer in the saved search results.")
            draft = gen.generate({**JOB, "id": job_id}, RESUME, prefs={}, ollama=FakeOllama())
            return save_documents(draft, {**JOB, "id": job_id}, "Jordan Rivera", "", "m", self.root, make_pdf=fake_pdf)
        return run

    def test_queue_runs_one_at_a_time_and_reports(self):
        order = []
        service = DocumentService(self.root, run=self.make_run(order))
        for job_id in ("a1", "bad", "a2"):
            service.start(job_id)
        done = self.wait(service, "a2")
        self.assertEqual(order, ["a1", "bad", "a2"])
        self.assertEqual(done["state"], "done")
        self.assertEqual(len(done["files"]), 4)
        self.assertTrue(done["files"][0]["url"].startswith("/api/documents/a2/"))
        failed = service.status("bad")
        self.assertEqual(failed["state"], "failed")
        self.assertIn("no longer in the saved search", failed["error"])
        self.assertEqual(set(service.all_statuses()), {"a1", "a2", "bad"})

    def test_only_listed_files_can_be_downloaded(self):
        service = DocumentService(self.root, run=self.make_run([]))
        service.start("a1")
        name = self.wait(service, "a1")["files"][0]["name"]
        self.assertIsNotNone(service.file_path("a1", name))
        self.assertIsNone(service.file_path("a1", "meta.json"))
        self.assertIsNone(service.file_path("a1", "../../secret.txt"))
        self.assertIsNone(service.file_path("zzz", name))


class DocumentApiTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        ranked = d / "ranked.json"
        ranked.write_text(json.dumps({"ranked_at": "2026-10-29T08:00:00", "jobs": [ranked_job("a1", 74)]}))
        build(ranked, d / "book.xlsx")
        self.root = d / "docs"
        run = ServiceTests.make_run(self, [])
        self.service = DocumentService(self.root, run=run)
        self.client = create_app(d / "book.xlsx", d / "pending.json", retry_every=None,
                                 documents=self.service).test_client()

    def test_generate_then_download(self):
        headers = {"X-Job-Tracker": "1"}
        self.assertEqual(self.client.post("/api/documents/a1").status_code, 403)          # needs the header
        self.assertEqual(self.client.post("/api/documents/nope", headers=headers).status_code, 404)
        self.assertIn(self.client.post("/api/documents/a1", headers=headers).get_json()["state"],
                      ("queued", "running", "done"))
        status = ServiceTests.wait(self, self.service, "a1")
        self.assertEqual(self.client.get("/api/documents").get_json()["a1"]["state"], "done")
        resume = status["files"][0]
        with self.client.get(resume["url"]) as resp:
            self.assertEqual(resp.status_code, 200)
            self.assertIn("attachment", resp.headers["Content-Disposition"])
            self.assertTrue(resp.data.startswith(b"PK"))  # a real .docx (zip)
        with self.client.get("/api/documents/a1/meta.json") as resp:
            self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
