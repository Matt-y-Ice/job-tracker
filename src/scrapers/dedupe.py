"""Remove duplicate jobs: exact id matches, plus the same job posted on two different sources."""
import re
from difflib import SequenceMatcher

from .base import normalize_words

COMPANY_NOISE = re.compile(
    r"\b(inc|llc|ltd|limited|corp|corporation|co|company|gmbh|plc|ag|sa|bv|"
    r"technologies|technology|labs|group|holdings|the)\b")
TITLE_NOISE = re.compile(r"\(.*?\)|\[.*?\]|\b(remote|us|usa|united states|anywhere|worldwide)\b")
LEVEL_WORDS = {"i", "ii", "iii", "iv", "v", "1", "2", "3", "4", "5", "junior", "senior", "staff",
               "principal", "lead", "intern", "entry", "mid", "associate", "distinguished", "head"}
DIRECT_SOURCES = {"Greenhouse", "Lever", "Ashby"}  # link straight to the employer
TITLE_THRESHOLD = 0.9
COMPANY_THRESHOLD = 0.9


def company_key(name: str) -> str:
    """'Samsara Inc.' and 'samsara' both become 'samsara'."""
    return COMPANY_NOISE.sub("", normalize_words(name)).replace(" ", "").replace(".", "")


def title_key(title: str) -> str:
    """Title without location tags like '(Remote)' or '- US'."""
    return re.sub(r"\s+", " ", TITLE_NOISE.sub(" ", normalize_words(title))).strip(" -,")


def same_title(a: str, b: str) -> bool:
    """Fuzzy title match that never merges different levels (Engineer II vs III)."""
    if a == b:
        return True
    if set(a.split()) & LEVEL_WORDS != set(b.split()) & LEVEL_WORDS:
        return False
    matcher = SequenceMatcher(None, a, b)
    return matcher.quick_ratio() >= TITLE_THRESHOLD and matcher.ratio() >= TITLE_THRESHOLD


def canonical_companies(keys: set[str]) -> dict[str, str]:
    """Map near-identical company keys (e.g. 'samsara' / 'samsarainc') to one key."""
    canon: list[str] = []
    mapping = {}
    for key in sorted(keys, key=len):
        match = next((c for c in canon
                      if SequenceMatcher(None, key, c).ratio() >= COMPANY_THRESHOLD), None)
        if match is None:
            canon.append(key)
            match = key
        mapping[key] = match
    return mapping


def preference(job: dict) -> tuple:
    """Lower sorts first: direct employer link, then has salary, then longer description."""
    return (job["source"] not in DIRECT_SOURCES, not job["salary_min"], -len(job["description"]))


def merge_into(keep: dict, other: dict) -> None:
    """Fill gaps in the kept job with details from its duplicate, and remember where else it was seen."""
    keep.setdefault("_also", []).append(other["source"])
    if not keep.get("_mode_hint") and other.get("_mode_hint"):
        keep["_mode_hint"] = other["_mode_hint"]
    for field in ("salary_min", "salary_max", "date_posted", "location"):
        if not keep[field] and other[field]:
            keep[field] = other[field]
    if not keep["salary_text"] and other["salary_text"]:
        keep["salary_text"] = other["salary_text"]
    if keep["remote_type"] == "unknown":
        keep["remote_type"] = other["remote_type"]


def dedupe(jobs: list[dict]) -> tuple[list[dict], int]:
    """Return (unique jobs, number removed)."""
    by_id: dict[str, dict] = {}
    for job in sorted(jobs, key=preference):
        if job["id"] in by_id:
            merge_into(by_id[job["id"]], job)
        else:
            by_id[job["id"]] = job

    companies = canonical_companies({company_key(j["company"]) for j in by_id.values()})
    buckets: dict[str, list[dict]] = {}
    kept = []
    for job in by_id.values():  # already in preference order
        bucket = buckets.setdefault(companies[company_key(job["company"])], [])
        key = title_key(job["title"])
        twin = next((k for k in bucket
                     if k["source"] != job["source"] and same_title(title_key(k["title"]), key)), None)
        if twin:
            merge_into(twin, job)
        else:
            bucket.append(job)
            kept.append(job)
    return kept, len(jobs) - len(kept)
