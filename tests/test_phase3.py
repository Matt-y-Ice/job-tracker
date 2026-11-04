"""Phase 3 tests: hard filters, score parts, review validation, caching, and the full rank() flow.

A fake Ollama stands in for the real one, so these run offline and fast.
"""
import hashlib
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from src import ranker
from src.cache import Cache
from src.questionnaire import with_defaults
from src.resume_parser import parse_resume

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
_no_catalog = mock.patch.object(ranker, "load_catalog", return_value={})


def setUpModule():
    _no_catalog.start()  # never read the real config/skill_catalog.json in tests


def tearDownModule():
    _no_catalog.stop()
PREFS = with_defaults({"min_salary": 100000, "target_titles": ["Software Engineer"],
                       "must_have_skills": ["Python", "Go"], "seniority": ["mid"],
                       "deal_breakers": ["AI/ML development", "required travel", "PHP"]})


def review(score_parts=(30, 15, 15, 10, 10), hits=(), missing=("Rust",), seniority="match"):
    keys = list(ranker.RUBRIC)
    return {"breakdown": dict(zip(keys, score_parts)), "score": sum(score_parts),
            "reasons": ["Good Python match."], "missing_skills": list(missing), "red_flags": [],
            "seniority_match": seniority,
            "deal_breakers_hit": [{"deal_breaker": d, "evidence": e} for d, e in hits]}


class FakeOllama:
    """Scripted replies. `replies` maps a job title to a review dict (or 'bad' for junk)."""

    def __init__(self, replies=None, confirm=True):
        self.replies, self.confirm, self.chats = replies or {}, confirm, []

    def check(self, *models):
        pass

    def processor(self, model):
        return "GPU"

    def embed(self, model, texts):
        vectors = []
        for text in texts:
            h = hashlib.sha256(text.encode()).digest()
            vectors.append([1.0] + [b / 255 for b in h[:7]])  # deterministic, similar-ish vectors
        return vectors

    def chat_json(self, model, messages, schema, options):
        prompt = messages[-1]["content"] if len(messages) < 3 else messages[1]["content"]
        self.chats.append(prompt)
        if "main_work" in json.dumps(schema):
            return {"main_work": self.confirm, "mandatory_condition": False, "why": "test"}
        title = prompt.split("Title: ", 1)[1].split("\n", 1)[0]
        reply = self.replies.get(title, review())
        if reply == "bad":
            raise ValueError("reply was not valid JSON")
        return reply


def make_job(jid, title, bucket="main", description="Build Python and Go services.", **extra):
    job = {"id": jid, "title": title, "company": "Acme", "location": "Remote - USA",
           "remote_type": "remote", "salary_min": None, "salary_max": None, "salary_text": "",
           "description": description, "url": f"https://x/{jid}", "source": "Test",
           "date_posted": date.today().isoformat(), "date_scraped": "", "remote_us": "yes",
           "us_eligible": "yes", "location_note": "Remote, open to US: test", "bucket": bucket}
    job.update(extra)
    return job


class HardFilterTests(unittest.TestCase):
    def flagged(self, title, **extra):
        return ranker.hard_filter(make_job("1", title, **extra), PREFS)

    def test_ml_titles_flagged_but_ai_adjacent_software_kept(self):
        for title in ["Machine Learning Engineer", "ML Engineer II, 3D Perception", "AI/ML Engineer",
                      "Research Engineer, AI", "Data Scientist", "ML Systems Engineer"]:
            self.assertTrue(self.flagged(title), title)
        for title in ["AI Platform Engineer", "Software Engineer, AI & Data", "Backend Engineer",
                      "HTML Developer"]:
            self.assertFalse(self.flagged(title), title)

    def test_literal_deal_breaker_and_salary(self):
        self.assertTrue(self.flagged("Senior PHP Developer"))
        self.assertIn("below your $100,000 minimum",
                      self.flagged("Backend Engineer", salary_min=60000, salary_max=80000)[0])
        self.assertFalse(self.flagged("Backend Engineer", salary_min=90000, salary_max=130000))
        self.assertFalse(self.flagged("Backend Engineer", salary_min=60000))  # no max: not flagged

    def test_excluded_bucket_flagged_with_location_reason(self):
        reasons = ranker.hard_filter(make_job("1", "Dev", bucket="excluded"), PREFS)
        self.assertIn("Not remote US", reasons[0])


