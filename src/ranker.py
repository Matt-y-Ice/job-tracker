"""Rank jobs on this computer. No paid or remote APIs: embeddings and the LLM run in Ollama.

Steps for every job in jobs_raw.json and jobs_unsure.json:
  1. Hard filters (instant): deal breaker in the title, pay below your minimum. Flagged jobs
     get score 0 with the reason and skip the LLM. They are kept, not deleted.
  2. Embedding similarity of the job to your resume and target titles (0-100).
  3. LLM review with the resume, preferences, and job description (JSON only, validated,
     retried once if malformed).
  4. Final score = weighted mix of LLM, embedding, salary fit, and keyword scores
     (weights in preferences.json -> score_weights).
Jobs in jobs_excluded.json (not remote US) get score 0 with their location reason.
Results go to data/jobs_ranked.json. Reviews are cached in data/cache.db, so later runs
only score new jobs unless the resume, preferences, or model changed.
"""
import argparse
import logging
import math
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .cache import Cache, digest
from .catalog import load_catalog
from .config import load_json, load_models, save_json
from .ollama_client import Ollama, OllamaError
from .paths import DATA_DIR, PREFERENCES_FILE
from .questionnaire import with_defaults
from .resume_parser import ResumeError, ResumeInfo, parse_resume
from .scrapers.runner import EXCLUDED_FILE, RAW_FILE, UNSURE_FILE
from .skills_vocab import find_skills

log = logging.getLogger("jobtracker.ranker")

RANKED_FILE = DATA_DIR / "jobs_ranked.json"
PROMPT_VERSION = 5  # bump when the prompt changes so cached reviews are redone
RESUME_CHARS = 8000
EMBED_JOB_CHARS = 5000
# Cosine similarity ranges of nomic-embed-text, measured on ~200 live postings (5th to 95th
# percentile was 0.59-0.70 for resume vs. posting, 0.63-0.77 for target titles vs. job title).
# Each range is stretched to 0-100 so the score actually separates jobs.
RESUME_SIM = (0.55, 0.75)
TITLE_SIM = (0.60, 0.82)

RUBRIC = {"skill": 40, "experience": 20, "role_fit": 20, "pay": 10, "constraints": 10}
REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "breakdown": {"type": "object",
                      "properties": {k: {"type": "integer", "minimum": 0, "maximum": top}
                                     for k, top in RUBRIC.items()},
                      "required": list(RUBRIC)},
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "reasons": {"type": "array", "items": {"type": "string"}},
        "missing_skills": {"type": "array", "items": {"type": "string"}},
        "red_flags": {"type": "array", "items": {"type": "string"}},
        "seniority_match": {"type": "string", "enum": ["under", "match", "over"]},
        "deal_breakers_hit": {"type": "array", "items": {
            "type": "object", "properties": {"deal_breaker": {"type": "string"},
                                             "evidence": {"type": "string"}},
            "required": ["deal_breaker", "evidence"]}},
    },
    "required": ["breakdown", "score", "reasons", "missing_skills", "red_flags",
                 "seniority_match", "deal_breakers_hit"],
}

# Title patterns for common deal-breaker phrases. Narrow on purpose: "AI/ML development"
# should catch "ML Engineer" or "Data Scientist", not "AI Platform Engineer".
TITLE_PATTERNS = {
    "ml": r"\bml\b|machine learning|deep learning|mlops|data scien|applied scien",
    "ai": r"\bai engineer|\bai/ml\b|\bml/ai\b|\bai research|research engineer,? ai\b|"
          r"artificial intelligence engineer|\bllm engineer|\bgen ?ai engineer",
    "frontend": r"front[- ]?end|\bui engineer",
    "mobile": r"\bios\b|android|mobile",
    "management": r"\bmanager\b|\bdirector\b|head of|\bvp\b",
    "sales": r"\bsales\b|account executive",
    "contract": r"\bcontract\b|contractor|freelance",
    "crypto": r"crypto|blockchain|web3",
}
GENERIC = {"development", "developer", "role", "roles", "job", "jobs", "work", "engineering",
           "engineer", "position", "positions", "related", "heavy", "focused", "based", "required"}

Report = Callable[[str], None]


@dataclass
class RankResult:
    """Summary of a ranking run."""
    jobs: list[dict]
    new: int = 0
    cached: int = 0
    flagged: int = 0
    failed: int = 0
    processor: str = "unknown"
    model: str = ""
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- loading

