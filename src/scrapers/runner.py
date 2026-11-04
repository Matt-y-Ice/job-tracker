"""Run every enabled source, filter, de-duplicate, and sort jobs into three files:

  data/jobs_raw.json       remote and open to the US (or matching your work preference)
  data/jobs_unsure.json    could not tell if remote / open to the US: kept for you to check
  data/jobs_excluded.json  clearly onsite, hybrid, or limited to other countries: kept with the reason
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable

from ..config import load_json, save_json
from ..paths import CONFIG_DIR, DATA_DIR, PREFERENCES_FILE, SOURCES_FILE
from . import discover
from .base import Context, Http, Source, SourceError, is_recent, title_matcher
from .dedupe import dedupe
from .location import bucket, classify

log = logging.getLogger("jobtracker.scrapers")

RAW_FILE = DATA_DIR / "jobs_raw.json"
UNSURE_FILE = DATA_DIR / "jobs_unsure.json"
EXCLUDED_FILE = DATA_DIR / "jobs_excluded.json"
SOURCE_CACHE_DIR = DATA_DIR / "sources"
API_KEYS_FILE = CONFIG_DIR / "api_keys.json"
Report = Callable[[str], None]


@dataclass
class SourceResult:
    """Outcome of one source: its jobs, or the error that stopped it."""
    key: str
    label: str
    jobs: list[dict] = field(default_factory=list)
    error: str = ""
    warnings: list[str] = field(default_factory=list)
    cached_at: str = ""
    seconds: float = 0.0


@dataclass
class ScrapeResult:
    """Everything the scrape produced."""
    jobs: list[dict]
    unsure: list[dict]
    excluded: list[dict]
    sources: list[SourceResult]
    duplicates_removed: int
    keywords: list[str]
    days_back: int


# ---------------------------------------------------------------- configuration

def load_settings() -> tuple[dict, dict]:
    """Return (sources.json contents, preferences.json contents or {})."""
    config = load_json(SOURCES_FILE)
    if config is None:
        raise SourceError(f"{SOURCES_FILE} is missing. Restore it from the download.")
    return config, load_json(PREFERENCES_FILE, {}) or {}


def search_keywords(config: dict, prefs: dict) -> list[str]:
    """Keywords from sources.json plus the target titles from the questionnaire."""
    words = list(config.get("keywords", []))
    if config.get("include_target_titles", True):
        words += prefs.get("target_titles", [])
    seen, unique = set(), []
    for word in words:
        if word.strip() and word.strip().lower() not in seen:
            seen.add(word.strip().lower())
            unique.append(word.strip())
    return unique


def has_key(entry: dict) -> bool:
    """True if every value in an api_keys.json entry is filled in."""
    return bool(entry) and all(str(v).strip() for v in entry.values())


def enabled_sources(config: dict, api_keys: dict) -> list[tuple[Source, dict]]:
    """Instantiate every source that is turned on, with its settings.

    Sources that need a key turn on by themselves once the key is filled in.
    """
    settings = config.get("sources", {})
    chosen = []
    for key, cls in discover().items():
        own = settings.get(key, {})
        default = has_key(api_keys.get(key, {})) if cls.needs_key else cls.enabled_by_default
        if own.get("enabled", default):
            chosen.append((cls(), own))
    return chosen


# ---------------------------------------------------------------- per-source cache

def read_cache(source: Source) -> dict:
    """The last fetch for this source: {'fetched_at': ..., 'jobs': [...]}, or {}."""
    return load_json(SOURCE_CACHE_DIR / f"{source.key}.json", {}) or {}


def fresh_cache(cached: dict, source: Source, own: dict) -> bool:
    """True if the last fetch is newer than the source's minimum refresh time."""
    hours = float(own.get("min_refresh_hours", source.min_refresh_hours))
    if not hours or not cached:
        return False
    return datetime.now() - datetime.fromisoformat(cached["fetched_at"]) <= timedelta(hours=hours)


def write_cache(source: Source, jobs: list[dict]) -> None:
    """Save this source's jobs so the next run can reuse or inspect them."""
    save_json(SOURCE_CACHE_DIR / f"{source.key}.json",
              {"fetched_at": datetime.now().isoformat(timespec="seconds"), "jobs": jobs})


# ---------------------------------------------------------------- running

def run_source(source: Source, own: dict, config: dict, keywords: list[str],
               days_back: int = 14, api_key: dict | None = None) -> SourceResult:
    """Fetch one source, never raising: failures are captured in the result."""
    result = SourceResult(source.key, source.label)
    started = time.monotonic()
    cached = read_cache(source)
    if fresh_cache(cached, source, own):
        result.jobs, result.cached_at = cached["jobs"], cached["fetched_at"]
        return result
    if source.needs_key and not has_key(api_key or {}):
        result.error = f"needs a free API key in {API_KEYS_FILE.name} (see api_keys.example.json)"
        return result
    http = Http(delay=float(own.get("request_delay_seconds", config.get("request_delay_seconds", 1.5))))
    details_file = SOURCE_CACHE_DIR / f"{source.key}_details.json"
    ctx = Context(http=http, settings=own, keywords=keywords, wants_title=title_matcher(keywords),
                  days_back=days_back, api_key=api_key or {},
                  detail_cache=load_json(details_file, {}) or {})
    try:
        result.jobs = list(source.fetch(ctx))
        write_cache(source, result.jobs)
        if ctx.details_used:  # only entries used this run, so the cache never grows without limit
            save_json(details_file, ctx.details_used)
    except SourceError as exc:
        result.error = str(exc)
    except Exception as exc:  # a broken source must not stop the others
        log.exception("%s crashed", source.label)
        result.error = f"unexpected error: {exc.__class__.__name__}: {exc}"
    result.warnings = source.warnings
    result.seconds = time.monotonic() - started
    return result


