"""Turn collected repositories into catalog entries, and merge them into config/skill_catalog.json.

Honesty rules:
  - Only repositories you committed to are included, with your commit share recorded.
  - Skills come from code evidence (languages and dependency files) in repos where you wrote a
    real share of the commits, not from words in a README.
  - Project summaries are drafted by the local model from the README, then fact-checked: numbers
    must appear in the README, most content words must come from the repo itself, listed tech
    must be found in the repo, and team repos are worded as contributions.
  - Re-running keeps your edits: "include" switches, "notes", and any project marked "locked".
"""
import logging
import re
from datetime import datetime
from typing import Callable

from ..config import load_json, load_models, save_json
from ..ollama_client import Ollama, OllamaError
from ..skills_vocab import find_skills
from . import CATALOG_FILE
from .github import Repo

log = logging.getLogger("jobtracker.catalog")

MIN_SHARE, MIN_COMMITS = 0.10, 10   # credit skills only above either threshold
MIN_LANGUAGE_SHARE = 0.05
LANGUAGE_SKILLS = {
    "Python": "Python", "C#": "C#", "C++": "C++", "C": "C", "Java": "Java", "TypeScript": "TypeScript",
    "JavaScript": "JavaScript", "HTML": "HTML", "CSS": "CSS", "SCSS": "CSS", "Shell": "Bash",
    "PowerShell": "PowerShell", "Ruby": "Ruby", "Swift": "Swift", "Go": "Go", "Rust": "Rust", "Kotlin": "Kotlin",
    "PHP": "PHP", "Dockerfile": "Docker", "Jupyter Notebook": "Jupyter Notebooks", "CMake": "CMake",
    "PLpgSQL": "SQL", "TSQL": "SQL", "SQL": "SQL", "MATLAB": "MATLAB", "R": "R", "Dart": "Dart",
}
PACKAGE_SKILLS = [  # (pattern in a dependency file, skill)
    (r"\btorch\b|pytorch", "PyTorch"), (r"tensorflow", "TensorFlow"), (r"\bkeras\b", "Keras"),
    (r"scikit-learn|\bsklearn\b", "scikit-learn"), (r"opencv", "OpenCV"), (r"\bnumpy\b", "NumPy"),
    (r"\bpandas\b", "pandas"), (r"matplotlib", "Matplotlib"), (r"\bflask\b", "Flask"), (r"\bdjango\b", "Django"),
    (r"fastapi", "FastAPI"), (r"streamlit", "Streamlit"), (r"sqlalchemy", "SQLAlchemy"),
    (r"psycopg|asyncpg|npgsql", "PostgreSQL"), (r"pymysql|mysql-connector|mysqlclient", "MySQL"),
    (r"pymongo|mongoose", "MongoDB"), (r"\bredis\b", "Redis"), (r"boto3", "AWS"),
    (r"\btransformers\b", "Hugging Face"), (r"langchain", "LangChain"), (r"\bopenai\b", "OpenAI API"),
    (r"ultralytics", "YOLO (Ultralytics)"), (r"detectron2", "Detectron2"), (r"tensorrt|torch2trt", "TensorRT"),
    (r"\bonnx", "ONNX"), (r"pytest", "pytest"), (r"\"react\"", "React"), (r"\"next\"", "Next.js"),
    (r"\"express\"", "Express"), (r"@angular/", "Angular"), (r"\"vue\"", "Vue"), (r"\"typescript\"", "TypeScript"),
    (r"\"jest\"", "Jest"), (r"\"vite\"", "Vite"), (r"tailwindcss", "Tailwind CSS"),
    (r"Microsoft\.AspNetCore", "ASP.NET Core"), (r"EntityFrameworkCore", "Entity Framework Core"),
    (r"xunit|nunit|mstest", "Unit Testing"), (r"xunit", "xUnit"), (r"testcontainers", "Testcontainers"), (r"Swashbuckle|OpenApi", "OpenAPI/Swagger"),
    (r"\brails\b", "Ruby on Rails"), (r"rspec", "RSpec"), (r"\bsdl2?\b", "SDL"), (r"zeromq|\bzmq\b", "ZeroMQ"),
    (r"pyqt|pyside", "Qt"), (r"tkinter", "Tkinter"), (r"pymupdf|\bfitz\b", "PyMuPDF"), (r"openpyxl", "openpyxl"),
]
FILE_SKILLS = [(r"(^|/)Dockerfile$", "Docker"), (r"docker-compose", "Docker Compose"),
               (r"\.github/workflows/", "GitHub Actions"), (r"CMakeLists\.txt$", "CMake")]
