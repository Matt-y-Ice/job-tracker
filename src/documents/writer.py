"""Turn a checked draft into Word files (and PDFs through the installed Microsoft Word).

Layout is plain on purpose so applicant tracking systems read it correctly: one column,
real headings and bullets, no tables, text boxes, or images.
"""
import json
import logging
import re
import shutil
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

from docx import Document
from docx.enum.text import WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from ..config import save_json
from ..paths import OUTPUT_DIR
from .generator import Draft

log = logging.getLogger("jobtracker.documents")

DOCS_DIR = OUTPUT_DIR / "documents"
NAVY = RGBColor(0x1F, 0x38, 0x64)
FONT = "Calibri"
PDF_SCRIPT = Path(__file__).resolve().parent / "docx_to_pdf.ps1"
APPLE_SCRIPT = Path(__file__).resolve().parent / "docx_to_pdf.applescript"


def safe_name(text: str, limit: int = 40) -> str:
    """Letters, digits, and dashes only, for file and folder names."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")
    return cleaned[:limit].strip("-") or "Job"


def folder_for(job: dict, root: Path = DOCS_DIR) -> Path:
    """output/documents/<Company>_<Title>_<id>/"""
    return root / f"{safe_name(job['company'], 30)}_{safe_name(job['title'], 40)}_{job['id']}"


# ---------------------------------------------------------------- Word helpers

def new_document() -> Document:
    doc = Document()
    for section in doc.sections:
        section.top_margin = section.bottom_margin = Inches(0.6)
        section.left_margin = section.right_margin = Inches(0.7)
    style = doc.styles["Normal"]
    style.font.name, style.font.size = FONT, Pt(10.5)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    style.paragraph_format.space_after = Pt(2)
    return doc


def para(doc, text: str = "", size: float | None = None, bold: bool = False, color=None,
         space_after: float = 2, italic: bool = False):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(space_after)
    if text:
        run = p.add_run(text)
        run.bold, run.italic = bold, italic
        if size:
            run.font.size = Pt(size)
        if color is not None:
            run.font.color.rgb = color
    return p


def heading(doc, text: str) -> None:
    """Section heading: navy, bold, upper case, with a thin rule underneath."""
    p = para(doc, text.upper(), size=11, bold=True, color=NAVY, space_after=3)
    p.paragraph_format.space_before = Pt(8)
    border = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for key, value in {"val": "single", "sz": "6", "space": "1", "color": "1F3864"}.items():
        bottom.set(qn(f"w:{key}"), value)
    border.append(bottom)
    p._p.get_or_add_pPr().append(border)


def line_with_date(doc, left_bold: str, left_rest: str, right: str) -> None:
    """'Title, Company — Location' on the left, dates flush right."""
    p = para(doc, space_after=1)
    width = doc.sections[0].page_width - doc.sections[0].left_margin - doc.sections[0].right_margin
    p.paragraph_format.tab_stops.add_tab_stop(width, WD_TAB_ALIGNMENT.RIGHT)
    p.add_run(left_bold).bold = True
    if left_rest:
        p.add_run(left_rest)
    if right:
        p.add_run("\t" + right)


def bullets(doc, items: list[str]) -> None:
    for item in items:
        p = doc.add_paragraph(item, style="List Bullet")
        p.paragraph_format.space_after = Pt(1)


# ---------------------------------------------------------------- documents

def write_resume(path: Path, draft: dict, name: str, contact: str) -> None:
    doc = new_document()
    para(doc, name, size=18, bold=True, color=NAVY, space_after=0)
    if draft.get("headline"):
        para(doc, draft["headline"], size=11.5, space_after=0)
    if contact:
        para(doc, contact, size=9.5, space_after=4)
    if draft.get("summary"):
        heading(doc, "Summary")
        para(doc, draft["summary"])
    if draft.get("skills"):
        heading(doc, "Technical Skills")
        for group in draft["skills"]:
            p = para(doc, space_after=1)
            p.add_run(f"{group['label']}: ").bold = True
            p.add_run(", ".join(group["items"]))
    if draft.get("experience"):
        heading(doc, "Experience")
        for role in draft["experience"]:
            rest = f", {role['company']}" + (f" — {role['location']}" if role.get("location") else "")
            line_with_date(doc, role["title"], rest, role.get("dates", ""))
            bullets(doc, role.get("bullets", []))
    if draft.get("projects"):
        heading(doc, "Projects")
        for project in draft["projects"]:
            line_with_date(doc, project["name"], f" ({project['tech']})" if project.get("tech") else "", "")
            bullets(doc, project.get("bullets", []))
    for section in draft.get("sections", []):  # copied word for word from the original resume
        heading(doc, section["heading"])
        for item in section["items"]:
            if item["bullet"]:
                bullets(doc, [item["text"]])
            else:
                para(doc, item["text"], bold=item["bold"], space_after=0 if item["bold"] else 3)
    doc.save(path)


def write_letter(path: Path, letter: dict, name: str, contact: str, job: dict) -> None:
    doc = new_document()
    para(doc, name, size=16, bold=True, color=NAVY, space_after=0)
    if contact:
        para(doc, contact, size=9.5, space_after=14)
    today = date.today()
    para(doc, f"{today:%B} {today.day}, {today.year}", space_after=10)
    para(doc, "Hiring Team", space_after=0)
    para(doc, job["company"], space_after=10)
    para(doc, f"Re: {job['title']}", bold=True, space_after=10)
    para(doc, letter["greeting"], space_after=8)
    for paragraph in letter["paragraphs"]:
        para(doc, paragraph, space_after=8).paragraph_format.line_spacing = 1.1
    para(doc, letter["closing"], space_after=18)
    para(doc, name)
    doc.save(path)


# ---------------------------------------------------------------- PDF

class PdfUnavailable(Exception):
    """Word could not make the PDF (not installed, or it failed)."""


def to_pdf(docx_files: list[Path], timeout: int = 180) -> list[Path]:
    """Convert docx files to PDF. Windows uses Word. Mac tries Word, then LibreOffice."""
    if sys.platform == "win32":
        return _pdf_windows(docx_files, timeout)
    if sys.platform == "darwin":
        return _pdf_macos(docx_files, timeout)
    raise PdfUnavailable("PDF needs Microsoft Word on Windows, or Word or LibreOffice on Mac. "
                         "The Word files are ready.")


def _pdf_windows(docx_files: list[Path], timeout: int) -> list[Path]:
    """Convert with the installed Microsoft Word (one Word session for all files)."""
    pdfs = [f.with_suffix(".pdf") for f in docx_files]
    args = [item for pair in zip(docx_files, pdfs) for item in map(str, pair)]
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                 "-File", str(PDF_SCRIPT), *args],
                                capture_output=True, text=True, timeout=timeout,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PdfUnavailable(f"Word did not finish the PDF ({exc.__class__.__name__}). The Word files are ready.") from exc
    missing = [p for p in pdfs if not p.exists()]
    if result.returncode != 0 or missing:
        log.warning("PDF conversion failed: %s %s", result.stdout[-500:], result.stderr[-500:])
        reason = "Microsoft Word is not installed" if "WORD_MISSING" in result.stdout else "Word could not convert them"
        raise PdfUnavailable(f"No PDF: {reason}. The Word files are ready.")
    return pdfs


def _pdf_macos(docx_files: list[Path], timeout: int) -> list[Path]:
    try:
        return _pdf_via_word_mac(docx_files, timeout)
    except PdfUnavailable:
        return _pdf_via_libreoffice(docx_files, timeout)


def _pdf_via_word_mac(docx_files: list[Path], timeout: int) -> list[Path]:
    """Ask the user's Word for Mac to save PDFs. Does not quit Word."""
    pdfs = [f.with_suffix(".pdf") for f in docx_files]
    args = [item for pair in zip(docx_files, pdfs) for item in map(str, pair)]
    try:
        result = subprocess.run(["osascript", str(APPLE_SCRIPT), *args],
                                capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PdfUnavailable(f"Word did not finish the PDF ({exc.__class__.__name__}). The Word files are ready.") from exc
    blob = f"{result.stdout}\n{result.stderr}"
    if "WORD_MISSING" in blob or result.returncode != 0:
        raise PdfUnavailable("No PDF: Microsoft Word is not installed. The Word files are ready.")
    missing = [p for p in pdfs if not p.exists()]
    if missing:
        raise PdfUnavailable("No PDF: Microsoft Word is not installed. The Word files are ready.")
    return pdfs


def _pdf_via_libreoffice(docx_files: list[Path], timeout: int) -> list[Path]:
    soffice = shutil.which("soffice")
    if not soffice:
        for candidate in (Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"),):
            if candidate.exists():
                soffice = str(candidate)
                break
    if not soffice:
        raise PdfUnavailable("No PDF: Word and LibreOffice are not installed. The Word files are ready.")
    pdfs = []
    for docx in docx_files:
        result = subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(docx.parent), str(docx)],
            capture_output=True, text=True, timeout=timeout,
        )
        pdf = docx.with_suffix(".pdf")
        if result.returncode != 0 or not pdf.exists():
            raise PdfUnavailable("No PDF: LibreOffice could not convert them. The Word files are ready.")
        pdfs.append(pdf)
    return pdfs


