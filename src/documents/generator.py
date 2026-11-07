"""Write a resume and cover letter tailored to one job, using the larger local model.

The model may choose, reorder, and rephrase what is in your resume, and use the posting's
wording where it is true. It may not add anything. After it writes, every claim is checked
against your original resume in code:
  - skills that are not on your resume are removed,
  - employers, schools, and projects that are not on your resume are removed,
  - job titles are restored to your real titles,
  - lines containing numbers that are not in your resume (or the posting) are removed.
Each correction is reported, so you can see exactly what was changed and why.
"""
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from typing import Callable

from ..catalog import load_catalog
from ..catalog.builder import contributed_wording
from ..config import load_json, load_models
from ..ollama_client import Ollama, OllamaError
from ..paths import DATA_DIR, PREFERENCES_FILE
from ..platform_text import setup_hint
from ..questionnaire import with_defaults
from ..resume_parser import DATE_RANGE, EXPERIENCE_WORDS, ResumeInfo, split_sections
from ..skills_vocab import find_skills

log = logging.getLogger("jobtracker.documents")
Progress = Callable[[str], None]

JOB_FILES = ["jobs_ranked.json", "jobs_raw.json", "jobs_unsure.json", "jobs_excluded.json"]
RESUME_CHARS = 7000

_str = {"type": "string"}
_list = {"type": "array", "items": _str}
RESUME_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": _str,
        "summary": _str,
        "skills": {"type": "array", "items": {"type": "object", "properties": {"label": _str, "items": _list},
                                              "required": ["label", "items"]}},
        "experience": {"type": "array", "items": {"type": "object", "properties": {
            "title": _str, "company": _str, "location": _str, "dates": _str, "bullets": _list},
            "required": ["title", "company", "location", "dates", "bullets"]}},
        "projects": {"type": "array", "items": {"type": "object", "properties": {
            "name": _str, "tech": _str, "bullets": _list}, "required": ["name", "tech", "bullets"]}},
    },
    "required": ["headline", "summary", "skills", "experience", "projects"],
}
# Factual lists are copied word for word from the resume, never rewritten by the model.
VERBATIM_SECTIONS = ["education", "publications", "certifications", "certificates",
                     "licenses and certifications", "awards", "honors", "honors and awards"]
DEGREE_WORDS = re.compile(r"\b(bachelor|master|associate|doctor|ph\.?d|m\.?s\.?|b\.?s\.?|b\.?a\.?|m\.?b\.?a|"
                          r"diploma|degree)\b", re.I)
MIN_SKILLS = 10
LETTER_SCHEMA = {
    "type": "object",
    "properties": {"greeting": _str, "paragraphs": _list, "closing": _str},
    "required": ["greeting", "paragraphs", "closing"],
}

RULES = """Rules you must follow:
- Use ONLY facts from the candidate's original resume. Never invent or upgrade employers, job
  titles, dates, degrees, schools, certifications, skills, tools, numbers, or results.
- You may choose what to include, reorder it, shorten it, and rephrase it. Where the original
  supports it, use the job posting's own words (for example "REST APIs" if the resume shows API work).
- If the posting asks for something the candidate does not have, leave it out. Do not imply it.
- Plain, confident, specific language. No buzzwords ("passionate", "synergy", "rockstar",
  "results-driven") and no claims the resume does not show ("experienced", "proven ability",
  "at scale", "expert", "seasoned", "drive technical decisions").
- Fix obvious typos and broken words from the original (for example "hands -on" -> "hands-on")."""


class DocumentError(Exception):
    """A plain-language reason documents could not be made."""


@dataclass
class Draft:
    """What the model wrote, after fact checking."""
    resume: dict
    letter: dict
    notes: list[str] = field(default_factory=list)  # corrections made, shown to the user
    model: str = ""


# ---------------------------------------------------------------- inputs

def find_job(job_id: str, data_dir=DATA_DIR) -> dict:
    """The full scraped job (with its description) for a Job ID."""
    for name in JOB_FILES:
        for job in (load_json(data_dir / name, {}) or {}).get("jobs", []):
            if job.get("id") == job_id:
                if not (job.get("description") or "").strip():
                    raise DocumentError("This posting's full text was not downloaded, so documents can't be "
                                        "tailored to it. Open the posting and paste its text into Notes instead.")
                return job
    raise DocumentError("This job is no longer in the saved search results (it may be from an old search). "
                        "Run a refresh, then try again.")