TOOLING_FILE = re.compile(r"(\.github/workflows/|Dockerfile$|docker-compose)", re.I)
STOPWORDS = set("""a an and the of for to in on with by from as at into using use used via its it this that
these those is are was were be been project projects repository repo team developed built build created
create implemented implement contributed contribute integrated integrate designed design added add provided
provide enabled enable set up made make work worked including include includes various multiple new
based system systems application app tool tools""".split())
IRREGULAR_ING = {"built": "building", "led": "leading", "wrote": "writing", "made": "making", "ran": "running",
                 "set": "setting", "took": "taking", "drove": "driving", "began": "beginning", "split": "splitting"}
COURSE = re.compile(r"(?<![A-Za-z])(CSC|CTS|WEB|CIS|CSE|CS)[-_ ]?(\d{3})(?!\d)", re.I)  # "CSC581_GameEngine" too


# ---------------------------------------------------------------- facts from code

def counts_for_skills(repo: Repo) -> bool:
    """Only repos where you wrote a real share of the code are evidence of your skills."""
    return repo.my_commits >= MIN_COMMITS or repo.share >= MIN_SHARE


def code_skills(repo: Repo) -> list[str]:
    """Skills shown by the repo's languages, dependency files, and tooling files."""
    skills = []
    total = sum(repo.languages.values()) or 1
    for language, size in sorted(repo.languages.items(), key=lambda kv: -kv[1]):
        if language in LANGUAGE_SKILLS and size / total >= MIN_LANGUAGE_SHARE:
            skills.append(LANGUAGE_SKILLS[language])
    # Only real dependency files: CI and Docker files mention things ("ubuntu-latest") you didn't use.
    manifest_text = "\n".join(text for path, text in repo.manifests.items() if not TOOLING_FILE.search(path))
    skills += [skill for pattern, skill in PACKAGE_SKILLS if re.search(pattern, manifest_text, re.I)]
    skills += find_skills(manifest_text)
    paths = list(repo.manifests) + repo.files
    skills += [skill for pattern, skill in FILE_SKILLS if any(re.search(pattern, p, re.I) for p in paths)]
    return list(dict.fromkeys(skills))


def context_of(repo: Repo, user: str) -> dict:
    """Where the work happened: personal, coursework, an organization, or someone else's repo."""
    course = COURSE.search(f"{repo.owner} {repo.name}")
    if course:
        kind, label = "coursework", f"Coursework ({course.group(1).upper()} {course.group(2)})"
    elif repo.owner.lower() == user.lower():
        kind, label = "personal", "Personal project"
    else:
        kind, label = "organization", f"Team project in {repo.owner}"
    return {"kind": kind, "label": label}


def role_of(repo: Repo) -> str:
    if repo.share >= 0.5:
        return "built"
    return f"contributed ({repo.my_commits} of {repo.total_commits} commits)"


# ---------------------------------------------------------------- model summaries

SUMMARY_SCHEMA = {"type": "object", "properties": {
    "summary": {"type": "string"}, "highlights": {"type": "array", "items": {"type": "string"}},
    "tech": {"type": "array", "items": {"type": "string"}}}, "required": ["summary", "highlights", "tech"]}


