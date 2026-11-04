"""Read a GitHub account's repositories: your commit share, languages, dependency files, README.

Uses the signed-in GitHub CLI's token when available (sees private and organization repos),
or the public API without any key (public repos only, 60 requests per hour).
"""
import base64
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field

from ..scrapers.base import Http, SourceError

log = logging.getLogger("jobtracker.catalog")

API = "https://api.github.com"
MANIFEST = re.compile(r"(^|/)(requirements[^/]*\.txt|pyproject\.toml|setup\.py|environment\.ya?ml|Pipfile|"
                      r"package\.json|[^/]+\.csproj|pom\.xml|build\.gradle|Gemfile|CMakeLists\.txt|Cargo\.toml|"
                      r"go\.mod|Dockerfile|docker-compose\.ya?ml|\.github/workflows/[^/]+\.ya?ml)$", re.I)
SKIP_REPOS = re.compile(r"^\.github(-private)?$")
MAX_MANIFESTS = 12
TOOLING = re.compile(r"(\.github/workflows/|Dockerfile$|docker-compose)", re.I)


@dataclass
class Repo:
    """Everything collected about one repository."""
    full_name: str
    name: str
    owner: str
    private: bool
    fork: bool
    description: str
    url: str
    pushed_at: str
    created_at: str
    default_branch: str
    my_commits: int = 0
    total_commits: int = 0
    first_commit: str = ""
    last_commit: str = ""
    languages: dict = field(default_factory=dict)   # language -> bytes
    manifests: dict = field(default_factory=dict)   # path -> text
    readme: str = ""
    files: list = field(default_factory=list)       # a sample of paths, for context

    @property
    def share(self) -> float:
        return self.my_commits / self.total_commits if self.total_commits else 0.0


def gh_token() -> str | None:
    """GITHUB_TOKEN, or the GitHub CLI's token if you are signed in. None means public access only."""
    if os.environ.get("GITHUB_TOKEN"):
        return os.environ["GITHUB_TOKEN"]
    if shutil.which("gh"):
        result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=20)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return None


def git_emails() -> set[str]:
    """Your git commit email, to count commits that GitHub did not link to your account."""
    if not shutil.which("git"):
        return set()
    result = subprocess.run(["git", "config", "--global", "user.email"], capture_output=True, text=True, timeout=10)
    return {result.stdout.strip().lower()} if result.stdout.strip() else set()


class GitHub:
    """Small GitHub REST client built on the project's polite HTTP client."""

    def __init__(self, token: str | None = None):
        self.http = Http(delay=0.25 if token else 1.5)
        self.headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        self.authenticated = bool(token)

    def get(self, path: str, **params):
        return self.http.get(f"{API}/{path}", headers=self.headers, **params)

    def json(self, path: str, **params):
        return self.get(path, **params).json()

    def paged(self, path: str, limit: int = 1000, **params) -> list:
        """All pages of a list endpoint (up to `limit` items)."""
        items, page = [], 1
        while len(items) < limit:
            batch = self.json(path, per_page=100, page=page, **params)
            if not isinstance(batch, list) or not batch:
                break
            items += batch
            if len(batch) < 100:
                break
            page += 1
        return items

    def optional(self, path: str, **params):
        """None instead of an error for things a repo may not have (README, empty history)."""
        try:
            return self.json(path, **params)
        except SourceError as exc:
            log.info("GitHub %s: %s", path, exc)
            return None


def list_repos(gh: GitHub, user: str) -> list[dict]:
    """Repos you own, collaborate on, or reach through an organization (or just public ones)."""
    if gh.authenticated and gh.json("user").get("login", "").lower() == user.lower():
        return gh.paged("user/repos", affiliation="owner,collaborator,organization_member")
    return gh.paged(f"users/{user}/repos", type="owner")


def commit_counts(gh: GitHub, full_name: str, user: str, emails: set[str]) -> tuple[int, int]:
    """(your commits, all commits), from the contributors list."""
    people = gh.optional(f"repos/{full_name}/contributors", per_page=100, anon=1) or []
    mine = total = 0
    for person in people if isinstance(people, list) else []:
        count = person.get("contributions", 0)
        total += count
        if (person.get("login") or "").lower() == user.lower() or (person.get("email") or "").lower() in emails:
            mine += count
    return mine, total


def commit_dates(gh: GitHub, full_name: str, user: str) -> tuple[str, str]:
    """(first, last) date of your commits."""
    first_page = gh.get(f"repos/{full_name}/commits", author=user, per_page=1)
    newest = first_page.json()
    if not newest:
        return "", ""
    last = newest[0]["commit"]["author"]["date"][:10]
    last_url = first_page.links.get("last", {}).get("url")
    if not last_url:
        return last, last
    page = re.search(r"[?&]page=(\d+)", last_url).group(1)
    oldest = gh.json(f"repos/{full_name}/commits", author=user, per_page=1, page=page)
    return (oldest[0]["commit"]["author"]["date"][:10] if oldest else last), last


def file_text(gh: GitHub, full_name: str, path: str, limit: int = 6000) -> str:
    data = gh.optional(f"repos/{full_name}/contents/{path}")
    if not isinstance(data, dict) or data.get("encoding") != "base64":
        return ""
    return base64.b64decode(data["content"]).decode("utf-8", errors="replace")[:limit]


def collect_repo(gh: GitHub, raw: dict, user: str, emails: set[str]) -> Repo:
    """All details for one repo (call only for repos you committed to)."""
    repo = Repo(full_name=raw["full_name"], name=raw["name"], owner=raw["owner"]["login"],
                private=raw["private"], fork=raw["fork"], description=raw.get("description") or "",
                url=raw["html_url"], pushed_at=raw.get("pushed_at") or "", created_at=raw.get("created_at") or "",
                default_branch=raw.get("default_branch") or "main")
    repo.my_commits, repo.total_commits = commit_counts(gh, repo.full_name, user, emails)
    if not repo.my_commits:
        return repo
    repo.first_commit, repo.last_commit = commit_dates(gh, repo.full_name, user)
    repo.languages = gh.optional(f"repos/{repo.full_name}/languages") or {}
    tree = gh.optional(f"repos/{repo.full_name}/git/trees/{repo.default_branch}", recursive=1) or {}
    paths = [t["path"] for t in tree.get("tree", []) if t.get("type") == "blob"]
    repo.files = [p for p in paths if "/" not in p or p.count("/") == 1][:80]
    manifests = [p for p in paths if MANIFEST.search(p) and "node_modules/" not in p]
    manifests = sorted(manifests, key=lambda p: bool(TOOLING.search(p)))[:MAX_MANIFESTS]  # dependency files first
    repo.manifests = {p: file_text(gh, repo.full_name, p) for p in manifests}
    readme = gh.optional(f"repos/{repo.full_name}/readme")
    if isinstance(readme, dict) and readme.get("encoding") == "base64":
        repo.readme = base64.b64decode(readme["content"]).decode("utf-8", errors="replace")[:12000]
    return repo