def header_info(resume: ResumeInfo) -> tuple[str, str, str]:
    """(name, headline, contact line) copied exactly from the top of the resume."""
    header = split_sections(resume.text)["header"]
    name = header[0] if header else "Your Name"
    contact = [line for line in header[1:] if re.search(r"@|\||\d{3}.*\d{4}|linkedin|github|\.com|\.io", line, re.I)]
    rest = [line for line in header[1:] if line not in contact]
    return name, (rest[0] if rest else ""), " | ".join(contact)


# ---------------------------------------------------------------- prompts

def relevant_projects(catalog: dict, job: dict, limit: int = 6) -> list[dict]:
    """Catalog projects that best match this job: shared tech first, then the most recent."""
    wanted = {s.lower() for s in find_skills(f"{job['title']}\n{job['description']}")}

    def score(p: dict) -> tuple:
        tech = {t.lower() for t in p.get("tech", []) + p.get("skills_evidence", [])}
        return (len(tech & wanted), p.get("last_commit") or "")
    projects = [p for p in catalog.get("projects", []) if p.get("summary")]
    return sorted(projects, key=score, reverse=True)[:limit]


def catalog_block(catalog: dict, job: dict) -> str:
    """The GitHub part of the candidate's verified experience, for the prompts."""
    projects = relevant_projects(catalog, job)
    if not projects:
        return ""
    lines = ["MORE VERIFIED EXPERIENCE (from the candidate's own GitHub; true, but not on the resume)"]
    skills = [s["name"] for s in catalog.get("skills", [])]
    if skills:
        lines.append(f"Skills shown in their code: {', '.join(skills[:40])}")
    for p in projects:
        years = "-".join(dict.fromkeys(y for y in (p.get('first_commit', '')[:4], p.get('last_commit', '')[:4]) if y))
        lines.append(f"- Project \"{p['name']}\" ({p.get('context', '')}; {p.get('role', '')}; {years}): "
                     f"{p.get('summary', '')}")
        for h in p.get("highlights", [])[:3]:
            lines.append(f"    * {h}")
        if p.get("tech"):
            lines.append(f"    Tech: {', '.join(p['tech'])}")
    return "\n".join(lines)


def resume_prompt(resume: ResumeInfo, prefs: dict, job: dict, max_chars: int,
                  catalog: dict | None = None) -> list[dict]:
    titles = ", ".join(prefs.get("target_titles") or resume.titles[:3])
    extra = catalog_block(catalog or {}, job)
    catalog_skills = [s["name"] for s in (catalog or {}).get("skills", [])]
    system = f"""You are an expert technical resume writer. Rewrite the candidate's resume so it is
the strongest truthful match for ONE job posting.

{RULES}

How to tailor:
- headline: the candidate's own title or one of their target titles ({titles}).
- summary: 2-3 sentences aimed at this job. Every claim must point to something specific in the
  resume (a degree, a role, a result). Do not name the company.
- skills: 3-5 groups with every skill from the resume that is relevant to this job (usually 12-25),
  the ones the posting asks for first, then the candidate's core languages and tools. Only skills
  that appear in the resume or in their GitHub code: {', '.join(list(dict.fromkeys(resume.skills[:60] + catalog_skills)))}
- experience: every position from the resume, newest first, with the exact title, company,
  location, and dates. 2-5 bullets each, most relevant to this job first; older or unrelated
  roles get 1-2 bullets. Keep each bullet's numbers and results (for example "~20%", "3 days to
  2 hours", "8-person team") exactly as written.
- projects: the 1-3 projects most relevant to this job, from the resume or from the GitHub
  projects below (may be empty). Use a GitHub project's name exactly as given, its tech, and 1-3
  bullets taken from its summary and points. If its role says "contributed", write "Contributed
  to ..." rather than claiming the whole project.
- Education, publications, and certifications are added automatically; do not write them.
- Keep it to about two pages: 18 bullets or fewer in total.
Reply with JSON only."""
    user = f"""ORIGINAL RESUME
{resume.text[:RESUME_CHARS]}

{extra}

JOB POSTING
Title: {job['title']}
Company: {job['company']}
Location: {job.get('location', '')}
Description:
{job['description'][:max_chars]}"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def letter_prompt(resume: ResumeInfo, job: dict, tailored: dict, max_chars: int,
                  catalog: dict | None = None) -> list[dict]:
    system = f"""You are an expert career writer. Write a cover letter from the candidate to the
