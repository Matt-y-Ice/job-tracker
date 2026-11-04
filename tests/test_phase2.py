"""Phase 2 tests: source parsers, salary/remote helpers, de-duplication, and the runner.

These run offline with canned responses shaped like the real APIs.
"""
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from src.scrapers import base, discover, runner
from src.scrapers.ashby import Ashby
from src.scrapers.base import (Context, SourceError, html_to_text, infer_remote_type, make_job,
                               parse_salary, title_matcher)
from src.scrapers.dedupe import dedupe
from src.scrapers.greenhouse import Greenhouse
from src.scrapers.himalayas import Himalayas
from src.scrapers.lever import Lever
from src.scrapers.remoteok import RemoteOK
from src.scrapers.weworkremotely import WeWorkRemotely

SCHEMA = ["id", "title", "company", "location", "remote_type", "salary_min", "salary_max",
          "salary_text", "description", "url", "source", "date_posted", "date_scraped",
          "remote_us", "us_eligible", "location_note", "_mode_hint"]


class FakeHttp:
    """Returns canned payloads by URL prefix (longest prefix wins); raises SourceError for `fail`."""

    def __init__(self, routes: dict, fail: tuple = ()):
        self.routes, self.fail, self.calls = routes, fail, []

    def _find(self, url, params=None, headers=None):
        self.calls.append((url, params or {}, headers or {}))
        if any(f in url for f in self.fail):
            raise SourceError("HTTP 404 from test")
        return next(v for k, v in sorted(self.routes.items(), key=lambda kv: -len(kv[0]))
                    if url.startswith(k))

    def json(self, url, headers=None, **params):
        return self._find(url, params, headers)

    def post_json(self, url, payload, headers=None):
        return self._find(url, payload, headers)

    def text(self, url, **params):
        return self._find(url, params)


def ctx(routes, settings=None, keywords=("software engineer",), fail=(), **extra):
    return Context(http=FakeHttp(routes, fail), settings=settings or {},
                   keywords=list(keywords), wants_title=title_matcher(list(keywords)), **extra)


class HelperTests(unittest.TestCase):
    def test_salary_formats(self):
        cases = {
            "Pay: $120,000 - $150,000 per year": (120000, 150000),
            "$120k-$150k": (120000, 150000),
            "$120k-150": (120000, 150000),
            "$50 - $65 per hour": (104000, 135200),
            "$8,000 to $10,000 per month": (96000, 120000),
            "We paid out $11M to engineers": (None, None),
            "New hire equity: $32,000-$48,000. Base: $140,000 - $180,000": (140000, 180000),
            "Sign-on bonus $5,000 - $10,000": (None, None),
        }
        for text, expected in cases.items():
            self.assertEqual(parse_salary(text), expected, text)

    def test_bad_structured_salary_dropped(self):
        job = make_job(title="Dev", company="X", url="https://x.io/1", source="T",
                       salary_min=2200, salary_max=2200)
        self.assertIsNone(job["salary_min"])

    def test_remote_type(self):
        self.assertEqual(infer_remote_type("Remote - USA"), "remote")
        self.assertEqual(infer_remote_type("New York, NY - Hybrid"), "hybrid")
        self.assertEqual(infer_remote_type("Berlin", default="onsite"), "onsite")

    def test_title_matcher(self):
        wants = title_matcher(["software engineer", "backend developer"])
        self.assertTrue(wants("Senior Software Engineer II"))
        self.assertTrue(wants("Engineer, Software (Platform)"))
        self.assertTrue(wants("Back-End Developer"))
        self.assertFalse(wants("Sales Engineer"))
        self.assertTrue(title_matcher([])("Anything"))

    def test_html_to_text_handles_double_escaping(self):
        text = html_to_text("&lt;p&gt;Hello&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;")
        self.assertEqual(text, "Hello\n\n- Python")

    def test_id_ignores_tracking_params(self):
        a = make_job(title="Dev", company="X", url="https://x.io/j/1?utm_source=api", source="T")
        b = make_job(title="Dev", company="X", url="https://x.io/j/1", source="T")
        self.assertEqual(a["id"], b["id"])

    def test_every_source_registers(self):
        self.assertEqual(set(discover()), {
            "remoteok", "remotive", "weworkremotely", "arbeitnow", "himalayas", "jobicy", "greenhouse",
            "lever", "ashby", "themuse", "jobyap", "parlel", "aidevjobs", "adzuna", "usajobs",
            "jooble", "findwork"})

    def test_fix_text_repairs_mojibake(self):
        from src.scrapers.base import fix_text
        self.assertEqual(fix_text("Sothebyâ\u0080\u0099s"), "Sotheby’s")
        self.assertEqual(fix_text("São Paulo"), "São Paulo")  # already fine: unchanged


