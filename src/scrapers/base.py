"""Shared building blocks for every job source: the Source plug-in base class,
a polite HTTP client, and helpers that turn raw postings into the common schema.

Job schema (every scraper returns dicts with exactly these keys):
  id, title, company, location, remote_type, salary_min, salary_max, salary_text,
  description, url, source, date_posted, date_scraped,
  remote_us, us_eligible, location_note   (filled in by location.classify in the runner)
"""
import hashlib
import html
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Callable, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

log = logging.getLogger("jobtracker.scrapers")

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "LocalJobTracker/1.0 (personal job search; respects robots and API terms)")
REMOTE_WORDS = re.compile(r"\b(remote|anywhere|work from home|wfh|distributed|worldwide)\b", re.I)
HYBRID_WORDS = re.compile(r"\bhybrid\b", re.I)


# ---------------------------------------------------------------- plug-in base

REGISTRY: dict[str, type["Source"]] = {}


@dataclass
class Context:
    """What a source needs while fetching: HTTP access, its settings, and filters."""
    http: "Http"
    settings: dict
    keywords: list[str]
    wants_title: Callable[[str], bool]
    days_back: int = 14
    api_key: dict = field(default_factory=dict)       # this source's entry in config/api_keys.json
    detail_cache: dict = field(default_factory=dict)  # raw detail payloads saved by the last run
    details_used: dict = field(default_factory=dict)  # payloads used this run (saved for next run)

    def detail(self, key: str, fetch: Callable[[], dict]) -> tuple[dict, bool]:
        """Return (raw payload, was_downloaded) for one job's detail request, reusing last run's copy.

        Raw payloads are cached rather than parsed jobs, so parser fixes apply to cached jobs too.
        """
        key = str(key)
        downloaded = key not in self.detail_cache
        payload = fetch() if downloaded else self.detail_cache[key]
        self.details_used[key] = payload
        return payload, downloaded


class Source:
    """Base class for a job source. Subclass it in a new file to add a source.

    Set `key` (used in sources.json) and `label` (shown in status output), then
    implement fetch() to yield dicts built with make_job().
    """
    key = ""
    label = ""
    enabled_by_default = True
    min_refresh_hours = 0.0  # reuse the last results if fetched more recently than this
    remote_board = False     # True if the site only lists remote jobs
    needs_key = False        # True if the source needs a free key in config/api_keys.json

    def __init__(self):
        self.warnings: list[str] = []  # non-fatal problems, shown in the status output

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.key:
            REGISTRY[cls.key] = cls

    def fetch(self, ctx: Context) -> Iterable[dict]:
        """Yield jobs in the common schema."""
        raise NotImplementedError


# ---------------------------------------------------------------- HTTP

class SourceError(Exception):
    """A request failed after all retries."""


class _HostLimiter:
    """Keeps a minimum gap between requests to the same host, across threads."""

    def __init__(self):
        self._lock = threading.Lock()
        self._next_ok: dict[str, float] = {}

    def wait(self, host: str, delay: float) -> None:
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next_ok.get(host, 0.0))
            self._next_ok[host] = start + delay
        if start > now:
            time.sleep(start - now)


LIMITER = _HostLimiter()


class Http:
    """requests.Session with a real User-Agent, per-host delays, and retry with backoff."""

    RETRY_STATUS = {429, 500, 502, 503, 504}

    def __init__(self, delay: float = 1.5, retries: int = 3, timeout: float = 30):
        self.delay, self.retries, self.timeout = delay, retries, timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en"})

    def request(self, method: str, url: str, headers: dict | None = None, **kwargs) -> requests.Response:
        """Send a request with retries. Raises SourceError with a readable message on failure."""
        host = urlsplit(url).netloc
        last = ""
        for attempt in range(self.retries + 1):
            LIMITER.wait(host, self.delay)
            try:
                resp = self.session.request(method, url, headers=headers, timeout=self.timeout, **kwargs)
            except requests.RequestException as exc:
                last = f"network error: {exc.__class__.__name__}"
            else:
                if resp.status_code == 200:
                    return resp
                last = f"HTTP {resp.status_code}"
                if resp.status_code in (401, 403):
                    last += " (access denied: check the API key, if this source uses one)"
                if resp.status_code not in self.RETRY_STATUS:
                    break
                retry_after = resp.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    time.sleep(min(int(retry_after), 60))
            if attempt < self.retries:
                wait = 2 ** (attempt + 1)
                log.info("Retrying %s in %ss (%s)", url, wait, last)
                time.sleep(wait)
        raise SourceError(f"{last} from {host}")

    def get(self, url: str, headers: dict | None = None, **params) -> requests.Response:
        """GET with query parameters."""
        return self.request("GET", url, headers=headers, params=params or None)

    def json(self, url: str, headers: dict | None = None, **params):
        """GET and decode JSON."""
        return self._decode(self.get(url, headers=headers, **params), url)

    def post_json(self, url: str, payload: dict, headers: dict | None = None):
        """POST a JSON body and decode the JSON reply."""
        return self._decode(self.request("POST", url, headers=headers, json=payload), url)

    def text(self, url: str, **params) -> str:
        """GET and return the body as text."""
        return self.get(url, **params).text

    @staticmethod
    def _decode(resp: requests.Response, url: str):
        try:
            return resp.json()
        except ValueError as exc:
            raise SourceError(f"{urlsplit(url).netloc} did not return JSON") from exc