company for ONE job posting.

{RULES}

How to write it:
- 3 or 4 short paragraphs, 250-350 words in total.
- Paragraph 1: the role and one concrete reason the candidate fits it. Do not open with
  "I am writing to express my interest" or "I am confident"; start with something specific.
- Middle: 2-3 specific achievements from the resume that match what the posting asks for,
  connected to the posting's needs. Use the real numbers from the resume where they help.
- Last: a short, confident close. Do not repeat the resume line by line.
- Say only what the posting says about the company; do not invent facts about it.
- greeting: "Dear Hiring Manager," unless the posting names the hiring person.
- closing: "Sincerely," (the name is added automatically).
Reply with JSON only."""
    highlights = "\n".join(f"- {b}" for role in tailored.get("experience", []) + tailored.get("projects", [])
                           for b in role.get("bullets", [])[:3])
    user = f"""CANDIDATE RESUME
{resume.text[:RESUME_CHARS]}

{catalog_block(catalog or {}, job)}

MOST RELEVANT POINTS (already chosen for this job)
{highlights}

JOB POSTING
Title: {job['title']}
Company: {job['company']}
Description:
{job['description'][:max_chars]}"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ---------------------------------------------------------------- fact checking

def plain(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9+#]+", str(text).lower()))


NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in NUMBER.findall(str(text))}


def unknown_numbers(text: str, allowed: set[str]) -> set[str]:
    return numbers_in(text) - allowed


def overlap(candidate: str, source: str) -> float:
    """Share of the candidate's words found in the source text."""
    words = plain(candidate).split()
    return sum(1 for w in words if f" {w} " in f" {source} ") / len(words) if words else 0.0


def catalog_skill_text(catalog: dict) -> str:
    """Skill names proven by code in the catalog (never README tech lists or summaries)."""
    names = [s["name"] for s in catalog.get("skills", [])]
    names += [t for p in catalog.get("projects", []) for t in p.get("skills_evidence", [])]  # code evidence only
    return " | ".join(names)


def catalog_text(catalog: dict) -> str:
    """Everything the catalog says about projects (for project names and numbers)."""
    return "\n".join(f"{p['name']} {p.get('summary', '')} {' '.join(p.get('highlights', []))}"
                     for p in catalog.get("projects", []))


def skill_supported(item: str, resume: ResumeInfo, source: str, extra_skills: set[str] = frozenset()) -> bool:
    """A skill counts if the resume or the catalog's code evidence names it."""
    if plain(item) and f" {plain(item)} " in f" {source} ":
        return True
    have = {s.lower() for s in resume.skills} | extra_skills
    found = find_skills(item)
    return bool(found) and all(f.lower() in have for f in found)


def closest_role(entry: dict, resume: ResumeInfo):
    """The original role this entry describes (by title, else by dates)."""
    best, score = None, 0.0
    for role in resume.roles:
        s = SequenceMatcher(None, plain(entry.get("title", "")), plain(role.title)).ratio()
        if s > score:
            best, score = role, s
    if score >= 0.6:
        return best
    years = set(re.findall(r"(?:19|20)\d{2}", entry.get("dates", "")))
    return next((r for r in resume.roles if {r.start[:4], r.end[:4]} <= years), None)


