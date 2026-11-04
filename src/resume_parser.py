"""Find the resume in /resume, extract its text, and pull out skills,
job titles, and years of experience."""
import hashlib
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from .paths import RESUME_DIR
from .skills_vocab import find_skills, known_alias_keys

SUPPORTED = {".pdf", ".docx", ".txt"}

HEADINGS = {
    "summary", "professional summary", "profile", "objective", "about me",
    "experience", "work experience", "professional experience", "relevant experience",
    "employment", "employment history", "work history", "career history",
    "research experience", "military experience", "industry experience",
    "skills", "technical skills", "core competencies", "technologies", "tech stack",
    "skills and tools", "tools and technologies", "key skills",
    "education", "projects", "personal projects", "selected projects", "academic projects",
    "certifications", "certificates", "licenses and certifications", "publications",
    "awards", "honors", "honors and awards", "volunteer", "volunteer experience",
    "leadership", "activities", "interests", "references", "languages",
}
EXPERIENCE_WORDS = ("experience", "employment", "work history", "career history")
SKILL_HEADINGS = {
    "skills", "technical skills", "core competencies", "technologies", "tech stack",
    "skills and tools", "tools and technologies", "key skills",
}

_MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
    r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
)
_DATE = rf"(?:{_MONTH}\s+(?:\d{{1,2}},\s*)?\d{{4}}|\d{{1,2}}/\d{{4}}|(?:19|20)\d{{2}})"
_END = rf"(?:{_DATE}|Present|Current|Now|Today|Ongoing)"
DATE_RANGE = re.compile(
    rf"(?P<start>{_DATE})\s*(?:-|–|—|to|until|through|thru)\s*(?P<end>{_END})",
    re.IGNORECASE,
)
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

TITLE_WORDS = re.compile(
    r"\b(engineer|developer|programmer|analyst|scientist|researcher|architect|manager|"
    r"specialist|consultant|administrator|designer|technician|intern|co-?op|lead|director|"
    r"assistant|associate|officer|coordinator|instructor|teacher|tutor|fellow|sre|devops)\b",
    re.IGNORECASE,
)
TECH_TITLE = re.compile(
    r"software|developer|programmer|devops|\bdata\b|machine learning|\bml\b|\bsre\b|web|"
    r"full.?stack|back.?end|front.?end|\bit\b|computer|systems engineer|cloud|security",
    re.IGNORECASE,
)
STATED_YEARS = re.compile(
    r"(\d{1,2})\+?\s*(?:years|yrs)\.?\s+(?:of\s+)?(?:professional\s+|industry\s+|"
    r"hands-on\s+|relevant\s+)?experience",
    re.IGNORECASE,
)


class ResumeError(Exception):
    """A problem the user needs to fix with their resume file."""


@dataclass
class Role:
    """One dated position from the experience section."""
    title: str
    start: str
    end: str
    months: int
    is_tech: bool


@dataclass
class ResumeInfo:
    """Everything the rest of the pipeline needs from the resume."""
    path: str
    text: str
    text_hash: str
    skills: list[str] = field(default_factory=list)
    titles: list[str] = field(default_factory=list)
    roles: list[Role] = field(default_factory=list)
    years_experience: float = 0.0
    years_tech_experience: float = 0.0
    stated_years: int | None = None

    def to_dict(self) -> dict:
        """Plain dict for JSON output."""
        return asdict(self)


# ---------------------------------------------------------------- finding the file

def find_resume(folder: Path = RESUME_DIR) -> Path:
    """Return the single resume file in `folder`, or raise a clear ResumeError."""
    if not folder.exists():
        raise ResumeError(f"The resume folder is missing. Create {folder} and put your resume in it.")
    files = [p for p in folder.iterdir()
             if p.is_file() and not p.name.startswith((".", "~$"))]
    resumes = [p for p in files if p.suffix.lower() in SUPPORTED]
    if not resumes:
        others = ", ".join(p.name for p in files)
        hint = f" Found {others}, but only PDF, DOCX, and TXT are supported." if others else ""
        raise ResumeError(f"No resume found in {folder}.{hint} Put ONE resume file "
                          f"(PDF, DOCX, or TXT) in that folder and run again.")
    if len(resumes) > 1:
        names = ", ".join(sorted(p.name for p in resumes))
        raise ResumeError(f"Found {len(resumes)} resumes in {folder} ({names}). "
                          f"Keep only ONE resume in that folder and run again.")
    return resumes[0]


# ---------------------------------------------------------------- text extraction

def read_pdf(path: Path) -> str:
    """Extract text from every page of a PDF."""
    from pypdf import PdfReader
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            reader.decrypt("")
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        raise ResumeError(f"Could not read {path.name} as a PDF ({exc}). "
                          f"Try saving it again from Word or Google Docs as PDF or DOCX.") from exc


