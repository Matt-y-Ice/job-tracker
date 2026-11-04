"""Remote-US classifier tests, plus parsers for the sources added from publicapis.dev.

Classifier cases come from real postings seen during live testing.
"""
import unittest
from datetime import date

from src.scrapers.adzuna import Adzuna
from src.scrapers.aidevjobs import AIDevJobs
from src.scrapers.findwork import Findwork
from src.scrapers.jobyap import JobYap
from src.scrapers.jooble import Jooble
from src.scrapers.location import bucket, classify
from src.scrapers.parlel import Parlel
from src.scrapers.themuse import TheMuse
from src.scrapers.usajobs import USAJobs
from tests.test_phase2 import ctx

TODAY = date.today().isoformat()


class ClassifierTests(unittest.TestCase):
    CASES = [
        # (location, description, remote_board, mode_hint) -> (remote_us, work_mode, us_eligible)
        (("Remote - USA", "E-Verify. 401(k).", False, None), ("yes", "remote", "yes")),
        (("Remote Poland", "Pay in PLN.", False, None), ("no", "remote", "no")),
        (("Remote (Egypt)", "", True, None), ("no", "remote", "no")),
        (("Remote (Europe)", "EEO employer.", True, None), ("no", "remote", "no")),
        (("Anywhere in the World", "Great team.", True, None), ("yes", "remote", "yes")),
        (("Anywhere in the World", "Must overlap with CET hours.", True, None), ("unsure", "remote", "unsure")),
        (("Remote", "", True, None), ("unsure", "remote", "unsure")),
        (("Remote", "You must reside in the United States.", True, None), ("yes", "remote", "yes")),
        (("Remote", "Candidates must be based in Europe.", True, None), ("no", "remote", "no")),
        (("Remote - US & Canada", "", False, None), ("yes", "remote", "yes")),
        (("Flexible / Remote; New York, NY", "", False, None), ("yes", "remote", "yes")),
        (("South San Francisco, CA", "401(k).", False, None), ("no", "onsite", "yes")),
        (("New York, NY - Hybrid", "We are a remote-friendly company.", False, None), ("no", "hybrid", "yes")),
        (("New York, NY (HQ)", "401(k)", False, "hybrid"), ("no", "hybrid", "yes")),
        (("Hybrid - Singapore", "Remote-first. E-Verify. Pay Transparency. EEO.", False, None),
         ("no", "hybrid", "no")),
        (("In-Office", "Salary: 90,000 EUR.", False, None), ("no", "onsite", "no")),
        (("Toronto, ON, CA", "", False, "remote"), ("no", "unknown", "no")),
        (("United States", "Join us.", False, None), ("unsure", "unknown", "yes")),
        (("United States", "", False, "remote"), ("yes", "remote", "yes")),
        (("San Francisco, CA", "This role is remote-friendly within the United States.", False, None),
         ("yes", "remote", "yes")),
        (("San Francisco, California, United States", "3 days a week in the office.", False, "remote"),
         ("unsure", "unknown", "yes")),
        (("Santa Fe, New Mexico", "Fully remote role.", False, None), ("unsure", "unknown", "yes")),
        (("Remote (United States)", "Must be based in Latin America.", True, None),
         ("unsure", "remote", "unsure")),
    ]

    def test_cases(self):
        for (location, text, board, hint), expected in self.CASES:
            with self.subTest(location=location, text=text):
                v = classify(location, "Software Engineer", text, remote_board=board, mode_hint=hint)
                self.assertEqual((v.remote_us, v.work_mode, v.us_eligible), expected, v.note)

    def test_note_explains_the_decision(self):
        v = classify("Remote", "Engineer", "Applicants must live in the U.S. and work EST hours.", True)
        self.assertIn("asks candidates to live in the US", v.note)
        self.assertIn("US time zones", v.note)

    def test_company_hq_is_not_candidate_location(self):
        v = classify("Remote", "Engineer", "Our company is based in London.", True)
        self.assertEqual(v.us_eligible, "unsure")

    def test_bucket_by_preference(self):
        job = lambda mode, us: {"remote_type": mode, "us_eligible": us}
        self.assertEqual(bucket(job("remote", "yes"), "remote_only"), "main")
        self.assertEqual(bucket(job("remote", "unsure"), "remote_only"), "unsure")
        self.assertEqual(bucket(job("unknown", "yes"), "remote_only"), "unsure")
        self.assertEqual(bucket(job("unknown", "no"), "remote_only"), "excluded")
        self.assertEqual(bucket(job("hybrid", "yes"), "remote_only"), "excluded")
        self.assertEqual(bucket(job("hybrid", "yes"), "remote_or_hybrid"), "main")
        self.assertEqual(bucket(job("onsite", "yes"), "any"), "main")


