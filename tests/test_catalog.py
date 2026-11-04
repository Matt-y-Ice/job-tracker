"""Skill catalog tests: GitHub evidence, honesty rules, merging your edits, and how resumes,
cover letters, and ranking use the catalog."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from src import ranker
from src.catalog import load_catalog
from src.catalog import builder as cb
from src.catalog import github as gh
from src.catalog.github import Repo
from src.documents import generator as gen
from src.resume_parser import parse_resume
from tests.test_phase7 import FakeOllama, JOB, RESUME

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def repo(name="GearShare", owner="me", mine=17, total=19, languages=None, manifests=None, readme="", files=(),
         description="Equipment lending REST API", pushed="2026-10-19T00:00:00Z"):
    r = Repo(full_name=f"{owner}/{name}", name=name, owner=owner, private=False, fork=False, description=description,
             url=f"https://github.com/{owner}/{name}", pushed_at=pushed, created_at="2026-10-01", default_branch="main")
    r.my_commits, r.total_commits = mine, total
    r.first_commit, r.last_commit = "2026-10-01", "2026-10-19"
    r.languages = languages if languages is not None else {"C#": 9000, "Dockerfile": 300}
    r.manifests = manifests if manifests is not None else {
        "GearShare.Api/GearShare.Api.csproj": '<PackageReference Include="Microsoft.AspNetCore.OpenApi" />'
                                             '<PackageReference Include="Npgsql.EntityFrameworkCore.PostgreSQL" />'}
    r.readme, r.files = readme, list(files)
    return r


class EvidenceTests(unittest.TestCase):
    def test_code_skills_from_languages_dependencies_and_files(self):
        skills = cb.code_skills(repo(files=["Dockerfile", ".github/workflows/ci.yml"]))
        for expected in ("C#", "Docker", "ASP.NET Core", "PostgreSQL", "Entity Framework Core", "GitHub Actions"):
            self.assertIn(expected, skills)

    def test_tiny_language_share_is_not_a_skill(self):
        skills = cb.code_skills(repo(languages={"Python": 98000, "Shell": 900}, manifests={}))
        self.assertEqual(skills, ["Python"])

    def test_skill_credit_needs_a_real_share(self):
        self.assertTrue(cb.counts_for_skills(repo(mine=8, total=55)))     # 15%
        self.assertTrue(cb.counts_for_skills(repo(mine=12, total=500)))   # 10+ commits
        self.assertFalse(cb.counts_for_skills(repo(mine=2, total=300)))

    def test_context_and_role(self):
        self.assertEqual(cb.context_of(repo(name="CSC581_GameEngine"), "me")["label"], "Coursework (CSC 581)")
        self.assertEqual(cb.context_of(repo(owner="ncsu-csc-501", name="project2"), "me")["kind"], "coursework")
        self.assertEqual(cb.context_of(repo(), "me")["kind"], "personal")
        self.assertEqual(cb.context_of(repo(owner="ISL-LAB", name="ATLAS"), "me")["label"], "Team project in ISL-LAB")
        self.assertEqual(cb.role_of(repo(mine=17, total=19)), "built")
        self.assertEqual(cb.role_of(repo(mine=8, total=55)), "contributed (8 of 55 commits)")

    def test_ungrounded_wording_and_team_claims(self):
        r = repo(readme="Inventory tracking with RFID tags for the NASA MINDS competition.", mine=43, total=105)
        draft = {"summary": "A blockchain marketplace for satellites.",                  # nothing like the README
                 "highlights": ["Built an inventory tracking system using RFID tags.",
                                "Designed a Kubernetes autoscaler for satellites."],
                 "tech": ["C#"]}
        checked = cb.check_summary(draft, r, cb.code_skills(r))
        self.assertEqual(checked["highlights"], ["Contributed to building an inventory tracking system using RFID tags."])
        self.assertEqual(checked["summary"], "Equipment lending REST API.")              # fell back to the description

    def test_profile_repo_and_token_contributions(self):
        self.assertTrue(cb.is_profile_repo("octocat/octocat"))
        self.assertFalse(cb.worth_including(repo(mine=1, total=9)))                      # a token contribution
        self.assertFalse(cb.worth_including(repo(readme="", description="", languages={})))  # nothing to describe
        self.assertTrue(cb.worth_including(repo(mine=2, total=2)))                       # small but all yours

    def test_own_readme_counts_but_team_readme_does_not(self):
        own = repo(readme="A REST API backed by PostgreSQL.", languages={"C#": 100}, manifests={})
        self.assertIn("PostgreSQL", cb.evidence_skills(own, cb.code_skills(own)))
        team = repo(readme="A REST API backed by PostgreSQL.", languages={"C#": 100}, manifests={}, mine=20, total=100)
        self.assertNotIn("PostgreSQL", cb.evidence_skills(team, cb.code_skills(team)))
        self.assertEqual(cb.evidence_skills(repo(mine=1, total=200), ["C#"]), [])   # token share: nothing

    def test_ci_files_are_not_skill_evidence(self):
        r = repo(languages={"Python": 100}, manifests={".github/workflows/ci.yml": "runs-on: ubuntu-latest\nuses: actions/checkout"})
        self.assertEqual(cb.code_skills(r), ["Python", "GitHub Actions"])  # the workflow itself, but not
        # "Linux" or "GitHub" from words inside it

    def test_summary_fact_check(self):
        r = repo(readme="GearShare lends equipment. Reservation endpoints support 3 roles and use Swagger docs.")
        draft = {"summary": "A lending API used by 500 teams.",
                 "highlights": ["Built reservation endpoints for 3 roles.", "Cut latency by 40%."],
                 "tech": ["C#", "Swagger", "Kubernetes"]}
        checked = cb.check_summary(draft, r, cb.code_skills(r))
        self.assertEqual(checked["highlights"], ["Built reservation endpoints for 3 roles."])
        self.assertEqual(checked["tech"], ["C#", "Swagger"])                 # Kubernetes is nowhere in the repo
        self.assertEqual(checked["summary"], "Equipment lending REST API.")  # "500 teams" was invented

    def test_skills_only_from_evidence_projects(self):
        projects = [cb.project_entry(repo(), "me", None, None),
                    cb.project_entry(repo(name="Big", owner="lab", mine=2, total=300,
                                          languages={"Rust": 5000}, manifests={}), "me", None, None)]
        names = [s["name"] for s in cb.skills_from(projects)]
        self.assertIn("C#", names)
        self.assertNotIn("Rust", names)   # 2 of 300 commits proves nothing about Rust


class MergeAndLoadTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "skill_catalog.json"

    def test_rebuild_keeps_your_edits(self):
        cb.build_catalog([repo(), repo(name="Old", pushed="2024-01-01T00:00:00Z")], "me", use_model=False, path=self.path)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        for p in data["projects"]:
            if p["name"] == "GearShare":
                p.update(include=False, notes="private for now")
            if p["name"] == "Old":
                p.update(summary="My own words.", locked=True)
        data["skills"][0]["include"] = False
        self.path.write_text(json.dumps(data), encoding="utf-8")

        cb.build_catalog([repo(pushed="2026-10-28T00:00:00Z"), repo(name="Old", pushed="2025-05-05T00:00:00Z")],
                         "me", use_model=False, path=self.path)
        again = {p["name"]: p for p in json.loads(self.path.read_text(encoding="utf-8"))["projects"]}
        self.assertEqual((again["GearShare"]["include"], again["GearShare"]["notes"]), (False, "private for now"))
        self.assertEqual(again["Old"]["summary"], "My own words.")
        self.assertFalse(json.loads(self.path.read_text(encoding="utf-8"))["skills"][0]["include"])

    def test_load_catalog_drops_switched_off_things(self):
        cb.build_catalog([repo(), repo(name="Py", languages={"Python": 100}, manifests={})], "me",
                         use_model=False, path=self.path)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        for p in data["projects"]:
            p["include"] = p["name"] != "Py"
        self.path.write_text(json.dumps(data), encoding="utf-8")
        loaded = load_catalog(self.path)
        self.assertEqual([p["name"] for p in loaded["projects"]], ["GearShare"])
        self.assertNotIn("Python", [s["name"] for s in loaded["skills"]])  # only proven by a switched-off project
        self.assertEqual(load_catalog(self.path.parent / "missing.json")["projects"], [])


class FakeGitHub:
    def __init__(self, contributors):
        self.contributors = contributors

    def optional(self, path, **params):
        return self.contributors


class GitHubTests(unittest.TestCase):
    def test_commit_counts_include_unlinked_email(self):
        fake = FakeGitHub([{"login": "octocat", "contributions": 17},
                           {"type": "Anonymous", "name": "Octo", "email": "me@example.com", "contributions": 2},
                           {"login": "someone", "contributions": 5}])
        self.assertEqual(gh.commit_counts(fake, "x/y", "OctoCat", {"me@example.com"}), (19, 24))

    def test_no_commits_means_no_details_fetched(self):
        fake = FakeGitHub([{"login": "other", "contributions": 30}])
        raw = {"full_name": "lab/x", "name": "x", "owner": {"login": "lab"}, "private": True, "fork": False,
               "html_url": "u", "pushed_at": "p", "created_at": "c", "default_branch": "main"}
        result = gh.collect_repo(fake, raw, "me", set())
        self.assertEqual((result.my_commits, result.languages), (0, {}))


CATALOG = {"skills": [{"name": "C++", "projects": ["me/Engine"]}, {"name": "CMake", "projects": ["me/Engine"]}],
           "projects": [{"id": "me/Engine", "name": "CSC581_GameEngine", "include": True, "context": "Coursework (CSC 581)",
                         "role": "contributed (34 of 89 commits)", "summary": "A multiplayer 2D game engine in C++.",
                         "highlights": ["Implemented networking for 4 concurrent clients."],
                         "tech": ["C++", "CMake", "ZeroMQ"], "skills_evidence": ["C++", "CMake"],
                         "first_commit": "2025-08-24", "last_commit": "2025-12-02"}]}


class DocumentsUseCatalogTests(unittest.TestCase):
    def draft(self, resume_reply):
        fake = FakeOllama(resume=resume_reply)
        return gen.generate({**JOB, "description": "C++ engine work. " + JOB["description"]}, RESUME, prefs={},
                            ollama=fake, catalog=CATALOG), fake

    def test_catalog_skill_project_and_number_are_allowed(self):
        reply = {"headline": "Backend Developer", "summary": "Backend developer.",
                 "skills": [{"label": "Languages", "items": ["Python", "C++", "Haskell"]}],
                 "experience": [{"title": "Backend Developer", "company": "Brightwave Analytics", "location": "",
                                 "dates": "Jan 2021 - Present", "bullets": ["Wrote pytest suites that raised coverage from 45% to 88%."]}],
                 "projects": [{"name": "CSC581 GameEngine", "tech": "C++, CMake",
                               "bullets": ["Contributed networking for 4 concurrent clients."]},
                              {"name": "Imaginary OS", "tech": "Rust", "bullets": ["Made up."]}]}
        draft, fake = self.draft(reply)
        items = [i for g in draft.resume["skills"] for i in g["items"]]
        self.assertIn("C++", items)            # proven by the catalog
        self.assertNotIn("Haskell", items)     # proven by nothing
        self.assertEqual([p["name"] for p in draft.resume["projects"]], ["CSC581 GameEngine"])
        self.assertEqual(draft.resume["projects"][0]["bullets"], ["Contributed networking for 4 concurrent clients."])

    def test_summary_claims_and_locations_are_checked(self):
        reply = {"headline": "Backend Developer",
                 "summary": "Backend developer building FastAPI services in Python. Senior engineer and expert in "
                            "Android systems programming. Strong background in real-time 3D rendering with Vulkan.",
                 "skills": [{"label": "Graphics", "items": ["Vulkan", "Real-time 3D rendering"]}],
                 "experience": [{"title": "Backend Developer", "company": "Brightwave Analytics", "location": "Remote",
                                 "dates": "Jan 2021 - Present", "bullets": ["Wrote pytest suites that raised coverage from 45% to 88%."]}],
                 "projects": []}
        draft, _ = self.draft(reply)
        self.assertEqual(draft.resume["summary"], "Backend developer building FastAPI services in Python.")
        self.assertEqual(draft.resume["experience"][0]["location"], "")          # "Remote" is not on the resume
        self.assertTrue(any("claiming something your resume doesn't show" in n for n in draft.notes))

    def test_summary_falls_back_to_your_own_words(self):
        reply = {"headline": "Backend Developer", "summary": "Expert Android engineer.", "skills": [],
                 "experience": [{"title": "Backend Developer", "company": "Brightwave Analytics", "location": "",
                                 "dates": "Jan 2021 - Present", "bullets": []}], "projects": []}
        draft, _ = self.draft(reply)
        self.assertTrue(draft.resume["summary"].startswith("Backend developer with 5+ years of experience"))

    def test_dropped_positions_are_added_back_in_order(self):
        reply = {"headline": "Backend Developer", "summary": "Backend developer.", "skills": [],
                 "experience": [{"title": "Backend Developer", "company": "Brightwave Analytics", "location": "",
                                 "dates": "Jan 2021 - Present", "bullets": []}], "projects": []}
        draft, _ = self.draft(reply)
        exp = draft.resume["experience"]
        self.assertEqual([e["title"] for e in exp], ["Backend Developer", "Software Engineer Intern", "IT Support Technician"])
        self.assertEqual(exp[2]["company"], "Initech Services")
        self.assertEqual(exp[2]["bullets"], ["Managed 300 Windows laptops and wrote PowerShell scripts to automate onboarding."])
        self.assertTrue(any("Added back your position" in n for n in draft.notes))

    def test_team_project_bullets_always_say_contributed(self):
        reply = {"headline": "Backend Developer", "summary": "Backend developer.", "skills": [],
                 "experience": [{"title": "Backend Developer", "company": "Brightwave Analytics", "location": "",
                                 "dates": "Jan 2021 - Present", "bullets": ["Wrote pytest suites that raised coverage from 45% to 88%."]}],
                 "projects": [{"name": "CSC581_GameEngine", "tech": "C++",
                               "bullets": ["Integrated networking for 4 concurrent clients."]}]}
        draft, _ = self.draft(reply)
        self.assertEqual(draft.resume["projects"][0]["bullets"],
                         ["Contributed to integrating networking for 4 concurrent clients."])
        self.assertTrue(any("as a contribution" in n for n in draft.notes))

    def test_prompt_lists_relevant_github_projects(self):
        messages = gen.resume_prompt(RESUME, {}, {**JOB, "description": "C++ and CMake"}, 5000, CATALOG)
        self.assertIn('Project "CSC581_GameEngine"', messages[1]["content"])
        self.assertIn("contributed (34 of 89 commits)", messages[1]["content"])
        self.assertIn("C++", messages[0]["content"])


class RankingUsesCatalogTests(unittest.TestCase):
    def test_catalog_skills_count_for_keywords_and_prompt(self):
        job = {"title": "Game Engineer", "description": "C++ and CMake required.", "bucket": "main",
               "flag_reasons": [], "id": "j", "salary_max": None, "salary_min": None}
        prefs = ranker.with_defaults({})
        without = ranker.score_job(dict(job), {"j": 50}, {}, RESUME, prefs)["keyword_score"]
        with_cat = ranker.score_job(dict(job), {"j": 50}, {}, RESUME, prefs, ["C++", "CMake"])["keyword_score"]
        self.assertGreater(with_cat, without)
        self.assertIn("GitHub projects: CSC581_GameEngine", ranker.system_prompt(RESUME, prefs, CATALOG))
        self.assertNotIn("Skills shown in their GitHub code", ranker.system_prompt(RESUME, prefs, {}))

    def test_catalog_change_rescores_and_says_why(self):
        from src.cache import Cache
        with tempfile.TemporaryDirectory() as tmp:
            cache = Cache(Path(tmp) / "c.db")
            first = ranker.explain_changes(cache, RESUME, "p", "m", ranker.catalog_hash(CATALOG))
            changed = {**CATALOG, "skills": CATALOG["skills"] + [{"name": "SDL"}]}
            second = ranker.explain_changes(cache, RESUME, "p", "m", ranker.catalog_hash(changed))
            cache.close()
        self.assertEqual(first, [])
        self.assertTrue(any("skill catalog changed" in n for n in second))
        self.assertNotEqual(ranker.review_context(RESUME, {}, "m", ranker.catalog_hash(CATALOG)),
                            ranker.review_context(RESUME, {}, "m", ""))
        self.assertEqual(ranker.review_context(RESUME, {}, "m", ""), ranker.review_context(RESUME, {}, "m"))


if __name__ == "__main__":
    unittest.main()