def read_docx(path: Path) -> str:
    """Extract text from a DOCX, including tables and text boxes, in document order."""
    from docx import Document
    from docx.oxml.ns import qn
    try:
        body = Document(str(path)).element.body
    except Exception as exc:
        raise ResumeError(f"Could not open {path.name} as a Word document ({exc}). "
                          f"If it is an old .doc file, open it in Word and save as .docx.") from exc
    fallback_tag = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
    lines = []
    for para in body.iter(qn("w:p")):
        if any(anc.tag == fallback_tag for anc in para.iterancestors()):
            continue  # duplicate copy of a text box kept for old Word versions
        breaks = {qn("w:tab"): "\t", qn("w:br"): "\n"}
        parts = [breaks.get(node.tag, node.text or "")
                 for node in para.iter(qn("w:t"), qn("w:tab"), qn("w:br"))]
        lines.append("".join(parts))
    return "\n".join(lines)


def read_txt(path: Path) -> str:
    """Read a plain text file, trying common encodings."""
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise ResumeError(f"Could not read {path.name}. Save it as UTF-8 text.")


def extract_text(path: Path) -> str:
    """Return cleaned plain text from a PDF, DOCX, or TXT resume."""
    readers = {".pdf": read_pdf, ".docx": read_docx, ".txt": read_txt}
    text = clean_text(readers[path.suffix.lower()](path))
    if len(text) < 200:
        raise ResumeError(
            f"{path.name} has almost no readable text ({len(text)} characters). "
            f"If it is a scanned image, export your resume as DOCX or a text-based PDF instead."
        )
    return text


