"""Phase 1 tests: resume parsing and the questionnaire.

Run from the project folder:  .venv\\Scripts\\python -m unittest discover tests
"""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from unittest import mock

from src import questionnaire as q
from src.resume_parser import (ResumeError, extract_roles, find_resume, heading_key,
                               parse_resume, total_years)
from src.skills_vocab import find_skills

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
TODAY = date(2026, 10, 29)


def scripted(*answers):
    """Input function that replays answers, then fails like a closed console."""
    queue = list(answers)

    def ask(prompt):
        print(prompt, end="")  # like input()
        if not queue:
            raise EOFError
        return queue.pop(0)
    return ask


class FindResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_folder(self):
        with self.assertRaisesRegex(ResumeError, "No resume found"):
            find_resume(self.dir)

    def test_two_resumes(self):
        (self.dir / "a.pdf").write_bytes(b"x")
        (self.dir / "b.docx").write_bytes(b"x")
        with self.assertRaisesRegex(ResumeError, "Found 2 resumes"):
            find_resume(self.dir)

    def test_ignores_word_lock_files_and_dotfiles(self):
        (self.dir / "resume.docx").write_bytes(b"x")
        (self.dir / "~$resume.docx").write_bytes(b"x")
        (self.dir / ".gitkeep").write_bytes(b"")
        self.assertEqual(find_resume(self.dir).name, "resume.docx")

    def test_unsupported_type_is_named(self):
        (self.dir / "resume.doc").write_bytes(b"x")
        with self.assertRaisesRegex(ResumeError, "resume.doc.*only PDF, DOCX, and TXT"):
            find_resume(self.dir)

    def test_missing_folder(self):
        with self.assertRaisesRegex(ResumeError, "folder is missing"):
            find_resume(self.dir / "nope")

    def test_scanned_pdf_style_empty_text(self):
        (self.dir / "r.txt").write_text("Jane Doe\n", encoding="utf-8")
        with self.assertRaisesRegex(ResumeError, "almost no readable text"):
            parse_resume(self.dir / "r.txt", TODAY)


class ParseSampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.info = parse_resume(SAMPLES / "sample_resume.txt", TODAY)

    def test_roles_and_titles(self):
        titles = [r.title for r in self.info.roles]
        self.assertEqual(titles, ["Backend Developer", "Software Engineer Intern",
                                  "IT Support Technician"])
        self.assertEqual(self.info.titles[0], "Backend Developer")

    def test_date_formats(self):
        spans = [(r.start, r.end) for r in self.info.roles]
        self.assertEqual(spans, [("2021-01", "2026-10"), ("2019-05", "2019-08"),
                                 ("2016-01", "2018-01")])

    def test_years(self):
        self.assertAlmostEqual(self.info.years_experience, 8.2)
        self.assertEqual(self.info.stated_years, 5)

    def test_skills(self):
        for skill in ["Python", "Go", "FastAPI", "PostgreSQL", "Kubernetes", "TDD", "Airflow"]:
            self.assertIn(skill, self.info.skills)
        self.assertNotIn("Java", self.info.skills)  # "JavaScript" absent, Java not mentioned

    def test_hash_is_stable(self):
        again = parse_resume(SAMPLES / "sample_resume.txt", TODAY)
        self.assertEqual(self.info.text_hash, again.text_hash)


class DocxTests(unittest.TestCase):
    def test_docx_with_table_layout(self):
        from docx import Document
        doc = Document()
        doc.add_paragraph("Sam Lee")
        doc.add_paragraph("Data Engineer")
        doc.add_paragraph("EXPERIENCE")
        table = doc.add_table(rows=1, cols=2)
        table.cell(0, 0).text = "Data Engineer, Acme"
        table.cell(0, 1).text = "March 2020 - Present"
        doc.add_paragraph("- Built Spark and Airflow pipelines on AWS feeding Snowflake.")
        doc.add_paragraph("EDUCATION")
        doc.add_paragraph("B.S. Statistics, 2015 - 2019")
        doc.add_paragraph("Filler text so the resume is long enough to count as real. " * 4)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "r.docx"
            doc.save(path)
            info = parse_resume(path, TODAY)
        self.assertEqual([r.title for r in info.roles], ["Data Engineer"])
        self.assertEqual(info.roles[0].start, "2020-03")  # education years ignored
        self.assertIn("Snowflake", info.skills)