def check_resume(draft: dict, resume: ResumeInfo, allowed_numbers: set[str],
                 allowed_headlines: list[str], job_text: str = "",
                 catalog: dict | None = None) -> tuple[dict, list[str]]:
    """Remove or repair anything the resume (or the skill catalog) does not support.
    Returns (clean draft, notes). Positions, titles, and dates must come from the resume itself."""
    catalog = catalog or {}
    source = plain(resume.text)
    skill_source = plain(f"{resume.text}\n{catalog_skill_text(catalog)}")
    catalog_skills = {s["name"].lower() for s in catalog.get("skills", [])}
    project_names = [plain(p["name"].replace("_", " ").replace("-", " ")) for p in catalog.get("projects", [])]
    originals = original_bullets(resume) + [h for p in catalog.get("projects", []) for h in p.get("highlights", [])]
    notes: list[str] = []
    out = dict(draft)

    if plain(draft.get("headline", "")) not in {plain(h) for h in allowed_headlines}:
        out["headline"] = allowed_headlines[0] if allowed_headlines else ""
    groups, rejected = [], []
    for group in draft.get("skills", []):
        kept = []
        for item in group.get("items", []):
            if skill_supported(item, resume, skill_source, catalog_skills):
                kept.append(item)
            else:
                rejected.append(item)
                notes.append(f"Left out skill \"{item}\": it is not on your resume or in your skill catalog.")
        if kept:
            groups.append({"label": group.get("label", "Skills"), "items": list(dict.fromkeys(kept))})
    out["skills"] = groups

    summary = polish(drop_sentences(draft.get("summary", ""), allowed_numbers, "Summary", notes), "Summary", notes)
    summary = drop_unsupported_claims(summary, rejected, resume, skill_source, catalog_skills, notes)
    out["summary"] = summary or original_summary(resume)

    out["experience"] = []
    for entry in draft.get("experience", []):
        if overlap(entry.get("company", ""), source) < 0.6:
            notes.append(f"Removed a position at \"{entry.get('company')}\": that employer is not on your resume.")
            continue
        entry = dict(entry)
        if entry.get("location") and overlap(entry["location"], source) < 0.8:
            notes.append(f"Removed the location \"{entry['location']}\" for {entry.get('company')}: "
                         f"it is not on your resume.")
            entry["location"] = ""
        if f" {plain(entry.get('title', ''))} " not in f" {source} ":
            role = closest_role(entry, resume)
            if role is None:
                notes.append(f"Removed the position \"{entry.get('title')}\": that title is not on your resume.")
                continue
            notes.append(f"Restored your real job title \"{role.title}\" (the draft said \"{entry.get('title')}\").")
            entry["title"] = role.title
        role = closest_role(entry, resume)
        years = set(re.findall(r"(?:19|20)\d{2}", entry.get("dates", "")))
        if role and (not years or years - {role.start[:4], role.end[:4]}):
            entry["dates"] = role_dates(role)
            notes.append(f"Restored your real dates for \"{role.title}\": {entry['dates']}.")
        bullets = keep_bullets(entry.get("bullets", []), allowed_numbers, notes)
        entry["bullets"] = restore_metrics(bullets, originals, notes)
        out["experience"].append(entry)
    if not out["experience"] and resume.roles:
        raise DocumentError("The writing model did not keep any of your real positions. Try again.")
    out["experience"] = restore_missing_roles(out["experience"], resume, notes)

    out["projects"] = []
    for project in draft.get("projects", []):
        name = project.get("name", "").replace("_", " ").replace("-", " ")
        in_catalog = any(overlap(name, known) >= 0.8 for known in project_names)
        if overlap(name, source) < 0.5 and not in_catalog:
            notes.append(f"Removed project \"{project.get('name')}\": it is not on your resume or in your skill catalog.")
            continue
        team_project = next((p for p, known in zip(catalog.get("projects", []), project_names)
                             if overlap(name, known) >= 0.8 and p.get("role", "").startswith("contributed")), None)
        if team_project:  # you made part of the commits: every bullet says "Contributed to"
            reworded = [contributed_wording(b) for b in project.get("bullets", [])]
            if reworded != project.get("bullets", []):
                notes.append(f"Worded \"{project['name']}\" as a contribution ({team_project['role']}).")
            project = {**project, "bullets": reworded}
        bullets = keep_bullets(project.get("bullets", []), allowed_numbers, notes)
        out["projects"].append({**project, "bullets": restore_metrics(bullets, originals, notes)})

    top_up_skills(out, resume, job_text, notes, [s["name"] for s in catalog.get("skills", [])])
    out["sections"] = verbatim_sections(resume)
    return out, notes


# ---------------------------------------------------------------- deterministic parts

def section_items(lines: list[str]) -> list[str]:
    """Join lines that a PDF wrapped back into whole items ('- Item' starts a new one)."""
    items: list[str] = []
    for line in lines:
        wrapped_tail = line[:1].islower() or (len(line.split()) <= 3 and not DEGREE_WORDS.search(line))
        continues_bullet = items and items[-1].startswith(("-", "*"))
        starts_new = line.startswith(("-", "*")) or not items or not (continues_bullet or wrapped_tail)
        if starts_new:
            items.append(line)
        else:
            joiner = "" if items[-1].endswith("-") else " "
            items[-1] = items[-1] + joiner + line
    return items