# ---------------------------------------------------------------- save everything

def save_documents(draft: Draft, job: dict, name: str, contact: str, model: str,
                   root: Path = DOCS_DIR, make_pdf=to_pdf) -> dict:
    """Write DOCX (and PDF when possible) plus meta.json. Returns the meta dict."""
    folder = folder_for(job, root)
    folder.mkdir(parents=True, exist_ok=True)
    base = f"{safe_name(name, 30)}_{{kind}}_{safe_name(job['company'], 30)}"
    resume_docx = folder / f"{base.format(kind='Resume')}.docx"
    letter_docx = folder / f"{base.format(kind='Cover-Letter')}.docx"
    write_resume(resume_docx, draft.resume, name, contact)
    write_letter(letter_docx, draft.letter, name, contact, job)
    files = [{"kind": "Resume", "format": "DOCX", "name": resume_docx.name},
             {"kind": "Cover letter", "format": "DOCX", "name": letter_docx.name}]
    pdf_note = ""
    try:
        resume_pdf, letter_pdf = make_pdf([resume_docx, letter_docx])
        files[1:1] = [{"kind": "Resume", "format": "PDF", "name": resume_pdf.name}]
        files.append({"kind": "Cover letter", "format": "PDF", "name": letter_pdf.name})
    except PdfUnavailable as exc:
        pdf_note = str(exc)
    meta = {"job_id": job["id"], "title": job["title"], "company": job["company"], "model": model,
            "generated_at": datetime.now().isoformat(timespec="seconds"), "files": files,
            "notes": draft.notes, "pdf_note": pdf_note,
            "draft": {"resume": draft.resume, "letter": draft.letter}}
    save_json(folder / "meta.json", meta)
    return meta


def load_meta(job_id: str, root: Path = DOCS_DIR) -> tuple[Path, dict] | None:
    """The newest saved documents for a job, if any."""
    for folder in sorted(root.glob(f"*_{job_id}"), reverse=True):
        try:
            return folder, json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return None