def summary_prompt(repo: Repo, role: str, context: dict, skills: list[str]) -> list[dict]:
    system = """You write short, factual project entries for a developer's skill catalog.
Use ONLY what the repository information below says. Do not invent features, users, results,
numbers, or technologies. If there is little information, write less.
Describe the project itself; never use pronouns (he, she, his, her) for the developer.
- summary: 1-2 sentences: what the project is and what it does.
- highlights: 1-4 short bullets about what was built (start with a verb). If the role says
  "contributed", describe the project's work without claiming the person did all of it.
- tech: languages, frameworks, and tools the project uses.
Reply with JSON only."""
    files = ", ".join(repo.files[:40])
    user = f"""Repository: {repo.full_name}
Context: {context['label']}
Role: {role}
Description: {repo.description or '(none)'}
Detected from code: {', '.join(skills) or '(none)'}
Files: {files}
README:
{repo.readme[:5000] or '(no README)'}"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def plain(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9+#.]+", str(text).lower()))


def check_summary(draft: dict, repo: Repo, skills: list[str]) -> dict:
    """Keep only what the repo backs up: known numbers, grounded wording, tech the repo shows."""
    source = f"{repo.description}\n{repo.readme}\n{' '.join(repo.manifests.values())}"
    allowed_numbers = set(re.findall(r"\d[\d,.]*", source))
    plain_source = plain(source)
    grounding = f"{plain_source} {plain(' '.join(repo.files))} {plain(repo.name.replace('_', ' ').replace('-', ' '))}"

    def numbers_ok(text: str) -> bool:
        return all(n in allowed_numbers for n in re.findall(r"\d[\d,.]*", text.rstrip(".")))

    def grounded(text: str) -> bool:
        """Most of the sentence's content words must come from the repo itself."""
        words = [w.strip(".") for w in plain(text).split() if w.strip(".") not in STOPWORDS and len(w) > 2]
        return not words or sum(1 for w in words if w in grounding) / len(words) >= 0.6

    highlights = [h.strip() for h in draft.get("highlights", [])
                  if h.strip() and numbers_ok(h) and grounded(h)][:4]
    if repo.share < 0.5:  # a team repo: never claim the whole project
        highlights = [contributed_wording(h) for h in highlights]
    highlights = [h[:1].upper() + h[1:] for h in highlights]
    known = {s.lower() for s in skills}
    tech = [t.strip() for t in draft.get("tech", []) if t.strip() and (
        t.lower() in known or f" {plain(t)} " in f" {plain_source} " or set(find_skills(t)) & set(skills))]
    summary = draft.get("summary", "").strip()
    if (not summary or not numbers_ok(summary) or not grounded(summary)
            or re.search(r"\b(he|she|his|her)\b", summary, re.I)):
        summary = fallback_summary(repo, skills)["summary"]
    return {"summary": summary, "highlights": highlights, "tech": list(dict.fromkeys(tech))}


def gerund(verb: str) -> str | None:
    """'Integrated' -> 'integrating', 'Built' -> 'building'; None if it is not a past-tense verb."""
    word = verb.lower()
    if word in IRREGULAR_ING:
        return IRREGULAR_ING[word]
    if word.endswith("ied"):
        return word[:-3] + "ying"
    if word.endswith("ed") and len(word) > 3:
        return word[:-2] + "ing"  # created -> creating, optimized -> optimizing, added -> adding
    return None


def contributed_wording(highlight: str) -> str:
    """'Built an inventory tracker' -> 'Contributed to building an inventory tracker' (team repos)."""
    if re.match(r"^contribut", highlight, re.I):
        return highlight
    first, _, rest = highlight.partition(" ")
    ing = gerund(first)
    if not (ing and rest):
        return f"Contributed to: {highlight[:1].lower() + highlight[1:]}"
    pair = re.match(r"and (\w+)\s+(.*)", rest)  # "adapted and enhanced X" -> "adapting and enhancing X"
    if pair and gerund(pair.group(1)):
        rest = f"and {gerund(pair.group(1))} {pair.group(2)}"
    return f"Contributed to {ing} {rest}"


def fallback_summary(repo: Repo, skills: list[str]) -> dict:
    """Used when there is nothing for the model to read, or the model is unavailable."""
    text = repo.description or f"{repo.name.replace('-', ' ').replace('_', ' ')} repository"
    return {"summary": text.rstrip(".") + ".", "highlights": [], "tech": skills}


# ---------------------------------------------------------------- building the catalog

def evidence_skills(repo: Repo, skills: list[str]) -> list[str]:
    """Skills this repo proves you have. In repos you mostly wrote, your own README and description
    count too (you wrote them); in team repos only code counts."""
    if not counts_for_skills(repo):
        return []
    if repo.share >= 0.5:
        skills = list(dict.fromkeys(skills + find_skills(f"{repo.description}\n{repo.readme}")))
    return skills


def worth_including(repo: Repo) -> bool:
    """Off by default (you can switch it on): nothing to describe, or only a token contribution."""
    nothing_to_read = not repo.readme.strip() and len(repo.description) < 20 and not repo.languages
    token_contribution = repo.my_commits <= 2 and repo.total_commits >= 5
    return not (nothing_to_read or token_contribution)


def is_profile_repo(full_name: str) -> bool:
    """github.com/NAME/NAME is the profile page README, not a project."""
    owner, _, name = full_name.partition("/")
    return owner.lower() == name.lower()