def original_bullets(resume: ResumeInfo) -> list[str]:
    """Every bullet point in the resume, as written (wrapped lines joined)."""
    found = []
    for key, lines in split_sections(resume.text).items():
        if key != "header":
            found += [b.lstrip("-* ").strip() for b in section_items(lines) if b.startswith(("-", "*"))]
    return found


def verbatim_sections(resume: ResumeInfo) -> list[dict]:
    """Education, publications, certifications, ... copied exactly, in resume order."""
    out = []
    for key, lines in split_sections(resume.text).items():
        if key not in VERBATIM_SECTIONS or not lines:
            continue
        items = []
        for item in section_items(lines):
            bullet = item.startswith(("-", "*"))
            text = item.lstrip("-* ").strip() if bullet else item
            items.append({"text": text, "bullet": bullet, "bold": not bullet and bool(DEGREE_WORDS.search(text))})
        out.append({"heading": key.title(), "items": items})
    return out


def restore_metrics(bullets: list[str], originals: list[str], notes: list[str]) -> list[str]:
    """If a rewritten bullet lost the numbers of the original it came from, use the original."""
    out = []
    for bullet in bullets:
        source = max(originals, key=lambda o: overlap(bullet, plain(o)), default="")
        source_numbers = numbers_in(source)
        if source and overlap(bullet, plain(source)) >= 0.5 and source_numbers and not (numbers_in(bullet) & source_numbers):
            notes.append(f"Kept your original wording, which has the numbers: \"{source[:90]}\"")
            out.append(source)
        else:
            out.append(bullet)
    return out


def top_up_skills(out: dict, resume: ResumeInfo, job_text: str, notes: list[str],
                  catalog_skills: list[str] = ()) -> None:
    """If the model listed too few skills, add back the most relevant known skills (resume, then catalog)."""
    listed = {plain(i) for g in out["skills"] for i in g["items"]}
    if len(listed) >= MIN_SKILLS:
        return
    wanted = set(find_skills(job_text))
    known = list(dict.fromkeys(find_skills(resume.text) + list(catalog_skills)))  # no odd phrases
    extra = sorted((s for s in known if plain(s) not in listed), key=lambda s: s not in wanted)
    extra = extra[:MIN_SKILLS + 5 - len(listed)]
    if extra:
        out["skills"].append({"label": "Also experienced with", "items": extra})
        notes.append(f"Added back {len(extra)} skills from your resume and skill catalog that the draft left out.")


MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def role_dates(role, today: date | None = None) -> str:
    """'Jun 2024 - Aug 2024', or '... - Present' for a role that runs to this month."""
    today = today or date.today()
    fmt = lambda ym: f"{MONTH_NAMES[int(ym[5:7]) - 1]} {ym[:4]}"
    end = "Present" if role.end >= f"{today.year}-{today.month:02d}" else fmt(role.end)
    return f"{fmt(role.start)} - {end}"


def drop_sentences(text: str, allowed_numbers: set[str], where: str, notes: list[str]) -> str:
    """Remove sentences that contain numbers not found in the allowed sources."""
    kept = []
    for sentence in re.split(r"(?<=[.!?])\s+", (text or "").strip()):
        bad = unknown_numbers(sentence, allowed_numbers)
        if bad:
            notes.append(f"{where}: removed a sentence with a number not in your resume "
                         f"({', '.join(sorted(bad))}): \"{sentence[:90]}\"")
        elif sentence:
            kept.append(sentence)
    return " ".join(kept)


FILLER = re.compile(r"\b(proven|track record|results[- ]driven|passionate|world[- ]class|synergy|rockstar|"
                    r"ninja|drive impactful|i am confident|i am writing to express|"
                    r"strong (?:collaboration|communication) skills)\b", re.I)
SELF_PRAISE_OPENER = re.compile(r"^(?:an?\s+)?(?:highly\s+)?(?:experienced|seasoned|accomplished)\s+", re.I)


SENIORITY_CLAIM = re.compile(r"\b(senior|staff|principal|lead engineer|expert|seasoned|veteran engineer|"
                             r"\d+\+? years of experience)\b", re.I)