class HelperTests(unittest.TestCase):
    def test_wrapped_bullet_is_not_heading(self):
        self.assertIsNone(heading_key("projects."))
        self.assertEqual(heading_key("PROJECTS"), "projects")
        self.assertEqual(heading_key("Work Experience:"), "work experience")

    def test_overlapping_roles_not_double_counted(self):
        lines = ["Engineer, A  Jan 2020 - Dec 2021", "Tutor, B  Jun 2021 - Dec 2021"]
        roles = extract_roles(lines, TODAY)
        self.assertEqual(total_years(roles), 2.0)

    def test_future_end_clamped_to_today(self):
        roles = extract_roles(["Engineer Co-op June 2026 to November 2026"], TODAY)
        self.assertEqual(roles[0].end, "2026-10")

    def test_ambiguous_words_not_skills(self):
        text = "Take a rest, excel at work, spark joy, go to the node."
        self.assertEqual(find_skills(text), [])

    def test_language_edge_cases(self):
        found = find_skills("Skills: C, C++, C#, Go, R, Java, JavaScript, .NET, Node.js")
        for skill in ["C", "C++", "C#", "Go", "R", "Java", "JavaScript", ".NET", "Node.js"]:
            self.assertIn(skill, found)


class QuestionnaireTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / "preferences.json"
        patcher = mock.patch.object(q, "PREFERENCES_FILE", self.file)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def run_q(self, *answers, **kwargs):
        with redirect_stdout(io.StringIO()) as out:
            result = q.load_or_ask(scripted(*answers), interactive=True, **kwargs)
        return result, out.getvalue()

    FIRST_RUN = ["abc", "95k", "2", "United States, EST", "", "Python, SQL",
                 "AI/ML development, PHP", "9", "2,3", "5,1", "200", "21"]

    def test_first_run_saves_answers(self):
        (prefs, how), out = self.run_q(*self.FIRST_RUN, resume_titles=["Software Engineer"])
        self.assertEqual(how, "new")
        saved = json.loads(self.file.read_text(encoding="utf-8"))
        self.assertEqual(saved["min_salary"], 95000)
        self.assertEqual(saved["remote_preference"], "remote_or_hybrid")
        self.assertEqual(saved["locations"], ["United States", "EST"])
        self.assertEqual(saved["target_titles"], ["Software Engineer"])  # from resume
        self.assertEqual(saved["deal_breakers"], ["AI/ML development", "PHP"])
        self.assertEqual(saved["seniority"], ["entry", "mid"])
        self.assertEqual(saved["company_size"], ["any"])  # "any" wins over others
        self.assertEqual(saved["days_back"], 21)
        self.assertEqual(saved["score_weights"], q.DEFAULT_WEIGHTS)
        self.assertIn("Please type a number like 90000", out)  # "abc" rejected
        self.assertIn("from 1 to 6", out)                      # "9" rejected
        self.assertIn("from 1 to 90", out)                     # "200" rejected

    def test_returning_user_keeps_answers(self):
        self.run_q(*self.FIRST_RUN)
        (prefs, how), out = self.run_q("")
        self.assertEqual(how, "saved")
        self.assertIn("$95,000", out)
        self.assertIn("Use these again", out)

    def test_returning_user_edits_one_answer(self):
        self.run_q(*self.FIRST_RUN)
        prefs = json.loads(self.file.read_text(encoding="utf-8"))
        prefs["score_weights"]["llm"] = 0.7  # user tuned a weight by hand
        self.file.write_text(json.dumps(prefs), encoding="utf-8")
        (prefs, how), _ = self.run_q("n", "", "", "", "", "-", "", "", "", "30")
        self.assertEqual(how, "updated")
        self.assertEqual(prefs["min_salary"], 95000)
        self.assertEqual(prefs["must_have_skills"], [])
        self.assertEqual(prefs["days_back"], 30)
        self.assertEqual(prefs["score_weights"]["llm"], 0.7)

    def test_reset_asks_again(self):
        self.run_q(*self.FIRST_RUN)
        (_, how), out = self.run_q(*self.FIRST_RUN[1:], reset=True)
        self.assertEqual(how, "new")
        self.assertNotIn("Use these again", out)

    def test_non_interactive_uses_saved(self):
        self.run_q(*self.FIRST_RUN)
        prefs, how = q.load_or_ask(scripted(), interactive=False)
        self.assertEqual((how, prefs["min_salary"]), ("saved", 95000))

    def test_non_interactive_without_saved_fails_clearly(self):
        with self.assertRaisesRegex(q.QuestionnaireAborted, "No saved preferences"):
            q.load_or_ask(scripted(), interactive=False)

    def test_closed_input_saves_nothing(self):
        with self.assertRaises(q.QuestionnaireAborted):
            self.run_q("95k")
        self.assertFalse(self.file.exists())

    def test_parse_salary(self):
        cases = {"100k": 100000, "$95,000": 95000, "120000": 120000, "95": 95000,
                 "87.5k": 87500, "lots": None}
        for text, expected in cases.items():
            self.assertEqual(q.parse_salary(text), expected, text)


if __name__ == "__main__":
    unittest.main()