def project_entry(repo: Repo, user: str, ollama: Ollama | None, model: str | None) -> dict:
    """One catalog project, summarized and fact-checked."""
    skills = code_skills(repo)
    context, role = context_of(repo, user), role_of(repo)
    texts = fallback_summary(repo, skills)
    if ollama and model and (repo.readme.strip() or len(repo.description) > 30):
        try:
            draft = ollama.chat_json(model, summary_prompt(repo, role, context, skills), SUMMARY_SCHEMA,
                                     {"temperature": 0.2, "seed": 7, "num_ctx": 8192}, keep_alive="5m")
            texts = check_summary(draft, repo, skills)
            if not texts["tech"]:
                texts["tech"] = skills
        except (ValueError, OllamaError) as exc:
            log.warning("Summary for %s failed, using the description: %s", repo.full_name, exc)
    return {
        "id": repo.full_name, "name": repo.name, "url": repo.url, "private": repo.private, "fork": repo.fork,
        "include": worth_including(repo), "context": context["label"], "kind": context["kind"], "role": role,
        "commits_by_you": repo.my_commits, "total_commits": repo.total_commits,
        "first_commit": repo.first_commit, "last_commit": repo.last_commit, "pushed_at": repo.pushed_at,
        "skills_evidence": evidence_skills(repo, skills),
        **texts, "notes": "",
    }


def skills_from(projects: list[dict]) -> list[dict]:
    """Catalog skills with the projects that prove them and when they were used."""
    by_skill: dict[str, dict] = {}
    for p in projects:
        for skill in p.get("skills_evidence", []):
            entry = by_skill.setdefault(skill, {"name": skill, "projects": [], "first_used": "", "last_used": "",
                                                "include": True})
            entry["projects"].append(p["id"])
            if p["first_commit"] and (not entry["first_used"] or p["first_commit"] < entry["first_used"]):
                entry["first_used"] = p["first_commit"]
            entry["last_used"] = max(entry["last_used"], p["last_commit"] or "")
    return sorted(by_skill.values(), key=lambda s: (-len(s["projects"]), s["name"].lower()))


def merge(old: dict, fresh: list[dict]) -> list[dict]:
    """Keep your switches and notes; keep locked or unchanged projects as they were."""
    previous = {p["id"]: p for p in old.get("projects", [])}
    merged = []
    for project in fresh:
        before = previous.pop(project["id"], None)
        if before and (before.get("locked") or before.get("pushed_at") == project["pushed_at"]):
            merged.append({**project, **{k: before[k] for k in ("summary", "highlights", "tech", "notes",
                                                                 "include", "locked") if k in before}})
        elif before:
            merged.append({**project, "include": before.get("include", True), "notes": before.get("notes", "")})
        else:
            merged.append(project)
    merged += [p for p in previous.values() if p.get("locked")]  # hand-added or locked entries stay
    return sorted(merged, key=lambda p: p.get("last_commit") or "", reverse=True)


def build_catalog(repos: list[Repo], user: str, use_model: bool = True,
                  progress: Callable[[str], None] = lambda s: None, path=CATALOG_FILE) -> dict:
    """Summarize changed repos, merge with the existing catalog, and save it."""
    old = load_json(path, {}) or {}
    unchanged = {p["id"]: p for p in old.get("projects", []) if p.get("pushed_at")}
    ollama, model = None, None
    if use_model:
        settings = load_models()
        ollama, model = Ollama(settings["llm"]["host"]), settings["documents"]["model"]
        try:
            ollama.check(model)
        except OllamaError:
            model = settings["llm"]["model"]
            ollama.check(model)
    repos = [r for r in repos if not is_profile_repo(r.full_name)]
    fresh = []
    for i, repo in enumerate(repos, 1):
        same = unchanged.get(repo.full_name, {}).get("pushed_at") == repo.pushed_at
        progress(f"[{i}/{len(repos)}] {repo.full_name}" + (" (unchanged)" if same else ""))
        fresh.append(project_entry(repo, user, None if same else ollama, model))
    projects = merge(old, fresh)
    old_skills = {s["name"]: s for s in old.get("skills", [])}
    skills = [{**s, "include": old_skills.get(s["name"], {}).get("include", True)} for s in skills_from(projects)]
    catalog = {
        "about": ("Your skill catalog, collected from GitHub. It is used next to your resume when ranking jobs "
                  "and writing tailored documents. Edit freely: set \"include\": false to leave a project or "
                  "skill out, fix any summary, and set \"locked\": true on a project so re-collecting never "
                  "changes it. Only projects you committed to are listed."),
        "collected_at": datetime.now().isoformat(timespec="seconds"),
        "sources": [{"type": "github", "user": user, "repositories": len(repos)}],
        "skills": skills,
        "projects": projects,
    }
    save_json(path, catalog)
    return catalog