def drop_unsupported_claims(text: str, rejected: list[str], resume: ResumeInfo, skill_source: str,
                            catalog_skills: set[str], notes: list[str]) -> str:
    """Remove summary sentences that claim a skill nothing proves, or a level the resume does not show
    (the skills list is checked item by item; prose has to be checked too)."""
    resume_words = plain(resume.text)
    kept = []
    for sentence in [s for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s]:
        named = [r for r in rejected if plain(r) and f" {plain(r)} " in f" {plain(sentence)} "]
        named += [s for s in find_skills(sentence) if not skill_supported(s, resume, skill_source, catalog_skills)]
        level = [m for m in SENIORITY_CLAIM.findall(sentence) if f" {plain(m)} " not in f" {resume_words} "]
        if named or level:
            reason = ", ".join(dict.fromkeys(named + level))
            notes.append(f"Summary: removed a sentence claiming something your resume doesn't show ({reason}): "
                         f"\"{sentence[:90]}\"")
            continue
        kept.append(sentence)
    return " ".join(kept)


def original_summary(resume: ResumeInfo) -> str:
    """The first two sentences of the resume's own summary, as a safe fallback."""
    for key in ("summary", "professional summary", "profile", "objective", "about me"):
        lines = split_sections(resume.text).get(key)
        if lines:
            text = re.sub(r"(\w) -(\w)", r"\1-\2", " ".join(lines))  # PDF split "hands -on"
            return " ".join(re.split(r"(?<=[.!?])\s+", text)[:2])
    return ""


def role_blocks(resume: ResumeInfo) -> dict[str, dict]:
    """For each resume position: the line under its title (the employer) and its original bullets."""
    lines = [line for key, ls in split_sections(resume.text).items()
             if any(w in key for w in EXPERIENCE_WORDS) for line in ls]
    # Find each position's title line on the raw lines (wrapped-line joining would swallow them).
    starts = []
    for role in resume.roles:
        index = next((i for i, line in enumerate(lines)
                      if role.title and role.title in line and not line.startswith(("-", "*"))), None)
        if index is not None:
            starts.append((index, role))
    starts.sort(key=lambda pair: pair[0])
    blocks: dict[str, dict] = {}
    for n, (index, role) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        segment = lines[index + 1:end]
        employer = DATE_RANGE.sub("", lines[index].replace(role.title, "", 1)).strip(" ,|-–—")
        if not employer:
            employer = next((l for l in segment if not l.startswith(("-", "*")) and not DATE_RANGE.fullmatch(l.strip())), "")
        bullets = [b.lstrip("-* ").strip() for b in section_items(segment) if b.startswith(("-", "*"))]
        dates = DATE_RANGE.search(" ".join(lines[index:index + 3]))
        blocks[role.title] = {"role": role, "employer": employer.split("|")[0].strip(), "bullets": bullets,
                              "dates": dates.group(0) if dates else role_dates(role)}
    return blocks


def restore_missing_roles(experience: list[dict], resume: ResumeInfo, notes: list[str]) -> list[dict]:
    """Every position stays on the resume (a missing one reads as a gap). Newest first."""
    present = {plain(e.get("title", "")) for e in experience}
    for title, block in role_blocks(resume).items():
        if plain(title) in present or not block["employer"]:
            continue
        experience.append({"title": title, "company": block["employer"], "location": "",
                           "dates": block["dates"], "bullets": block["bullets"][:2]})
        notes.append(f"Added back your position \"{title}\", which the draft left out.")
    spans = {plain(r.title): (r.end, r.start) for r in resume.roles}  # most recently ended first
    return sorted(experience, key=lambda e: spans.get(plain(e.get("title", "")), ("", "")), reverse=True)


def polish(text: str, where: str, notes: list[str]) -> str:
    """Remove stock filler the model adds despite instructions (it claims more than the resume shows)."""
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s]
    kept = []
    for i, sentence in enumerate(sentences):
        last_one_left = not kept and i == len(sentences) - 1
        if FILLER.search(sentence) and not last_one_left:
            notes.append(f"{where}: removed a filler sentence: \"{sentence[:90]}\"")
            continue
        kept.append(sentence)
    if kept and SELF_PRAISE_OPENER.match(kept[0]):
        trimmed = SELF_PRAISE_OPENER.sub("", kept[0])
        kept[0] = trimmed[:1].upper() + trimmed[1:]
    return " ".join(kept)