class ScorePartTests(unittest.TestCase):
    def test_salary_score(self):
        self.assertEqual(ranker.salary_score({"salary_max": None}, 100000), 50.0)
        self.assertEqual(ranker.salary_score({"salary_max": 125000}, 100000), 100.0)
        self.assertEqual(ranker.salary_score({"salary_max": 100000}, 100000), 60.0)
        self.assertEqual(ranker.salary_score({"salary_max": 150000}, 0), 50.0)

    def test_keyword_score(self):
        resume_skills = ["Python", "Docker"]
        job = make_job("1", "Backend Engineer", description="Python, Docker, Kubernetes.")
        score, matched = ranker.keyword_score(job, resume_skills, ["Python", "Go"])
        self.assertEqual(matched, ["Python"])
        self.assertAlmostEqual(score, (0.5 * 2 / 3 + 0.5 * 1 / 2) * 100, places=1)

    def test_level_penalty_follows_wanted_levels(self):
        wanted = ["entry", "mid"]
        cases = {"Principal Python Developer": 20, "Staff Platform Engineer": 20,
                 "Senior Backend Engineer": 10, "Sr. Python Developer": 10, "Software Engineer III": 10,
                 "Software Engineer II, Backend": 0, "Software Engineer 1 (Data)": 0,
                 "Software Engineer, Early Career": 0, "Backend Engineer": 0,
                 "Software Engineer Intern": 5, "Engineering Manager": 20}
        for title, points in cases.items():
            self.assertEqual(ranker.level_penalty(title, wanted, None)[0], points, title)
        self.assertEqual(ranker.level_penalty("Backend Engineer", wanted, "over")[0], 5)
        self.assertEqual(ranker.level_penalty("Staff Engineer", ["senior", "lead"], None)[0], 0)
        self.assertEqual(ranker.level_penalty("Staff Engineer", [], None)[0], 0)

    def test_two_must_haves_is_full_coverage(self):
        job = make_job("1", "Dev", description="Python and Java services.")
        _, matched = ranker.keyword_score(job, [], ["Python", "Java", "C#"])
        self.assertEqual(ranker.keyword_score(job, ["Python", "Java"], ["Python", "Java", "C#"])[0], 100.0)
        self.assertEqual(matched, ["Python", "Java"])

    def test_final_score_skips_missing_parts(self):
        weights = {"llm": 0.5, "embedding": 0.25, "salary": 0.1, "keywords": 0.15}
        self.assertEqual(ranker.final_score({"llm": 80, "embedding": 60, "salary": 50, "keywords": 40},
                                            weights), 66)
        self.assertEqual(ranker.final_score({"llm": None, "embedding": 60, "salary": 50, "keywords": 40},
                                            weights), 52)

    def test_similarity_mapping_is_clamped(self):
        self.assertEqual(ranker.similarity_score(0.50, (0.55, 0.75)), 0.0)
        self.assertEqual(ranker.similarity_score(0.65, (0.55, 0.75)), 50.0)
        self.assertEqual(ranker.similarity_score(0.90, (0.55, 0.75)), 100.0)


class ValidationTests(unittest.TestCase):
    POSTING = "Backend Engineer\nYou will train and deploy ranking models. Must travel quarterly."

    def test_clamps_negative_parts_and_sums(self):
        data = review(score_parts=(30, 15, 15, 10, -5))
        cleaned = ranker.validate_review(data, PREFS["deal_breakers"], self.POSTING)
        self.assertEqual((cleaned["breakdown"]["constraints"], cleaned["score"]), (0, 70))

    def test_rejects_bad_seniority_and_far_out_values(self):
        with self.assertRaises(ValueError):
            ranker.validate_review(review(seniority="senior"), [], "")
        with self.assertRaises(ValueError):
            ranker.validate_review(review(score_parts=(300, 15, 15, 10, 10)), [], "")

    def test_deal_breaker_claims_need_a_real_on_topic_quote(self):
        hits = [("AI/ML development", "train and deploy ranking models"),        # real, on topic
                ("required travel", "train and deploy ranking models"),          # real, off topic
                ("AI/ML development", "builds large language models every day"),  # not in posting
                ("remote work", "Must travel quarterly")]                        # not a user deal breaker
        cleaned = ranker.validate_review(review(hits=hits), PREFS["deal_breakers"], self.POSTING)
        self.assertEqual(cleaned["deal_breakers_hit"],
                         [("AI/ML development", "train and deploy ranking models")])

    def test_missing_skills_on_resume_are_dropped(self):
        resume = parse_resume(SAMPLES / "sample_resume.txt")
        cleaned = ranker.validate_review(review(missing=("Python", "Rust", "kubernetes")), [], "", resume)
        self.assertEqual(cleaned["missing_skills"], ["Rust"])


class LlmReviewTests(unittest.TestCase):
    SETTINGS = {"model": "m", "max_description_chars": 6000, "temperature": 0.1}

    def test_retries_once_then_gives_up(self):
        fake = FakeOllama({"Dev": "bad"})
        self.assertIsNone(ranker.llm_review(fake, self.SETTINGS, "sys", make_job("1", "Dev"), []))
        self.assertEqual(len(fake.chats), 2)

    def test_unconfirmed_deal_breaker_becomes_red_flag(self):
        job = make_job("1", "Dev", description="Use AI-assisted development workflows daily.")
        hit = [("AI/ML development", "Use AI-assisted development workflows daily")]
        result = ranker.llm_review(FakeOllama({"Dev": review(hits=hit)}, confirm=False),
                                   self.SETTINGS, "sys", job, ["AI/ML development"])
        self.assertEqual(result["deal_breakers_hit"], [])
        self.assertIn("not as a main duty", result["red_flags"][0])

    def test_confirmed_deal_breaker_is_kept(self):
        job = make_job("1", "Dev", description="Must travel quarterly to the office.")
        hit = [("required travel", "Must travel quarterly to the office")]
        result = ranker.llm_review(FakeOllama({"Dev": review(hits=hit)}), self.SETTINGS, "sys", job,
                                   ["required travel"])
        self.assertTrue(result["deal_breakers_hit"][0].startswith("required travel: "))


class RankFlowTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        files = {"RAW_FILE": "jobs_raw.json", "UNSURE_FILE": "jobs_unsure.json",
                 "EXCLUDED_FILE": "jobs_excluded.json", "RANKED_FILE": "jobs_ranked.json"}
        for attr, name in files.items():
            patcher = mock.patch.object(ranker, attr, self.dir / name)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.write("jobs_raw.json", [make_job("a", "Backend Engineer", salary_max=150000),
                                     make_job("b", "Machine Learning Engineer"),
                                     make_job("c", "Python Developer", salary_max=70000),
                                     make_job("d", "Software Engineer")])
        self.write("jobs_unsure.json", [make_job("u", "Platform Engineer", remote_us="unsure")])
        self.write("jobs_excluded.json", [make_job("x", "Software Engineer", location="Remote Poland")])
        self.resume = parse_resume(SAMPLES / "sample_resume.txt")

    def write(self, name, jobs):
        (self.dir / name).write_text(json.dumps({"jobs": jobs}), encoding="utf-8")

    def run_rank(self, fake, prefs=PREFS, **kwargs):
        cache = Cache(self.dir / "cache.db")
        return ranker.rank(self.resume, prefs, report=lambda line: None, progress_bar=False,
                           cache=cache, ollama=fake, **kwargs)

    def test_full_flow(self):
        fake = FakeOllama({"Software Engineer": "bad"})
        result = self.run_rank(fake)
        jobs = {j["id"]: j for j in result.jobs}
        self.assertEqual(jobs["b"]["score"], 0)
        self.assertIn("Deal breaker in title", jobs["b"]["flag_reasons"][0])
        self.assertIn("below your", jobs["c"]["flag_reasons"][0])
        self.assertEqual(jobs["x"]["score"], 0)
        self.assertEqual(jobs["x"]["llm_status"], "skipped (flagged)")
        self.assertEqual(jobs["a"]["llm_score"], 80)
        self.assertGreater(jobs["a"]["score"], 0)
        self.assertEqual(jobs["a"]["level_penalty"], 0)
        self.assertEqual(jobs["u"]["bucket"], "unsure")
        self.assertEqual(jobs["u"]["llm_status"], "ok")                 # unsure jobs are LLM-ranked
        self.assertIsNone(jobs["d"]["llm_score"])                        # LLM failed twice
        self.assertGreater(jobs["d"]["score"], 0)                        # still scored from other parts
        self.assertEqual((result.new, result.cached, result.failed), (3, 0, 1))
        saved = json.loads((self.dir / "jobs_ranked.json").read_text(encoding="utf-8"))
        self.assertEqual([j["bucket"] for j in saved["jobs"]][-1], "excluded")
        self.assertEqual(saved["counts"]["llm_failed"], 1)

    def test_second_run_uses_cache_and_retries_failures(self):
        self.run_rank(FakeOllama({"Software Engineer": "bad"}))
        fake = FakeOllama()
        result = self.run_rank(fake)
        self.assertEqual((result.new, result.cached), (1, 2))   # only the failed job is redone
        self.assertEqual(len(fake.chats), 1)

    def test_preference_change_rescores_and_says_why(self):
        self.run_rank(FakeOllama())
        notes = []
        cache = Cache(self.dir / "cache.db")
        changed = with_defaults({**PREFS, "must_have_skills": ["Rust"]})
        result = ranker.rank(self.resume, changed, report=notes.append, progress_bar=False,
                             cache=cache, ollama=FakeOllama())
        self.assertEqual(result.new, 3)
        self.assertTrue(any("preferences changed" in n for n in notes))

    def test_weights_change_does_not_rescore(self):
        self.run_rank(FakeOllama())
        reweighted = with_defaults({**PREFS, "score_weights": {"llm": 1.0, "embedding": 0,
                                                               "salary": 0, "keywords": 0}})
        result = self.run_rank(FakeOllama(), prefs=reweighted)
        self.assertEqual(result.new, 0)
        top = next(j for j in result.jobs if j["id"] == "a")
        self.assertEqual(top["score"], 80)  # final score now equals the LLM score

    def test_rescore_all_and_limit(self):
        self.run_rank(FakeOllama())
        result = self.run_rank(FakeOllama(), rescore_all=True, limit=2)
        self.assertEqual(result.new, 2)


if __name__ == "__main__":
    unittest.main()