def clean_text(text: str) -> str:
    """Normalize whitespace and odd characters left behind by PDF extraction."""
    text = text.replace("\r", "\n").replace(" ", " ").replace("�", "-")
    text = re.sub(r"[•●▪◦■►]", "-", text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


# ---------------------------------------------------------------- sections

def heading_key(line: str) -> str | None:
    """Return the normalized heading name if `line` is a section heading."""
    if not line[:1].isupper() or line.rstrip().endswith((".", ",", ";")):
        return None  # e.g. the wrapped tail of a bullet: "projects."
    norm = re.sub(r"[^a-z& ]", "", line.lower().replace("&", "and")).strip()
    norm = re.sub(r"\s+", " ", norm)
    if norm in HEADINGS:
        return norm
    words = line.split()
    if line.isupper() and len(words) <= 4 and any(h in norm for h in HEADINGS):
        return norm
    return None


def split_sections(text: str) -> dict[str, list[str]]:
    """Group lines under their section heading. Text before the first heading is 'header'."""
    sections: dict[str, list[str]] = {"header": []}
    current = "header"
    for line in text.split("\n"):
        key = heading_key(line) if line else None
        if key:
            current = key
            sections.setdefault(current, [])
        elif line:
            sections[current].append(line)
    return sections


# ---------------------------------------------------------------- skills

def harvest_skill_list(lines: list[str]) -> list[str]:
    """Pull items out of a 'Label: a, b, c' style skills section."""
    entries: list[str] = []
    for line in lines:
        if ":" in line or not entries:
            entries.append(line)
        else:
            entries[-1] += " " + line  # PDF wrapped a long line
    items = []
    for entry in entries:
        entry = entry.split(":", 1)[1] if ":" in entry else entry
        entry = re.sub(r"\([^)]*\)", "", entry)
        for item in re.split(r"[,;|]| - ", entry):
            item = item.strip(" .-")
            if item and len(item) <= 40 and len(item.split()) <= 4:
                items.append(item)
    return items


def extract_skills(text: str, sections: dict[str, list[str]]) -> list[str]:
    """Known skills found anywhere, plus extra items listed in the skills section."""
    skills = find_skills(text)
    seen = {s.lower() for s in skills} | known_alias_keys()
    for name in SKILL_HEADINGS & sections.keys():
        for item in harvest_skill_list(sections[name]):
            if item.lower() not in seen:
                seen.add(item.lower())
                skills.append(item)
    return skills


# ---------------------------------------------------------------- experience

def parse_date(value: str, today: date) -> tuple[int, int] | None:
    """Turn a resume date string into (year, month). Returns None if unparseable."""
    value = value.strip().lower().rstrip(".")
    if value in {"present", "current", "now", "today", "ongoing"}:
        return today.year, today.month
    if m := re.fullmatch(r"(\d{1,2})/(\d{4})", value):
        return int(m[2]), max(1, min(12, int(m[1])))
    if m := re.fullmatch(r"([a-z]+)\.?\s+(?:\d{1,2},\s*)?(\d{4})", value):
        month = MONTHS.get(m[1][:3])
        return (int(m[2]), month) if month else None
    if re.fullmatch(r"\d{4}", value):
        return int(value), 1
    return None


def month_index(ym: tuple[int, int]) -> int:
    """Months since year 0, for easy arithmetic."""
    return ym[0] * 12 + ym[1] - 1


def guess_title(line: str, match: re.Match, previous: list[str]) -> str:
    """Pick the job title from the date line itself, or the lines just above it."""
    before = line[: match.start()].strip(" ,|-–—")
    after = line[match.end():].strip(" ,|-–—")
    previous = [p for p in previous if not p.startswith(("-", "*"))]
    for chunk in [before, after, *previous]:
        for part in re.split(r"\s*(?:\||–|—| - |,| at | @ )\s*", chunk):
            if TITLE_WORDS.search(part) and len(part.split()) <= 8:
                return part.strip()
    return before or (previous[0] if previous else "")


def extract_roles(lines: list[str], today: date) -> list[Role]:
    """Find every dated position in the experience lines."""
    hits = []
    for i, line in enumerate(lines):
        match = DATE_RANGE.search(line)
        if not match or line.startswith(("-", "*")):
            continue
        start = parse_date(match["start"], today)
        end = parse_date(match["end"], today)
        if not start or not end or month_index(start) > month_index((today.year, today.month)):
            continue
        end = min(end, (today.year, today.month), key=month_index)
        previous = lines[max(0, i - 2):i][::-1]  # nearest line first
        hits.append((i, guess_title(line, match, previous), start, end))

    roles = []
    for n, (i, title, start, end) in enumerate(hits):
        stop = hits[n + 1][0] if n + 1 < len(hits) else len(lines)
        block = "\n".join(lines[i:stop])
        months = max(0, month_index(end) - month_index(start) + 1)
        is_tech = bool(TECH_TITLE.search(title)) or len(find_skills(block)) >= 2
        roles.append(Role(title, f"{start[0]}-{start[1]:02d}", f"{end[0]}-{end[1]:02d}",
                          months, is_tech))
    return roles


def total_years(roles: list[Role]) -> float:
    """Sum role durations in years, without double counting overlaps."""
    spans = sorted(
        (month_index(tuple(map(int, r.start.split("-")))),
         month_index(tuple(map(int, r.end.split("-")))) + 1)
        for r in roles
    )
    months, cur_start, cur_end = 0, None, None
    for start, end in spans:
        if cur_end is None or start > cur_end:
            if cur_end is not None:
                months += cur_end - cur_start
            cur_start, cur_end = start, end
        else:
            cur_end = max(cur_end, end)
    if cur_end is not None:
        months += cur_end - cur_start
    return round(months / 12, 1)


def headline_title(header_lines: list[str]) -> str | None:
    """A short title line near the name at the top, like 'Software Engineer'."""
    for line in header_lines[:5]:
        if (TITLE_WORDS.search(line) and len(line.split()) <= 6
                and not re.search(r"[@|\d]", line)):
            return line.strip()
    return None


# ---------------------------------------------------------------- main entry

def parse_resume(path: Path | None = None, today: date | None = None) -> ResumeInfo:
    """Read the resume and return structured info. Raises ResumeError on problems."""
    path = path or find_resume()
    today = today or date.today()
    text = extract_text(path)
    sections = split_sections(text)

    exp_lines = [line for key, lines in sections.items()
                 if any(w in key for w in EXPERIENCE_WORDS) for line in lines]
    roles = extract_roles(exp_lines or text.split("\n"), today)

    titles = []
    if headline := headline_title(sections["header"]):
        titles.append(headline)
    titles += [r.title for r in roles if r.title and r.title not in titles]

    stated = [int(m[1]) for m in STATED_YEARS.finditer(text)]
    return ResumeInfo(
        path=str(path),
        text=text,
        text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        skills=extract_skills(text, sections),
        titles=titles,
        roles=roles,
        years_experience=total_years(roles),
        years_tech_experience=total_years([r for r in roles if r.is_tech]),
        stated_years=max(stated) if stated else None,
    )


def summary_line(info: ResumeInfo) -> str:
    """One-line status, e.g. 'found resume.pdf (42 skills detected)'."""
    return f"found {Path(info.path).name} ({len(info.skills)} skills detected)"


def main() -> int:
    """Print what was found in the resume. Used for testing: python -m src.resume_parser [file]"""
    try:
        info = parse_resume(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
    except ResumeError as exc:
        print(f"Resume problem: {exc}")
        return 1
    print(summary_line(info))
    print(f"Years of experience: {info.years_experience} total, "
          f"{info.years_tech_experience} in tech roles"
          + (f" (resume states {info.stated_years}+)" if info.stated_years else ""))
    print("Job titles:")
    for role in info.roles:
        tag = "tech" if role.is_tech else "other"
        print(f"  - {role.title}  {role.start} to {role.end}  ({role.months} mo, {tag})")
    if info.titles and info.titles[0] not in [r.title for r in info.roles]:
        print(f"  headline: {info.titles[0]}")
    print("Skills:", ", ".join(info.skills))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace", line_buffering=True)
    sys.exit(main())