def keep_bullets(bullets: list[str], allowed_numbers: set[str], notes: list[str]) -> list[str]:
    kept = []
    for bullet in bullets:
        bad = unknown_numbers(bullet, allowed_numbers)
        if bad:
            notes.append(f"Removed a line with a number that is not in your resume ({', '.join(sorted(bad))}): "
                         f"\"{bullet[:90]}\"")
        elif bullet.strip():
            kept.append(bullet.strip())
    return kept


def check_letter(draft: dict, allowed_numbers: set[str]) -> tuple[dict, list[str]]:
    """Drop sentences with numbers that are in neither the resume nor the posting."""
    notes, paragraphs = [], []
    for paragraph in draft.get("paragraphs", []):
        kept = polish(drop_sentences(paragraph, allowed_numbers, "Cover letter", notes), "Cover letter", notes)
        if kept:
            paragraphs.append(kept)
    if not paragraphs:
        raise DocumentError("The cover letter came back empty. Try again.")
    greeting = draft.get("greeting") or "Dear Hiring Manager,"
    closing = draft.get("closing") or "Sincerely,"
    return {"greeting": greeting, "paragraphs": paragraphs, "closing": closing}, notes


# ---------------------------------------------------------------- main entry

def generate(job: dict, resume: ResumeInfo, prefs: dict | None = None, ollama: Ollama | None = None,
             catalog: dict | None = None,
             progress: Progress = lambda text: None) -> Draft:
    """Write and fact-check a resume and cover letter for `job`."""
    prefs = with_defaults(prefs if prefs is not None else load_json(PREFERENCES_FILE))
    catalog = load_catalog() if catalog is None else catalog
    models = load_models()
    settings = models["documents"]
    ollama = ollama or Ollama(models["llm"]["host"])
    fallback_note = ""
    try:
        ollama.check(settings["model"])
    except OllamaError as exc:
        if "not downloaded" not in str(exc):
            raise
        ollama.check(models["llm"]["model"])  # the ranking model is always there after setup
        fallback_note = (f"Written with {models['llm']['model']} because {settings['model']} is not downloaded. "
                         f"For better writing, run {setup_hint()} or: ollama pull {settings['model']}")
        settings = {**settings, "model": models["llm"]["model"]}
    options = {"temperature": float(settings["temperature"]), "seed": 7, "num_ctx": int(settings["num_ctx"])}
    limit = int(settings["max_description_chars"])
    keep_alive = settings.get("keep_alive", "5m")

    name, headline, _ = header_info(resume)
    resume_numbers = numbers_in(resume.text) | numbers_in(catalog_text(catalog)) | {str(int(resume.years_experience)),
                                                 str(int(resume.years_tech_experience))}
    letter_numbers = resume_numbers | numbers_in(job["description"])  # the letter may quote the posting
    allowed_headlines = [h for h in [headline, *(prefs.get("target_titles") or []), *resume.titles] if h]

    progress(f"Writing your resume with {settings['model']} (the first one can take a minute to load)…")
    raw = call(ollama, settings["model"], resume_prompt(resume, prefs, job, limit, catalog), RESUME_SCHEMA, options, keep_alive)
    tailored, notes = check_resume(raw, resume, resume_numbers, allowed_headlines,
                                   f"{job['title']}\n{job['description']}", catalog)

    progress("Writing your cover letter…")
    raw_letter = call(ollama, settings["model"], letter_prompt(resume, job, tailored, limit, catalog), LETTER_SCHEMA,
                      options, keep_alive)
    letter, letter_notes = check_letter(raw_letter, letter_numbers)
    log.info("Documents for %s (%s @ %s): %d corrections", job["id"], job["title"], job["company"],
             len(notes) + len(letter_notes))
    return Draft(tailored, letter, ([fallback_note] if fallback_note else []) + notes + letter_notes, settings["model"])


def call(ollama: Ollama, model: str, messages: list[dict], schema: dict, options: dict, keep_alive: str) -> dict:
    """One structured request, retried once if the reply is not usable JSON."""
    for attempt in (1, 2):
        try:
            reply = ollama.chat_json(model, messages, schema, options, keep_alive=keep_alive)
            if isinstance(reply, dict):
                return reply
            raise ValueError("reply is not a JSON object")
        except ValueError as exc:
            log.warning("Document model reply was not usable (attempt %d): %s", attempt, exc)
    raise DocumentError("The writing model returned something unusable twice. Try again; if it keeps "
                        "happening, try a different documents model in config/models.json.")