# ---------------------------------------------------------------- text helpers

class _TextExtractor(HTMLParser):
    """Turns HTML into readable plain text with line breaks and bullets."""
    BLOCK = {"p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "ul", "ol", "section"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in self.BLOCK or tag == "li":
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(markup: str | None) -> str:
    """Plain text from HTML (handles HTML that was escaped twice, as Greenhouse sends it)."""
    if not markup:
        return ""
    if "&lt;" in markup and "<" not in markup.replace("&lt;", ""):
        markup = html.unescape(markup)
    parser = _TextExtractor()
    parser.feed(markup)
    parser.close()
    text = "".join(parser.parts).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


_MOJIBAKE = re.compile("[ÃÂâ][\u0080-¿‐-›€Œ-ž]")


def fix_text(text: str | None) -> str:
    """Repair UTF-8 text that was decoded as Windows-1252 somewhere upstream ('SothebyÃ¢Â€Â™s')."""
    if not text or not _MOJIBAKE.search(text):
        return text or ""
    for codec in ("cp1252", "latin-1"):
        try:
            return text.encode(codec).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return text


def normalize_words(text: str) -> str:
    """Lowercase, unify spellings like back-end/backend, drop punctuation."""
    text = text.lower()
    text = re.sub(r"\bback[\s-]+end\b", "backend", text)
    text = re.sub(r"\bfront[\s-]+end\b", "frontend", text)
    text = re.sub(r"\bfull[\s-]*stack\b", "fullstack", text)
    text = re.sub(r"\bsr\b\.?", "senior", text)
    text = re.sub(r"\bjr\b\.?", "junior", text)
    text = re.sub(r"[^a-z0-9+#.]+", " ", text)
    return re.sub(r"\s+", " ", text).strip(" .")


def title_matcher(keywords: list[str]) -> Callable[[str], bool]:
    """Return a function that is True when a title contains all words of any keyword."""
    keyword_sets = [set(normalize_words(k).split()) for k in keywords if k.strip()]
    keyword_sets = [k for k in keyword_sets if k]

    def wants(title: str) -> bool:
        if not keyword_sets:
            return True
        words = set(normalize_words(title).split())
        return any(k <= words for k in keyword_sets)
    return wants


# ---------------------------------------------------------------- field helpers

_AMOUNT = r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*([kK])?"
SALARY_RANGE = re.compile(
    rf"\$\s?{_AMOUNT}\s*(?:USD)?\s*(?:-|–|—|to)\s*\$?\s?{_AMOUNT}(?=(?P<tail>.{{0,30}}))", re.S)
PERIODS = [(re.compile(r"\b(hour|hr|hourly)\b", re.I), 2080),
           (re.compile(r"\b(month|mo|monthly)\b", re.I), 12),
           (re.compile(r"\b(week|weekly)\b", re.I), 52)]
# Dollar ranges right after these words are not base salary.
NOT_SALARY = re.compile(r"equity|stock|rsus?\b|bonus|refresh|sign-?on|signing|commission|"
                        r"stipend|reimburse|allowance|budget|401", re.I)
MIN_YEARLY = 15_000  # below this a "yearly" figure is almost certainly a mislabeled period


def _amount(number: str, k: str | None) -> float:
    value = float(number.replace(",", ""))
    return value * 1000 if k else value


def parse_salary(text: str | None) -> tuple[int | None, int | None]:
    """Find a US dollar range like '$120k - $150k' or '$50-$65/hour' and return yearly ints."""
    if not text:
        return None, None
    for match in SALARY_RANGE.finditer(text):
        before = re.split(r"[.;\n]\s", text[max(0, match.start() - 50):match.start()])[-1]
        if NOT_SALARY.search(before):  # only look back within the same sentence
            continue
        low = _amount(match[1], match[2])
        high = _amount(match[3], match[4] or match[2])  # "$120k-150" means 150k
        multiplier = next((m for pattern, m in PERIODS if pattern.search(match["tail"])), 1)
        low, high = low * multiplier, high * multiplier
        if 20_000 <= low <= high <= 1_000_000:
            return int(low), int(high)
    return None, None


def annualize(amount, period: str | None) -> int | None:
    """Convert an amount paid per hour/month/week to a yearly figure."""
    if not amount:
        return None
    period = (period or "year").lower()
    factor = 2080 if "hour" in period else 12 if "month" in period else 52 if "week" in period else 1
    return int(float(amount) * factor)


def format_salary(low: int | None, high: int | None) -> str:
    """'$120K-$150K' style text, matching the reference workbook."""
    if not low and not high:
        return ""
    fmt = lambda v: f"${round(v / 1000)}K"
    if low and high and low != high:
        return f"{fmt(low)}-{fmt(high)}"
    return fmt(low or high)


def infer_remote_type(*texts: str, default: str = "unknown") -> str:
    """'remote', 'hybrid', 'onsite', or `default` from location or description text."""
    joined = " ".join(t for t in texts if t)
    if HYBRID_WORDS.search(joined):
        return "hybrid"
    if REMOTE_WORDS.search(joined):
        return "remote"
    return default


def to_date(value) -> str | None:
    """ISO date (YYYY-MM-DD) from epoch seconds/ms, ISO strings, or RSS dates."""
    if value in (None, "", 0):
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            seconds = float(value) / (1000 if float(value) > 1e11 else 1)
            return datetime.fromtimestamp(seconds, tz=timezone.utc).date().isoformat()
        text = str(value).strip()
        if re.match(r"\d{4}-\d{2}-\d{2}", text):
            return text[:10]
        return parsedate_to_datetime(text).date().isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def clean_url(url: str) -> str:
    """Drop tracking parameters (utm_*, ref) so the same job always hashes the same."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith(("utm_", "ref"))]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def job_id(company: str, title: str, url: str) -> str:
    """Stable id: hash of company + title + url."""
    raw = f"{company.strip().lower()}|{title.strip().lower()}|{url.strip().lower()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def make_job(*, title: str, company: str, url: str, source: str, location: str = "",
             remote_type: str | None = None, description: str = "", salary_min=None,
             salary_max=None, salary_text: str = "", date_posted=None,
             mode_hint: str | None = None) -> dict:
    """Build a job dict in the common schema, filling in salary and remote type if possible.

    mode_hint: 'remote' | 'hybrid' | 'onsite' when the source states it in a structured field.
    """
    title, company, url = fix_text(title).strip(), fix_text(company).strip(), clean_url(url or "")
    location, description = fix_text(location), fix_text(description)
    if max(salary_min or 0, salary_max or 0) < MIN_YEARLY:
        salary_min = salary_max = None
    if not salary_min and not salary_max:
        salary_min, salary_max = parse_salary(salary_text) if salary_text else (None, None)
    if not salary_min and not salary_max:
        salary_min, salary_max = parse_salary(description[:20000])
    salary_text = (salary_text or "").strip() or format_salary(salary_min, salary_max)
    return {
        "id": job_id(company, title, url),
        "title": title,
        "company": company,
        "location": (location or "").strip(),
        "remote_type": remote_type or infer_remote_type(location, title),
        "salary_min": int(salary_min) if salary_min else None,
        "salary_max": int(salary_max) if salary_max else None,
        "salary_text": salary_text,
        "description": description.strip(),
        "url": url,
        "source": source,
        "date_posted": to_date(date_posted),
        "date_scraped": datetime.now().isoformat(timespec="seconds"),
        "remote_us": None,
        "us_eligible": None,
        "location_note": "",
        "_mode_hint": mode_hint if mode_hint in ("remote", "hybrid", "onsite") else None,
    }


def is_recent(job: dict, cutoff: date) -> bool:
    """True if the job was posted on/after cutoff, or has no date (kept to be safe)."""
    return not job["date_posted"] or job["date_posted"] >= cutoff.isoformat()