class ParserTests(unittest.TestCase):
    def check_schema(self, jobs):
        self.assertTrue(jobs)
        for job in jobs:
            self.assertEqual(list(job), SCHEMA)

    def test_remoteok(self):
        payload = [{"legal": "..."},
                   {"slug": "a", "position": "Software Engineer", "company": "Acme", "epoch": 1793001612,
                    "description": "<p>Python</p>", "location": "", "salary_min": 100000,
                    "salary_max": 140000, "url": "https://remoteOK.com/remote-jobs/a"},
                   {"slug": "b", "position": "Account Executive", "company": "Acme"}]
        jobs = list(RemoteOK().fetch(ctx({"https://remoteok.com": payload})))
        self.check_schema(jobs)
        self.assertEqual(len(jobs), 1)
        self.assertEqual((jobs[0]["salary_text"], jobs[0]["date_posted"]), ("$100K-$140K", "2026-10-26"))

    def test_wwr_rss(self):
        rss = """<?xml version="1.0"?><rss><channel><item>
          <title>Lithic: Senior Software Engineer, Processing</title><region>Anywhere in the World</region>
          <link>https://weworkremotely.com/remote-jobs/lithic-x</link>
          <pubDate>Mon, 27 Oct 2026 10:00:00 +0000</pubDate>
          <description>&lt;p&gt;Salary: $150,000 - $190,000&lt;/p&gt;</description></item></channel></rss>"""
        jobs = list(WeWorkRemotely().fetch(ctx({"https://weworkremotely.com": rss},
                                               {"feeds": ["remote-programming-jobs"]})))
        self.check_schema(jobs)
        self.assertEqual((jobs[0]["company"], jobs[0]["salary_min"]), ("Lithic", 150000))

    def test_himalayas_monthly_salary(self):
        item = {"title": "Software Engineer", "companyName": "X", "minSalary": 8000, "maxSalary": 10000,
                "salaryPeriod": "monthly", "currency": "USD", "locationRestrictions": ["United States"],
                "pubDate": 1791008150, "guid": "https://himalayas.app/companies/x/jobs/se",
                "description": "<p>Hi</p>"}
        jobs = list(Himalayas().fetch(ctx({"https://himalayas.app": {"jobs": [item]}})))
        self.assertEqual((jobs[0]["salary_min"], jobs[0]["salary_max"]), (96000, 120000))
        self.assertEqual(jobs[0]["location"], "Remote (United States)")

    def test_greenhouse_onsite_detection_and_board_failure(self):
        payload = {"jobs": [
            {"title": "Software Engineer", "location": {"name": "Remote US"}, "absolute_url": "https://g/1",
             "content": "&lt;p&gt;Base pay $133,000 - $183,000&lt;/p&gt;", "first_published": "2026-10-20T00:00:00-04:00"},
            {"title": "Software Engineer", "location": {"name": "San Francisco, CA"}, "absolute_url": "https://g/2",
             "content": ""}]}
        source = Greenhouse()
        c = ctx({"https://boards-api.greenhouse.io/v1/boards/affirm": payload},
                {"companies": {"affirm": "Affirm", "gone": "Gone Inc"}}, fail=("/gone/",))
        jobs = list(source.fetch(c))
        self.check_schema(jobs)
        self.assertEqual([j["remote_type"] for j in jobs], ["remote", "onsite"])
        self.assertEqual(jobs[0]["salary_max"], 183000)
        self.assertIn("1 of 2 boards failed: gone", source.warnings[0])

    def test_all_boards_failing_raises(self):
        with self.assertRaises(SourceError):
            list(Greenhouse().fetch(ctx({}, {"companies": ["a", "b"]}, fail=("boards",))))

    def test_lever_workplace_and_salary(self):
        payload = [{"text": "Software Engineer", "categories": {"location": "United States"},
                    "workplaceType": "remote", "hostedUrl": "https://jobs.lever.co/x/1",
                    "createdAt": 1790106601403, "descriptionPlain": "Intro",
                    "lists": [{"text": "Requirements", "content": "<li>Python</li>"}],
                    "salaryRange": {"currency": "USD", "interval": "per-year-salary", "min": 120000, "max": 150000}}]
        jobs = list(Lever().fetch(ctx({"https://api.lever.co": payload}, {"companies": {"x": "X Co"}})))
        self.assertEqual((jobs[0]["remote_type"], jobs[0]["salary_min"]), ("remote", 120000))
        self.assertIn("Requirements\n- Python", jobs[0]["description"])

    def test_ashby_compensation(self):
        payload = {"jobs": [{"title": "Software Engineer", "location": "Remote, US", "isListed": True,
                             "workplaceType": "Remote", "jobUrl": "https://jobs.ashbyhq.com/x/1",
                             "descriptionHtml": "<p>Hi</p>", "publishedAt": "2026-10-01T00:00:00Z",
                             "compensation": {"compensationTierSummary": "$150K - $200K",
                                              "summaryComponents": [{"compensationType": "Salary",
                                                                     "interval": "1 YEAR", "currencyCode": "USD",
                                                                     "minValue": 150000, "maxValue": 200000}]}}]}
        jobs = list(Ashby().fetch(ctx({"https://api.ashbyhq.com": payload}, {"companies": {"x": "X"}})))
        self.assertEqual((jobs[0]["salary_min"], jobs[0]["salary_text"]), (150000, "$150K - $200K"))