def load_buckets() -> list[dict]:
    """All scraped jobs, each tagged with bucket = main | unsure | excluded."""
    jobs = []
    for path, bucket in ((RAW_FILE, "main"), (UNSURE_FILE, "unsure"), (EXCLUDED_FILE, "excluded")):
        data = load_json(path, {}) or {}
        jobs += [{**job, "bucket": bucket} for job in data.get("jobs", [])]
    if not any(j["bucket"] != "excluded" for j in jobs):
        raise FileNotFoundError(f"No scraped jobs found in {RAW_FILE.parent}. Run the scrape step first.")
    return jobs


# ---------------------------------------------------------------- hard filters

def deal_breaker_patterns(phrase: str) -> list[re.Pattern]:
    """Title regexes for one deal-breaker phrase like 'AI/ML development' or 'PHP'."""
    patterns = []
    for piece in re.split(r"[/,&+]|\band\b|\bor\b", phrase.lower()):
        words = [w for w in re.findall(r"[a-z0-9#+.-]+", piece) if w not in GENERIC]
        term = " ".join(words)
        if not term:
            continue
        known = TITLE_PATTERNS.get(term)
        patterns.append(re.compile(known or rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", re.I))
    return patterns


def hard_filter(job: dict, prefs: dict) -> list[str]:
    """Reasons this job is flagged before any model runs (empty list = passes)."""
    if job["bucket"] == "excluded":
        return [f"Not remote US. {job.get('location_note', '')}".strip()]
    reasons = []
    for phrase in prefs.get("deal_breakers", []):
        if any(p.search(job["title"]) for p in deal_breaker_patterns(phrase)):
            reasons.append(f"Deal breaker in title: '{phrase}'")
    minimum = prefs.get("min_salary") or 0
    if minimum and job.get("salary_max") and job["salary_max"] < minimum:
        reasons.append(f"Pays up to ${job['salary_max']:,}, below your ${minimum:,} minimum")
    return reasons


# ---------------------------------------------------------------- scores that need no LLM

def salary_score(job: dict, minimum: int) -> float:
    """0-100. Unknown pay is neutral (50); pay well above your minimum is 100."""
    top = job.get("salary_max") or job.get("salary_min")
    if not minimum or not top:
        return 50.0
    if top < minimum:
        return 0.0
    return min(100.0, 60 + 40 * (top - minimum) / (0.25 * minimum))


def keyword_score(job: dict, resume_skills: list[str], must_haves: list[str]) -> tuple[float, list[str]]:
    """0-100 from must-have coverage and how much of the job's tech stack is on the resume."""
    text = f"{job['title']}\n{job['description']}"
    job_skills = set(find_skills(text))
    have = {s.lower() for s in resume_skills}
    overlap = len([s for s in job_skills if s.lower() in have]) / len(job_skills) if job_skills else 0.5
    matched = [m for m in must_haves
               if m.lower() in {s.lower() for s in job_skills}
               or re.search(rf"(?<![\w+#]){re.escape(m)}(?![\w+#])", text, re.I)]
    if not must_haves:
        return round(overlap * 100, 1), matched
    # Most jobs use one or two main languages, so matching two must-haves counts as full coverage.
    coverage = min(1.0, len(matched) / min(2, len(must_haves)))
    return round((0.5 * overlap + 0.5 * coverage) * 100, 1), matched


LEVELS = ["intern", "entry", "mid", "senior", "lead", "manager"]
LEVEL_NAMES = {"intern": "Intern", "entry": "Entry-level", "mid": "Mid-level", "senior": "Senior",
               "lead": "Staff/lead", "manager": "Manager"}
LEVEL_WORDS = [  # checked in order; the first match decides the title's level
    ("manager", r"\b(?:manager|director|vp|vice president|head of)\b"),
    ("lead", r"\b(?:staff|principal|distinguished|lead|architect|chief|fellow)\b|\b(?:iv|v|vi|[4-9])\b"),
    ("senior", r"\b(?:senior|sr)\b|\b(?:iii|3)\b"),
    ("intern", r"\b(?:intern|internship|co-?op)\b"),
    ("entry", r"\b(?:junior|jr|entry|associate|new grad|graduate|early career|apprentice)\b|\b(?:i|1)\b"),
    ("mid", r"\b(?:mid|intermediate)\b|\b(?:ii|2)\b"),
]


def title_level(title: str) -> str | None:
    """Seniority implied by a job title, or None if the title does not say."""
    text = title.lower().replace("-", " ")
    return next((level for level, pattern in LEVEL_WORDS if re.search(pattern, text)), None)


def level_penalty(title: str, wanted: list[str], llm_seniority: str | None) -> tuple[int, str]:
    """Points taken off the final score when the job's level is outside the levels you want.

    Uses the title first ('Staff', 'Senior', 'II'); if the title does not say, uses the LLM's
    seniority_match. Returns (points, note for red flags).
    """
    wanted = [w for w in wanted if w in LEVELS]
    if not wanted:
        return 0, ""
    level = title_level(title)
    if level is None or level in wanted:
        if level is None and llm_seniority == "over":
            return 5, "Posting asks for more experience than you have"
        return 0, ""
    if level == "manager":
        return 20, "Management role; you did not ask for manager roles"
    rank, top, bottom = LEVELS.index(level), max(map(LEVELS.index, wanted)), min(map(LEVELS.index, wanted))
    if rank > top:
        gap = rank - top
        return (10 if gap == 1 else 20), f"{LEVEL_NAMES[level]} title is above the levels you want"
    return 5 * (bottom - rank), f"{LEVEL_NAMES[level]} title is below the levels you want"


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def similarity_score(sim: float, low_high: tuple[float, float]) -> float:
    """Map cosine similarity within a measured range to 0-100."""
    low, high = low_high
    return round(max(0.0, min(1.0, (sim - low) / (high - low))) * 100, 1)


def final_score(parts: dict[str, float | None], weights: dict[str, float]) -> int:
    """Weighted mix of the available parts (a failed LLM review is left out, not counted as 0)."""
    usable = {k: v for k, v in parts.items() if v is not None and weights.get(k, 0) > 0}
    total = sum(weights[k] for k in usable)
    return round(sum(weights[k] * v for k, v in usable.items()) / total) if total else 0


# ---------------------------------------------------------------- embeddings

class Embedder:
    """Embeds text with Ollama, caching every vector in cache.db."""

    def __init__(self, ollama: Ollama, cache: Cache, model: str):
        self.ollama, self.cache, self.model = ollama, cache, model

    def vectors(self, texts: list[str]) -> list[list[float]]:
        found = {t: self.cache.get_vector(t, self.model) for t in set(texts)}
        missing = [t for t, v in found.items() if v is None]
        for i in range(0, len(missing), 16):
            batch = missing[i:i + 16]
            for text, vec in zip(batch, self.ollama.embed(self.model, batch)):
                self.cache.put_vector(text, self.model, vec)
                found[text] = vec
        return [found[t] for t in texts]


def embedding_scores(embedder: Embedder, jobs: list[dict], resume: ResumeInfo,
                     titles: list[str]) -> dict[str, float]:
    """0-100 per job: 70% similarity to the resume, 30% title similarity to target titles."""
    resume_vec, titles_vec = embedder.vectors([
        f"search_query: {resume.text[:RESUME_CHARS]}",
        f"search_query: {', '.join(titles) or 'software engineer'}"])
    docs = [f"search_document: {j['title']}\n{j['description'][:EMBED_JOB_CHARS]}" for j in jobs]
    heads = [f"search_document: {j['title']}" for j in jobs]
    doc_vecs, head_vecs = embedder.vectors(docs), embedder.vectors(heads)
    return {job["id"]: round(0.7 * similarity_score(cosine(resume_vec, d), RESUME_SIM)
                             + 0.3 * similarity_score(cosine(titles_vec, h), TITLE_SIM), 1)
            for job, d, h in zip(jobs, doc_vecs, head_vecs)}


# ---------------------------------------------------------------- LLM review

def system_prompt(resume: ResumeInfo, prefs: dict, catalog: dict | None = None) -> str:
    """Candidate and preferences. Identical for every job, so Ollama can reuse it."""
    salary = f"${prefs['min_salary']:,}" if prefs.get("min_salary") else "no minimum"
    years = (f"{resume.years_tech_experience} years in tech roles (compare this to the years a job "
             f"asks for); {resume.years_experience} years of work experience in total")
    return f"""You are an experienced technical recruiter. Judge how well ONE job fits ONE candidate.
Be strict and realistic; do not inflate scores. Base every claim on the posting text.

Score with this rubric. Every part is a whole number from 0 up to its maximum, never negative.
Put each part in "breakdown" and their sum in "score".
- skill (0-40): share of the job's required tech stack the candidate has used. 40 = nearly all,
  20 = about half, 0 = almost none. If the job uses none of the must-have skills/languages
  below as a main language, give at most 20.
- experience (0-20): 20 = the years/level asked for fit the candidate's TECH experience. Subtract 5 for each
  year the requirement is above the candidate's tech experience. Internships, co-ops, and
  research count as real experience.
- role_fit (0-20): 20 = the role is one of the target titles; lower the further it drifts.
- pay (0-10): 10 = listed pay reaches the minimum salary; 5 = pay is not listed;
  0-4 = listed pay is below the minimum or barely reaches it.
- constraints (0-10): start at 10. Subtract 2-5 for each real issue the posting states:
  travel, on-call, security clearance, relocation, contract-only, location limits. Stop at 0.
  Name every issue you subtract for in red_flags.

Other fields:
- reasons: 2-4 short, specific sentences on why it fits or does not.
- missing_skills: skills the JOB asks for that do NOT appear anywhere in the candidate's resume.
  Never list a skill the resume already shows. Empty list if nothing is missing.
- red_flags: concrete concerns stated in the posting. Empty list if none.
- seniority_match: "under" = job is more junior than the candidate; "match" = fits;
  "over" = job asks for more years or a higher level than the candidate has.
- deal_breakers_hit: deal breakers from the list below that THIS ROLE clearly violates. For each,
  give "deal_breaker" (copied exactly from the list) and "evidence" (an exact quote from the
  posting's duties or requirements that proves it). It counts only if the person hired would
  spend much of their time on it, or the posting requires it. These do NOT count: what the
  company or its products do, mission statements, using AI coding tools, or "nice to have"
  mentions. Example: for "AI/ML development", a backend engineer at an AI company who builds
  APIs is NOT a hit; a role that trains models or builds ML pipelines IS. Missing salary is
  never a deal breaker. Most jobs have an empty list here.

CANDIDATE
Experience: {years}
Detected skills: {', '.join(resume.skills[:60])}{github_lines(catalog or {})}
Resume:
{resume.text[:RESUME_CHARS]}

PREFERENCES
Target titles: {', '.join(prefs.get('target_titles', [])) or 'not specified'}
Seniority levels wanted: {', '.join(prefs.get('seniority', [])) or 'any'}
Must-have skills/languages (the job should use at least one as a main tool): {', '.join(prefs.get('must_have_skills', [])) or 'none'}
Deal breakers: {'; '.join(prefs.get('deal_breakers', [])) or 'none'}
Minimum salary: {salary}
Company size: {', '.join(prefs.get('company_size', [])) or 'any'}

Reply with JSON only."""


def job_prompt(job: dict, max_chars: int) -> str:
    """The job being judged."""
    pay = job.get("salary_text") or "not listed"
    return f"""JOB
Title: {job['title']}
Company: {job['company']}
Location: {job['location'] or 'not listed'}
Work arrangement check: {job.get('location_note', '')}
Salary: {pay}
Posted: {job.get('date_posted') or 'unknown'}
Description:
{job['description'][:max_chars]}"""


def _string_list(value, limit: int) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("expected a list")
    return [str(v).strip() for v in value if str(v).strip()][:limit]


def _plain(text: str) -> str:
    """Lowercase letters and digits only, single-spaced, for forgiving quote matching."""
    return " ".join(re.findall(r"[a-z0-9+#]+", text.lower()))


RELATED_WORDS = {  # loose matches used to check that a quote is about the deal breaker at all
    "ai": ["ai", "artificial intelligence", "llm", "llms", "agent", "agents", "genai", "gpt", "model", "models"],
    "ml": ["ml", "machine learning", "deep learning", "model", "models", "training", "inference"],
    "on-call": ["on-call", "on call", "oncall", "pager"],
}


def related_terms(phrase: str) -> list[str]:
    """Words that a quote proving this deal breaker should contain."""
    terms = []
    for piece in re.split(r"[/,&+]|\band\b|\bor\b", phrase.lower()):
        words = [w for w in re.findall(r"[a-z0-9#+.-]+", piece) if w not in GENERIC]
        for word in words:
            terms += RELATED_WORDS.get(word, [word.rstrip("s")])
        if " ".join(words) in RELATED_WORDS:
            terms += RELATED_WORDS[" ".join(words)]
    return terms


def verified_hits(raw, deal_breakers: list[str], posting: str) -> list[tuple[str, str]]:
    """(deal breaker, quote) pairs the model claimed that hold up: the name is one of the user's
    deal breakers, the quote really is in the posting, and the quote is about that topic."""
    if not isinstance(raw, list):
        raise ValueError("deal_breakers_hit should be a list")
    text, hits = _plain(posting), []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("deal_breaker", "")).strip()
        evidence = str(item.get("evidence", "")).strip()
        quote = _plain(evidence)
        match = next((d for d in deal_breakers if name and (name.lower() in d.lower()
                                                            or d.lower() in name.lower())), None)
        on_topic = match and any(re.search(rf"(?<![a-z0-9]){re.escape(_plain(t))}", quote)
                                 for t in related_terms(match))
        if match and len(quote) >= 12 and quote in text and on_topic:
            hits.append((match, evidence[:160]))
        else:
            log.info("Ignored unverified deal-breaker claim %r", item)
    return hits


CONFIRM_SCHEMA = {"type": "object", "properties": {"main_work": {"type": "boolean"},
                                                   "mandatory_condition": {"type": "boolean"},
                                                   "why": {"type": "string"}},
                  "required": ["main_work", "mandatory_condition", "why"]}


def confirm_hit(ollama: Ollama, settings: dict, job: dict, deal_breaker: str, quote: str) -> bool:
    """Second, narrow question for a claimed deal breaker. Small models judge this far better
    on its own than inside the full review."""
    question = f"""A job seeker refuses jobs that involve: "{deal_breaker}".
Job title: {job['title']} at {job['company']}
Quote from the posting: "{quote}"

Answer two questions about this quote:
main_work: Is "{deal_breaker}" the main thing this person would build or do day to day?
  False if it is one skill among many, about using tools (for example AI coding assistants or
  "AI-assisted" processes), or describes the company or its products.
mandatory_condition: Does the quote make "{deal_breaker}" a required condition of taking the job
  (for example "must travel", "ability to travel twice a year", "required on-call rotation")?
  False if it is optional, preferred, or only possible.
JSON only."""
    # Same num_ctx as the main review: a different value makes Ollama reload the whole model.
    options = {"temperature": 0, "seed": 42, "num_ctx": int(settings.get("num_ctx", 8192))}
    try:
        reply = ollama.chat_json(settings["model"], [{"role": "user", "content": question}],
                                 CONFIRM_SCHEMA, options)
        log.info("Deal-breaker check for %s / %s: %s", job["id"], deal_breaker, reply)
        return bool(reply.get("main_work")) or bool(reply.get("mandatory_condition"))
    except (ValueError, TypeError):
        return True  # cannot double-check: keep the claim, it already passed the quote checks


def not_on_resume(skills: list[str], resume: ResumeInfo | None) -> list[str]:
    """Drop 'missing' skills that the resume actually shows (small models mix these up)."""
    if resume is None:
        return skills
    have = {s.lower() for s in resume.skills}
    return [s for s in skills if s.lower() not in have
            and not re.search(rf"(?<![\w+#]){re.escape(s)}(?![\w+#])", resume.text, re.I)]


def validate_review(data: dict, deal_breakers: list[str], posting: str = "",
                    resume: ResumeInfo | None = None) -> dict:
    """Check and clean the model's JSON. Raises ValueError if it is unusable."""
    if not isinstance(data, dict):
        raise ValueError("reply is not a JSON object")
    breakdown = data.get("breakdown")
    if not isinstance(breakdown, dict):
        raise ValueError("missing breakdown")
    parts = {}
    for key, top in RUBRIC.items():
        value = int(float(breakdown[key])) if key in breakdown else None
        if value is None or not -top <= value <= 2 * top:
            raise ValueError(f"breakdown.{key}={value} is missing or far outside 0-{top}")
        parts[key] = max(0, min(top, value))  # e.g. "-5 for travel" means 0
    seniority = str(data.get("seniority_match", "")).strip().lower()
    if seniority not in ("under", "match", "over"):
        raise ValueError(f"seniority_match '{seniority}' is not under/match/over")
    hits = verified_hits(data.get("deal_breakers_hit", []), deal_breakers, posting)
    return {
        "score": sum(parts.values()),  # the sum is more reliable than the model's own total
        "breakdown": parts,
        "reasons": _string_list(data.get("reasons"), 4),
        "missing_skills": not_on_resume(_string_list(data.get("missing_skills"), 8), resume),
        "red_flags": _string_list(data.get("red_flags"), 5),
        "seniority_match": seniority,
        "deal_breakers_hit": hits,
    }


def llm_review(ollama: Ollama, settings: dict, system: str, job: dict,
               deal_breakers: list[str], resume: ResumeInfo | None = None) -> dict | None:
    """Ask the LLM about one job. Retries once if the reply is malformed; None if it fails twice."""
    limit = int(settings["max_description_chars"])
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": job_prompt(job, limit)}]
    options = {"temperature": float(settings.get("temperature", 0.1)), "seed": 42,
               "num_ctx": int(settings.get("num_ctx", 8192))}
    posting = f"{job['title']}\n{job['description'][:limit]}"
    for attempt in (1, 2):
        try:
            data = ollama.chat_json(settings["model"], messages, REVIEW_SCHEMA, options)
            review = validate_review(data, deal_breakers, posting, resume)
            break
        except (ValueError, TypeError) as exc:
            log.warning("LLM reply for %s was malformed (attempt %d): %s", job["id"], attempt, exc)
            messages = messages[:2] + [{"role": "user", "content":
                                        f"Your last reply was invalid ({exc}). Reply again with "
                                        f"JSON only, following the schema exactly."}]
    else:
        return None
    confirmed = []
    for name, quote in review["deal_breakers_hit"]:
        if confirm_hit(ollama, settings, job, name, quote):
            confirmed.append(f"{name}: \"{quote}\"")
        else:
            review["red_flags"].append(f"Mentions {name}, but not as a main duty: \"{quote[:80]}\"")
    review["deal_breakers_hit"] = confirmed
    return review


# ---------------------------------------------------------------- main ranking

def scoring_prefs(prefs: dict) -> dict:
    """The preferences that change an LLM review. Weights and days_back are not included."""
    return {k: prefs.get(k) for k in ("min_salary", "target_titles", "must_have_skills",
                                      "deal_breakers", "seniority", "company_size")}


def github_lines(catalog: dict) -> str:
    """Extra candidate facts from the skill catalog (GitHub), for the ranking prompt."""
    if not catalog.get("projects"):
        return ""
    skills = ", ".join(s["name"] for s in catalog.get("skills", [])[:40])
    projects = "; ".join(f"{p['name']} ({', '.join(p.get('tech', [])[:5])}; {p.get('role', '')})"
                         for p in catalog["projects"][:12])
    return f"\nSkills shown in their GitHub code: {skills}\nGitHub projects: {projects}"


def catalog_hash(catalog: dict) -> str:
    """Changes when the catalog's included skills or projects change ('' when there is none)."""
    if not catalog.get("projects"):
        return ""
    return digest([s["name"] for s in catalog.get("skills", [])],
                  [(p["id"], p.get("summary"), p.get("tech")) for p in catalog["projects"]])


def review_context(resume: ResumeInfo, prefs: dict, model: str, catalog_digest: str = "") -> str:
    """Hash of everything that changes an LLM review."""
    parts = [resume.text_hash, scoring_prefs(prefs), model, PROMPT_VERSION]
    if catalog_digest:
        parts.append(catalog_digest)
    return digest(*parts)


def explain_changes(cache: Cache, resume: ResumeInfo, prefs_hash: str, model: str,
                    catalog_digest: str = "") -> list[str]:
    """Plain-language notes on why cached reviews will be redone."""
    notes = []
    checks = [("resume_hash", resume.text_hash, "Your resume changed"),
              ("catalog_hash", catalog_digest, "Your skill catalog changed"),
              ("prefs_hash", prefs_hash, "Your preferences changed"),
              ("llm_model", model, f"The model changed to {model}"),
              ("prompt_version", str(PROMPT_VERSION), "The ranking prompt was updated")]
    for key, value, message in checks:
        old = cache.get_meta(key)
        if old is not None and old != value:
            notes.append(f"{message}, so every job will be scored again.")
        cache.set_meta(key, value)
    return notes


def rank(resume: ResumeInfo | None = None, prefs: dict | None = None, model: str | None = None,
         rescore_all: bool = False, limit: int | None = None, report: Report = print,
         progress_bar: bool = True, cache: Cache | None = None, ollama: Ollama | None = None,
         progress_desc: str = "Ranking", catalog: dict | None = None) -> RankResult:
    """Rank every scraped job and write data/jobs_ranked.json."""
    resume = resume or parse_resume()
    prefs = with_defaults(prefs if prefs is not None else load_json(PREFERENCES_FILE))
    settings = load_models()
    llm = dict(settings["llm"], model=model or settings["llm"]["model"])
    embed_model = settings["embedding"]["model"]
    ollama = ollama or Ollama(llm["host"])
    ollama.check(llm["model"], embed_model)
    cache = cache or Cache()

    catalog = load_catalog() if catalog is None else catalog
    catalog_digest = catalog_hash(catalog)
    context = review_context(resume, prefs, llm["model"], catalog_digest)
    result = RankResult(jobs=[], model=llm["model"])
    result.notes = explain_changes(cache, resume, digest(scoring_prefs(prefs)), llm["model"], catalog_digest)
    for note in result.notes:
        report(note)

    jobs = load_buckets()
    for job in jobs:
        job["flag_reasons"] = hard_filter(job, prefs)
    candidates = [j for j in jobs if not j["flag_reasons"]]

    embed = embedding_scores(Embedder(ollama, cache, embed_model), candidates, resume,
                             prefs.get("target_titles") or resume.titles)
    cached, todo = {}, []
    for job in candidates:
        review = None if rescore_all else cache.get_review(job["id"], context)
        if review:
            cached[job["id"]] = review
        else:
            todo.append(job)
    # Best-looking jobs first, so an interrupted run has scored the ones that matter most.
    todo.sort(key=lambda j: (j["bucket"] != "main", -embed[j["id"]]))
    if limit is not None:
        todo = todo[:limit]
    result.new, result.cached = len(todo), len(cached)
    excluded = sum(1 for j in jobs if j["bucket"] == "excluded")
    filtered = len(jobs) - len(candidates) - excluded
    estimate = f" ({about(len(todo) * seconds_per_job(cache, llm['model']))})" if todo else ""
    report(f"{len(todo)} new jobs to review{estimate}, {len(cached)} cached, {filtered} caught by "
           f"deal-breaker/salary filters, {excluded} not remote US | model {llm['model']}")

    reviews = dict(cached)
    if todo:
        system = system_prompt(resume, prefs, catalog)
        started = time.monotonic()
        reviews.update(review_jobs(ollama, llm, system, todo, prefs, cache, context, progress_bar,
                                   resume, progress_desc))
        cache.set_meta(f"seconds_per_job:{llm['model']}", f"{(time.monotonic() - started) / len(todo):.2f}")
        result.processor = ollama.processor(llm["model"])

    catalog_skills = [s["name"] for s in catalog.get("skills", [])]
    result.jobs = [score_job(job, embed, reviews, resume, prefs, catalog_skills) for job in jobs]
    result.flagged = sum(1 for j in result.jobs if j["flagged"])
    result.failed = sum(1 for j in todo if j["id"] not in reviews)
    write_ranked(result, prefs, embed_model)
    cache.close()
    return result


def seconds_per_job(cache: Cache, model: str) -> float:
    """Measured speed from the last run, or a first guess from the hardware."""
    measured = cache.get_meta(f"seconds_per_job:{model}")
    if measured:
        return float(measured)
    from .system_check import gpu_report
    return 6.0 if gpu_report(model)[0] == "GPU" else 45.0


def about(seconds: float) -> str:
    """'about 25 min' style estimate."""
    if seconds < 90:
        return f"about {max(1, round(seconds))} seconds"
    if seconds < 90 * 60:
        return f"about {round(seconds / 60)} min"
    return f"about {int(seconds // 3600)} h {round(seconds % 3600 / 60)} min"


def review_jobs(ollama: Ollama, llm: dict, system: str, todo: list[dict], prefs: dict,
                cache: Cache, context: str, progress_bar: bool,
                resume: ResumeInfo | None = None, desc: str = "Ranking") -> dict[str, dict]:
    """Run the LLM over `todo`, caching each review as soon as it is done."""
    from tqdm import tqdm

    reviews = {}
    bar = tqdm(todo, desc=desc, unit="job", disable=not progress_bar, leave=False, mininterval=0.5,
               bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]")
    for job in bar:
        started = time.monotonic()
        review = llm_review(ollama, llm, system, job, prefs.get("deal_breakers", []), resume)
        if review:
            cache.put_review(job["id"], context, review)
            reviews[job["id"]] = {**review, "scored_at": datetime.now().isoformat(timespec="seconds")}
        log.info("Reviewed %s (%s @ %s) in %.1fs: %s", job["id"], job["title"], job["company"],
                 time.monotonic() - started, review["score"] if review else "FAILED")
    return reviews


def score_job(job: dict, embed: dict, reviews: dict, resume: ResumeInfo, prefs: dict,
              extra_skills: list[str] = ()) -> dict:
    """Attach every score and the final verdict to one job."""
    review = reviews.get(job["id"]) or {}
    flags = list(job["flag_reasons"])
    if review.get("deal_breakers_hit"):
        flags.append("Deal breaker (LLM): " + ", ".join(review["deal_breakers_hit"]))
    keywords, matched = (keyword_score(job, list(resume.skills) + list(extra_skills), prefs.get("must_have_skills", []))
                         if not job["flag_reasons"] else (None, []))
    parts = {
        "llm": review.get("score"),
        "embedding": embed.get(job["id"]),
        "salary": salary_score(job, prefs.get("min_salary") or 0) if not job["flag_reasons"] else None,
        "keywords": keywords,
    }
    penalty, level_note = level_penalty(job["title"], prefs.get("seniority", []),
                                        review.get("seniority_match"))
    if level_note:
        review.setdefault("red_flags", []).append(level_note)
    job.update({
        "score": 0 if flags else max(0, final_score(parts, prefs["score_weights"]) - penalty),
        "level_penalty": penalty,
        "llm_score": parts["llm"],
        "embedding_score": parts["embedding"],
        "salary_score": None if parts["salary"] is None else round(parts["salary"], 1),
        "keyword_score": parts["keywords"],
        "score_breakdown": review.get("breakdown"),
        "reasons": review.get("reasons", []),
        "missing_skills": review.get("missing_skills", []),
        "red_flags": review.get("red_flags", []),
        "seniority_match": review.get("seniority_match"),
        "matched_must_haves": matched,
        "flagged": bool(flags),
        "flag_reasons": flags,
        "llm_status": ("skipped (flagged)" if job["flag_reasons"] else
                       "ok" if review else "failed or not yet reviewed"),
        "scored_at": review.get("scored_at"),
    })
    return job


def write_ranked(result: RankResult, prefs: dict, embed_model: str) -> None:
    """Write data/jobs_ranked.json, best first within each bucket."""
    order = {"main": 0, "unsure": 1, "excluded": 2}
    result.jobs.sort(key=lambda j: (order[j["bucket"]], -j["score"]))
    counts = {b: sum(1 for j in result.jobs if j["bucket"] == b) for b in order}
    save_json(RANKED_FILE, {
        "ranked_at": datetime.now().isoformat(timespec="seconds"),
        "model": result.model,
        "embedding_model": embed_model,
        "weights": prefs["score_weights"],
        "counts": {**counts, "flagged": result.flagged, "llm_failed": result.failed},
        "jobs": result.jobs,
    })


def summary_line(result: RankResult) -> str:
    """'61 new jobs, 9 cached | model qwen2.5:14b on GPU'"""
    where = f"on {result.processor}" if result.new else "(nothing new to review)"
    return f"{result.new} new jobs, {result.cached} cached | model {result.model} {where}"


def main(argv: list[str] | None = None) -> int:
    """python -m src.ranker [--resume FILE] [--prefs FILE] [--model NAME] [--rescore-all] [--limit N]"""
    from .log import setup_logging

    parser = argparse.ArgumentParser(description="Rank scraped jobs with local models.")
    parser.add_argument("--resume", type=Path, help="resume file (default: the one in resume/)")
    parser.add_argument("--prefs", type=Path, help="preferences JSON (default: config/preferences.json)")
    parser.add_argument("--model", help="Ollama model to use instead of models.json")
    parser.add_argument("--rescore-all", action="store_true", help="ignore cached reviews")
    parser.add_argument("--limit", type=int, help="review at most N new jobs (for testing)")
    args = parser.parse_args(argv)
    setup_logging()
    try:
        resume = parse_resume(args.resume)
        prefs = load_json(args.prefs) if args.prefs else load_json(PREFERENCES_FILE)
        if prefs is None:
            print("No preferences yet. Run the questionnaire first:  python -m src.questionnaire")
            return 1
        result = rank(resume, prefs, args.model, args.rescore_all, args.limit)
    except (ResumeError, OllamaError, FileNotFoundError) as exc:
        print(f"Ranking problem: {exc}")
        return 1
    top = [j for j in result.jobs if j["bucket"] == "main" and j["score"] >= 50]
    print(summary_line(result))
    print(f"{len(top)} remote US jobs score 50+. Results: {RANKED_FILE}")
    if result.failed:
        print(f"{result.failed} jobs could not be reviewed by the LLM; they will be retried next run.")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace", line_buffering=True)
    sys.exit(main())
