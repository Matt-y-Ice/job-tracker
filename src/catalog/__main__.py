"""Build your skill catalog from GitHub:

    python -m src.catalog github YOUR_GITHUB_NAME [--no-summaries]

Uses your GitHub CLI sign-in if you have one (includes private and organization repos);
otherwise reads public repos without any key.
"""
import argparse
import sys
import time

from ..log import setup_logging
from ..scrapers.base import SourceError
from . import CATALOG_FILE
from .builder import build_catalog
from .github import SKIP_REPOS, GitHub, collect_repo, gh_token, git_emails, list_repos


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect your experience from GitHub into a skill catalog.")
    sub = parser.add_subparsers(dest="source", required=True)
    gh_cmd = sub.add_parser("github", help="read a GitHub account")
    gh_cmd.add_argument("user", help="GitHub user name, e.g. octocat")
    gh_cmd.add_argument("--no-summaries", action="store_true", help="skip the AI-written summaries (faster)")
    args = parser.parse_args()
    setup_logging()
    started = time.monotonic()

    token = gh_token()
    gh = GitHub(token)
    print("Reading GitHub " + ("(signed in: private and organization repos included)" if token
                               else "(public repos only; sign in with 'gh auth login' to include private ones)"))
    try:
        raw = [r for r in list_repos(gh, args.user) if not SKIP_REPOS.match(r["name"])]
        print(f"{len(raw)} repositories found. Checking which ones you committed to...")
        emails = git_emails()
        repos = []
        for i, item in enumerate(raw, 1):
            repo = collect_repo(gh, item, args.user, emails)
            if repo.my_commits:
                repos.append(repo)
            print(f"  [{i}/{len(raw)}] {repo.full_name}: {repo.my_commits} of {repo.total_commits} commits"
                  + ("" if repo.my_commits else " (skipped: none of your commits)"))
    except SourceError as exc:
        print(f"GitHub problem: {exc}. If you hit the hourly limit, sign in with 'gh auth login' or try later.")
        return 1

    print(f"Summarizing {len(repos)} repositories with your commits (only changed ones use the AI model)...")
    catalog = build_catalog(repos, args.user, use_model=not args.no_summaries, progress=lambda s: print(f"  {s}"))
    print(f"Done in {time.monotonic() - started:.0f}s: {len(catalog['projects'])} projects, "
          f"{len(catalog['skills'])} skills -> {CATALOG_FILE}")
    print("Open that file to review it. Set \"include\": false on anything you don't want used.")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace", line_buffering=True)
    sys.exit(main())