def describe(result: SourceResult) -> str:
    """One status line for a finished source."""
    if result.error:
        return f"{result.label}: FAILED ({result.error}). Other sources continue."
    note = f", reused results from {result.cached_at[11:16]}" if result.cached_at else f", {result.seconds:.0f}s"
    extra = f" [{'; '.join(result.warnings)}]" if result.warnings else ""
    return f"{result.label}: {len(result.jobs)} jobs{note}{extra}"


def classify_all(jobs: list[dict], remote_boards: set[str]) -> None:
    """Fill in remote_type, remote_us, us_eligible, and location_note on every job."""
    for job in jobs:
        sources = {job["source"], *job.pop("_also", [])}
        verdict = classify(job["location"], job["title"], job["description"],
                           remote_board=bool(sources & remote_boards),
                           mode_hint=job.pop("_mode_hint", None))
        job["remote_type"] = verdict.work_mode
        job["remote_us"] = verdict.remote_us
        job["us_eligible"] = verdict.us_eligible
        job["location_note"] = verdict.note


def scrape(report: Report = print, progress_desc: str | None = None) -> ScrapeResult:
    """Run all enabled sources in parallel and write the three job files.

    progress_desc: show a progress bar over sources with this label (None = no bar).
    """
    config, prefs = load_settings()
    api_keys = load_json(API_KEYS_FILE, {}) or {}
    keywords = search_keywords(config, prefs)
    days_back = int(prefs.get("days_back") or config.get("default_days_back", 14))
    cutoff = date.today() - timedelta(days=days_back)
    wants = title_matcher(keywords)

    sources = enabled_sources(config, api_keys)
    log.info("Scraping %d sources, keywords=%s, days_back=%d", len(sources), keywords, days_back)
    results = []
    from tqdm import tqdm

    bar = tqdm(total=len(sources), desc=progress_desc, unit="source", leave=False,
               disable=progress_desc is None, bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}]")
    with ThreadPoolExecutor(max_workers=min(8, len(sources) or 1)) as pool:
        futures = [pool.submit(run_source, s, own, config, keywords, days_back, api_keys.get(s.key))
                   for s, own in sources]
        for future in as_completed(futures):
            result = future.result()
            result.jobs = [j for j in result.jobs if wants(j["title"]) and is_recent(j, cutoff)]
            results.append(result)
            (log.warning if result.error else log.info)(describe(result))
            bar.clear()
            report(describe(result))
            bar.update()
    bar.close()

    order = {s.key: i for i, (s, _) in enumerate(sources)}
    results.sort(key=lambda r: order[r.key])
    jobs, removed = dedupe([dict(j) for r in results for j in r.jobs])
    classify_all(jobs, {s.label for s, _ in sources if s.remote_board})
    jobs.sort(key=lambda j: j["date_posted"] or "", reverse=True)

    preference = prefs.get("remote_preference", "remote_only")
    groups = {"main": [], "unsure": [], "excluded": []}
    for job in jobs:
        groups[bucket(job, preference)].append(job)
    outcome = ScrapeResult(groups["main"], groups["unsure"], groups["excluded"], results,
                           removed, keywords, days_back)
    write_outputs(outcome)
    return outcome


def write_outputs(outcome: ScrapeResult) -> None:
    """Write jobs_raw.json (with run details), jobs_unsure.json, and jobs_excluded.json."""
    stamp = datetime.now().isoformat(timespec="seconds")
    save_json(RAW_FILE, {
        "scraped_at": stamp,
        "keywords": outcome.keywords,
        "days_back": outcome.days_back,
        "sources": {r.label: {"count": len(r.jobs), "error": r.error or None,
                              "warnings": r.warnings, "reused_from": r.cached_at or None}
                    for r in outcome.sources},
        "duplicates_removed": outcome.duplicates_removed,
        "total": len(outcome.jobs),
        "unsure": len(outcome.unsure),
        "excluded": len(outcome.excluded),
        "jobs": outcome.jobs,
    })
    for path, jobs, about in [
        (UNSURE_FILE, outcome.unsure, "Could not tell if these are remote or open to the US. See location_note."),
        (EXCLUDED_FILE, outcome.excluded, "Onsite, hybrid, or limited to other countries. See location_note."),
    ]:
        save_json(path, {"scraped_at": stamp, "about": about, "total": len(jobs), "jobs": jobs})


def summary_line(outcome: ScrapeResult) -> str:
    """'RemoteOK 34, Remotive 21, WWR 18 (12 duplicates removed)'"""
    parts = [f"{r.label} {'failed' if r.error else len(r.jobs)}" for r in outcome.sources]
    return f"{', '.join(parts)} ({outcome.duplicates_removed} duplicates removed)"


def bucket_line(outcome: ScrapeResult) -> str:
    """'180 remote US, 40 unsure (kept separately), 60 excluded (kept with reasons)'"""
    return (f"{len(outcome.jobs)} remote US, {len(outcome.unsure)} unsure (kept separately), "
            f"{len(outcome.excluded)} not remote US (kept with reasons)")