def job(title, company, source, url=None, **extra):
    return make_job(title=title, company=company, source=source,
                    url=url or f"https://{source.lower()}.example/{title}/{company}", **extra)


class DedupeTests(unittest.TestCase):
    def test_cross_source_duplicate_merged_and_direct_link_kept(self):
        jobs = [job("Senior Software Engineer (Remote)", "Lithic Inc.", "WWR", salary_text="$150k-$190k"),
                job("Senior Software Engineer", "Lithic", "Greenhouse")]
        kept, removed = dedupe(jobs)
        self.assertEqual((len(kept), removed), (1, 1))
        self.assertEqual(kept[0]["source"], "Greenhouse")
        self.assertEqual(kept[0]["salary_min"], 150000)  # filled in from the duplicate

    def test_different_levels_not_merged(self):
        kept, _ = dedupe([job("Software Engineer II", "Affirm", "Greenhouse"),
                          job("Software Engineer III", "Affirm", "Himalayas")])
        self.assertEqual(len(kept), 2)

    def test_same_source_postings_not_merged(self):
        kept, _ = dedupe([job("Software Engineer", "Affirm", "Greenhouse", url="https://g/1"),
                          job("Software Engineer", "Affirm", "Greenhouse", url="https://g/2")])
        self.assertEqual(len(kept), 2)

    def test_similar_company_names_not_merged(self):
        kept, _ = dedupe([job("Software Engineer", "Meta", "Jobicy"),
                          job("Software Engineer", "Metabase", "Himalayas")])
        self.assertEqual(len(kept), 2)

    def test_exact_id_duplicate(self):
        a = job("Dev", "X", "RemoteOK", url="https://x/1")
        kept, removed = dedupe([a, dict(a)])
        self.assertEqual((len(kept), removed), (1, 1))


class GoodSource(base.Source):
    label = "Good"

    def fetch(self, c):
        today = date.today().isoformat()
        yield job("Software Engineer", "A", "Good", location="Remote - USA", date_posted=today)
        yield job("Software Engineer", "B", "Good", location="Remote - USA", date_posted="2020-01-01")  # old
        yield job("Software Engineer", "C", "Good", location="Austin, TX")             # onsite: excluded
        yield job("Software Engineer", "E", "Good", location="Remote")                 # no US clue: unsure
        yield job("Software Engineer", "F", "Good", location="Remote (Poland)")        # excluded
        yield job("Sales Manager", "D", "Good", location="Remote - USA")               # wrong title


class BrokenSource(base.Source):
    label = "Broken"

    def fetch(self, c):
        raise RuntimeError("boom")
        yield


class RunnerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        for target, value in [("RAW_FILE", self.dir / "jobs_raw.json"),
                              ("UNSURE_FILE", self.dir / "jobs_unsure.json"),
                              ("EXCLUDED_FILE", self.dir / "jobs_excluded.json"),
                              ("API_KEYS_FILE", self.dir / "api_keys.json"),
                              ("SOURCE_CACHE_DIR", self.dir / "sources")]:
            patcher = mock.patch.object(runner, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        GoodSource.key, BrokenSource.key = "good", "broken"
        sources = [(GoodSource(), {}), (BrokenSource(), {})]
        self.prefs = {"days_back": 7, "remote_preference": "remote_only"}
        for target, value in [("enabled_sources", lambda cfg, keys: sources),
                              ("load_settings", lambda: ({"keywords": ["software engineer"]}, self.prefs))]:
            patcher = mock.patch.object(runner, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def read(self, name):
        return json.loads((self.dir / name).read_text(encoding="utf-8"))

    def test_filters_buckets_and_failure_isolation(self):
        lines = []
        outcome = runner.scrape(report=lines.append)
        self.assertEqual([j["company"] for j in outcome.jobs], ["A"])
        self.assertEqual([j["company"] for j in outcome.unsure], ["E"])
        self.assertEqual(sorted(j["company"] for j in outcome.excluded), ["C", "F"])
        self.assertTrue(any("Broken: FAILED (unexpected error: RuntimeError: boom)" in l for l in lines))
        saved = self.read("jobs_raw.json")
        self.assertEqual((saved["total"], saved["unsure"], saved["excluded"]), (1, 1, 2))
        self.assertEqual(saved["sources"]["Broken"]["count"], 0)
        self.assertNotIn("_mode_hint", saved["jobs"][0])
        self.assertEqual(saved["jobs"][0]["remote_us"], "yes")
        self.assertIn("outside the US", self.read("jobs_excluded.json")["jobs"][0]["location_note"]
                      + self.read("jobs_excluded.json")["jobs"][1]["location_note"])
        self.assertEqual(self.read("jobs_unsure.json")["total"], 1)
        self.assertIn("Good 4, Broken failed", runner.summary_line(outcome))

    def test_hybrid_preference_moves_onsite_still_excluded(self):
        self.prefs["remote_preference"] = "any"
        outcome = runner.scrape(report=lambda line: None)
        self.assertEqual(sorted(j["company"] for j in outcome.jobs), ["A", "C"])

    def test_key_source_without_key_reports_clearly(self):
        class KeySource(base.Source):
            label, needs_key = "Keyed", True

            def fetch(self, c):
                raise AssertionError("must not fetch without a key")
        KeySource.key = "keyed"
        result = runner.run_source(KeySource(), {}, {}, ["software engineer"], api_key={"api_key": ""})
        self.assertIn("needs a free API key", result.error)

    def test_min_refresh_reuses_cache(self):
        source = GoodSource()
        source.min_refresh_hours = 6
        first = runner.run_source(source, {}, {}, ["software engineer"])
        with mock.patch.object(GoodSource, "fetch", side_effect=AssertionError("should not refetch")):
            second = runner.run_source(source, {}, {}, ["software engineer"])
        self.assertTrue(second.cached_at)
        self.assertEqual(len(second.jobs), len(first.jobs))


class EnableTests(unittest.TestCase):
    def test_key_sources_turn_on_only_when_key_filled(self):
        from src.scrapers.adzuna import Adzuna
        from src.scrapers.aidevjobs import AIDevJobs
        registry = {"adzuna": Adzuna, "aidevjobs": AIDevJobs, "remoteok": RemoteOK}
        with mock.patch.object(runner, "discover", return_value=registry):
            keys = lambda cfg, k: [s.key for s, _ in runner.enabled_sources(cfg, k)]
            self.assertEqual(keys({}, {"adzuna": {"app_id": "x", "app_key": ""}}), ["remoteok"])
            self.assertEqual(keys({}, {"adzuna": {"app_id": "x", "app_key": "y"}}), ["adzuna", "remoteok"])
            self.assertEqual(keys({"sources": {"aidevjobs": {"enabled": True}}}, {}), ["aidevjobs", "remoteok"])


class LimiterTests(unittest.TestCase):
    def test_same_host_requests_are_spaced(self):
        limiter = base._HostLimiter()
        with mock.patch.object(base.time, "sleep") as sleep:
            limiter.wait("h", 1.5)
            limiter.wait("h", 1.5)
            limiter.wait("other", 1.5)
        self.assertEqual(sleep.call_count, 1)
        self.assertAlmostEqual(sleep.call_args[0][0], 1.5, places=1)


if __name__ == "__main__":
    unittest.main()