class NewSourceTests(unittest.TestCase):
    def test_themuse(self):
        payload = {"page_count": 1, "results": [{
            "name": "Staff Software Engineer, Backend", "publication_date": "2026-10-20T18:44:45Z",
            "locations": [{"name": "Flexible / Remote"}, {"name": "New York, NY"}],
            "refs": {"landing_page": "https://www.themuse.com/jobs/x/se"}, "company": {"name": "GlossGenius"},
            "contents": "<p>Pay: $180,000 - $220,000</p>"}]}
        jobs = list(TheMuse().fetch(ctx({"https://www.themuse.com": payload})))
        self.assertEqual(jobs[0]["location"], "Flexible / Remote; New York, NY")
        self.assertEqual(jobs[0]["salary_min"], 180000)

    def test_jobyap_fetches_details_reuses_cache_and_caps(self):
        listing = {"next_cursor": None, "jobs": [
            {"id": 1, "title": "Software Engineer", "url": "https://jobyap.com/job/1", "posted_at": TODAY},
            {"id": 2, "title": "Software Engineer II", "url": "https://jobyap.com/job/2", "posted_at": TODAY},
            {"id": 3, "title": "Senior Software Engineer", "url": "https://jobyap.com/job/3", "posted_at": TODAY},
            {"id": 4, "title": "Recruiter", "url": "https://jobyap.com/job/4", "posted_at": TODAY}]}
        detail = {"id": 2, "title": "Software Engineer II", "company": "CrowdStrike", "url": "https://jobyap.com/job/2",
                  "posted_at": TODAY, "description_markdown": "Python",
                  "locations": [{"display": "United States", "country_code": "US", "work_mode": "remote"},
                                {"display": "Austin, Texas, United States", "country_code": "US", "work_mode": "onsite"}],
                  "salaries": [{"min": 150000, "max": 200000, "currency": "USD", "period": "yearly"}]}
        cached = dict(detail, id=1, title="Software Engineer", url="https://jobyap.com/job/1")
        source = JobYap()
        c = ctx({"https://jobyap.com/api/v1/jobs/": detail, "https://jobyap.com/api/v1/jobs": listing},
                {"max_details": 1}, detail_cache={"1": cached})
        jobs = list(source.fetch(c))
        self.assertEqual([j["title"] for j in jobs], ["Software Engineer", "Software Engineer II"])
        detail_calls = [u for u, _, _ in c.http.calls if u.startswith("https://jobyap.com/api/v1/jobs/")]
        self.assertEqual(detail_calls, ["https://jobyap.com/api/v1/jobs/2"])  # job 1 came from the cache
        self.assertEqual(jobs[1]["location"], "United States (remote)")       # remote entries only
        self.assertEqual(jobs[1]["salary_max"], 200000)
        self.assertEqual(set(c.details_used), {"1", "2"})
        self.assertIn("1 new jobs left for the next run", source.warnings[0])
        self.assertEqual(c.http.calls[0][1]["posted_within"], "30d")         # 14 days -> 30d window
        v = classify(jobs[1]["location"], jobs[1]["title"], jobs[1]["description"])
        self.assertEqual(v.remote_us, "yes")

    def test_cad_software_is_not_a_currency(self):
        v = classify("Remote (USA)", "Software Engineer", "Build AI-native CAD integrations.", True)
        self.assertNotIn("currency", v.note)
        self.assertIn("currency", classify("Remote", "SE", "Pay: 120,000 CAD", True).note)

    def test_parlel_skips_details_for_foreign_and_stops_at_old(self):
        items = {"next_cursor": "more", "items": [
            {"id": "a", "title": "Software Engineer", "location": "Remote - Poland", "remote": "remote",
             "published_at": TODAY, "role_url": "https://parlel.com/c/x/a", "company": {"name": "X"}},
            {"id": "b", "title": "Software Engineer", "location": "Austin", "address_region": "TX",
             "remote": "remote", "published_at": TODAY, "role_url": "https://parlel.com/c/x/b",
             "company": {"name": "X"}},
            {"id": "c", "title": "Software Engineer", "location": "Remote", "published_at": "2020-01-01",
             "role_url": "https://parlel.com/c/x/c", "company": {"name": "X"}}]}
        detail = dict(items["items"][1], description="<p>Must reside in the US</p>")
        c = ctx({"https://api.parlel.com": items, "https://parlel.com/c/x/b.json": detail})
        jobs = list(Parlel().fetch(c))
        self.assertEqual([j["location"] for j in jobs], ["Remote - Poland", "Austin, TX"])
        self.assertEqual(jobs[0]["description"], "")
        self.assertIn("Must reside", jobs[1]["description"])
        fetched = [url for url, _, _ in c.http.calls]
        self.assertEqual(fetched.count("https://api.parlel.com/api/public/roles"), 1)  # stopped at old job

    def test_aidevjobs_rss(self):
        rss = """<rss><channel><item>
          <title>Senior Software Engineer at LangChain ($145k-$180k)</title>
          <link>https://aidevboard.com/job/x</link>
          <description>Senior Software Engineer — San Francisco, CA · full-time · Hybrid · $145k-$180k</description>
          <pubDate>Wed, 14 Oct 2026 15:18:56 +0000</pubDate><category>llm</category></item></channel></rss>"""
        jobs = list(AIDevJobs().fetch(ctx({"https://aidevboard.com": rss})))
        self.assertEqual((jobs[0]["title"], jobs[0]["company"]), ("Senior Software Engineer", "LangChain"))
        self.assertEqual((jobs[0]["location"], jobs[0]["_mode_hint"]), ("San Francisco, CA", "hybrid"))
        self.assertEqual(jobs[0]["salary_min"], 145000)

    def test_adzuna_ignores_predicted_salary(self):
        payload = {"results": [{"title": "Software Engineer", "company": {"display_name": "X"},
                                "location": {"display_name": "Remote, US"}, "description": "snippet",
                                "redirect_url": "https://adzuna/1", "created": "2026-10-20T00:00:00Z",
                                "salary_min": 90000, "salary_max": 90000, "salary_is_predicted": "1"}]}
        c = ctx({"https://api.adzuna.com": payload}, api_key={"app_id": "i", "app_key": "k"})
        jobs = list(Adzuna().fetch(c))
        self.assertIsNone(jobs[0]["salary_min"])
        self.assertEqual(c.http.calls[0][1]["app_key"], "k")

    def test_usajobs_headers_and_pay(self):
        payload = {"SearchResult": {"SearchResultItems": [{"MatchedObjectDescriptor": {
            "PositionTitle": "Software Engineer", "OrganizationName": "NASA",
            "PositionLocationDisplay": "Anywhere in the U.S. (remote job)", "PositionURI": "https://usajobs/1",
            "PositionRemuneration": [{"MinimumRange": "99000", "MaximumRange": "128000", "RateIntervalCode": "PA"}],
            "PublicationStartDate": "2026-10-20", "UserArea": {"Details": {"JobSummary": "Build things",
                                                                           "MajorDuties": ["Code"]}}}}]}}
        c = ctx({"https://data.usajobs.gov": payload}, api_key={"email": "me@x.com", "api_key": "k"})
        jobs = list(USAJobs().fetch(c))
        self.assertEqual((jobs[0]["salary_min"], jobs[0]["_mode_hint"]), (99000, "remote"))
        self.assertEqual(c.http.calls[0][2]["Authorization-Key"], "k")
        v = classify(jobs[0]["location"], jobs[0]["title"], jobs[0]["description"], mode_hint="remote")
        self.assertEqual(v.remote_us, "yes")

    def test_jooble_posts_and_findwork_token(self):
        jooble = {"jobs": [{"title": "Software Engineer", "company": "X", "link": "https://jooble/1",
                            "location": "Remote", "snippet": "<b>Python</b>", "updated": "2026-10-20T00:00:00"}]}
        c = ctx({"https://jooble.org/api/k": jooble}, api_key={"api_key": "k"})
        self.assertEqual(list(Jooble().fetch(c))[0]["description"], "Python")
        self.assertEqual(c.http.calls[0][1]["location"], "Remote")
        findwork = {"results": [{"role": "Software Engineer", "company_name": "Y", "url": "https://fw/1",
                                 "location": "", "remote": True, "text": "<p>Go</p>", "date_posted": TODAY}]}
        c = ctx({"https://findwork.dev": findwork}, api_key={"api_key": "t"})
        self.assertEqual(list(Findwork().fetch(c))[0]["_mode_hint"], "remote")
        self.assertEqual(c.http.calls[0][2]["Authorization"], "Token t")


if __name__ == "__main__":
    unittest.main()
