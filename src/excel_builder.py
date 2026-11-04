"""Build output/Job_Rankings.xlsx from data/jobs_ranked.json.

The workbook is MERGED, never wiped: rows are matched by Job ID, and everything you type
(Status, Date Applied, Notes, and the other tracking columns, plus any columns or rows you
add yourself) survives every run. Jobs move between sheets as their score changes. Jobs that
no longer appear in a scrape stay in the workbook with their "Last Seen" date.

Sheets: Top Matches (score 50+), Low Matches, Unsure Location (could not confirm remote US),
Summary, Excluded (not remote US), Scoring Method. Styling follows Remote_SWE_Job_Rankings.xlsx.
"""
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from .backups import make_backup
from .config import load_json
from .paths import DATA_DIR, OUTPUT_DIR
from .workbook_lock import WorkbookBusy, workbook_lock

RANKED_FILE = DATA_DIR / "jobs_ranked.json"
OUTPUT_FILE = OUTPUT_DIR / "Job_Rankings.xlsx"

TOP, LOW, UNSURE = "Top Matches", "Low Matches", "Unsure Location"
SUMMARY, EXCLUDED, METHOD = "Summary", "Excluded", "Scoring Method"
TRACKED_SHEETS = (TOP, LOW, UNSURE)
SHEET_ORDER = (TOP, LOW, UNSURE, SUMMARY, EXCLUDED, METHOD)
TOP_THRESHOLD = 50

STATUSES = ["New", "Interested", "Applied", "Interviewing", "Offer", "Rejected", "Not Interested"]
APPLIED_VIA = ["Company site", "LinkedIn", "Indeed", "Handshake", "Referral", "Recruiter", "Other"]
TRACKING = ["Status", "Date Applied", "Applied Via", "Contact / Recruiter", "Last Contact",
            "Next Step", "Follow-Up Date", "Notes", "Interview Scheduled", "Rejected", "Hired"]
JOB_ID, LINK, LAST_SEEN = "Job ID", "Link", "Last Seen"

# Reference styling
NAVY = "FF1F3864"
HEADER_FONT = Font(name="Arial", size=11, bold=True, color="FFFFFFFF")
HEADER_FILL = PatternFill("solid", start_color=NAVY, end_color=NAVY)
BODY_FONT = Font(name="Arial", size=10)
BOLD_FONT = Font(name="Arial", size=10, bold=True)
SUBSCORE_FONT = Font(name="Arial", size=10, color="FF0000FF")
LINK_FONT = Font(name="Arial", size=10, color="FF0563C1")
THIN = Side(style="thin", color="FFBFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(wrap_text=True, vertical="top")
CENTER = Alignment(horizontal="center", vertical="top")
DATE_FORMAT = "mm/dd/yyyy"


def fill(color: str) -> PatternFill:
    return PatternFill("solid", start_color=color, end_color=color, bgColor=color)


@dataclass(frozen=True)
class Col:
    """One workbook column: header text, width, and how cells are styled."""
    name: str
    width: float
    kind: str = "wrap"  # wrap | center | score | sub | date | link | formula | id


TRACKED_COLUMNS = [
    Col("Rank", 6, "center"), Col("Score", 9, "score"), Col("Title", 34), Col("Company", 18),
    Col("Skill (40)", 9, "sub"), Col("Experience (20)", 11, "sub"), Col("Role Fit (20)", 10, "sub"),
    Col("Pay (10)", 8, "sub"), Col("Constraints (10)", 12, "sub"),
    Col("Location", 22), Col("Remote Type", 11, "center"), Col("Salary", 14), Col("Source", 11),
    Col("Date Posted", 12, "date"), Col(LINK, 13, "link"), Col("Why It Matched", 42),
    Col("Missing Skills", 24), Col("Red Flags", 40),
    Col("Status", 13), Col("Date Applied", 13, "date"), Col("Applied Via", 16),
    Col("Contact / Recruiter", 18), Col("Last Contact", 13, "date"), Col("Next Step", 24),
    Col("Follow-Up Date", 14, "date"), Col("Days Since Applied", 11, "formula"), Col("Notes", 28),
    Col("Interview Scheduled", 12, "center"), Col("Rejected", 11, "center"), Col("Hired", 10, "center"),
    Col(LAST_SEEN, 12, "date"), Col(JOB_ID, 18, "id"),
]
LOCATION_CHECK = Col("Location Check", 40)
EXCLUDED_COLUMNS = [
    Col("Company", 26), Col("Title", 38), Col("Reason", 62), Col("Location", 22), Col("Source", 11),
    Col("Date Posted", 12, "date"), Col(LINK, 13, "link"), Col(LAST_SEEN, 12, "date"), Col(JOB_ID, 18, "id"),
]
SUBSCORES = {"Skill (40)": "skill", "Experience (20)": "experience", "Role Fit (20)": "role_fit",
             "Pay (10)": "pay", "Constraints (10)": "constraints"}
COMPUTED = {"Rank", "Days Since Applied"}


class WorkbookLocked(Exception):
    """The workbook is open in Excel (Windows locks open files)."""


@dataclass
class BuildResult:
    """Row counts per sheet after a build."""
    counts: dict
    path: Path
    backup: Path | None

    def summary_line(self) -> str:
        """'28 top matches, 33 low matches -> output/Job_Rankings.xlsx'"""
        c = self.counts
        return (f"{c[TOP]} top matches, {c[LOW]} low matches, {c[UNSURE]} unsure location, "
                f"{c[EXCLUDED]} excluded -> {self.path}")


def columns_for(sheet: str) -> list[Col]:
    """Tracked sheets share one layout; Unsure Location adds a Location Check column."""
    if sheet == UNSURE:
        at = next(i for i, c in enumerate(TRACKED_COLUMNS) if c.name == "Remote Type") + 1
        return TRACKED_COLUMNS[:at] + [LOCATION_CHECK] + TRACKED_COLUMNS[at:]
    return EXCLUDED_COLUMNS if sheet == EXCLUDED else TRACKED_COLUMNS


# ---------------------------------------------------------------- reading the old workbook

@dataclass
class OldRow:
    """A row as it was in the existing workbook."""
    sheet: str
    values: dict


def read_existing(path: Path) -> tuple[dict[str, OldRow], dict[str, list[str]]]:
    """Rows keyed by Job ID (hand-added rows get a 'manual-N' key), and any extra columns
    you added to each sheet, so nothing you typed is lost."""
    rows: dict[str, OldRow] = {}
    extras: dict[str, list[str]] = {}
    if not path.exists():
        return rows, extras
    wb = load_workbook(path)
    manual = 0
    for sheet in (*TRACKED_SHEETS, EXCLUDED):
        if sheet not in wb.sheetnames:
            continue
        ws = wb[sheet]
        headers = [str(c.value).strip() if c.value is not None else "" for c in ws[1]]
        known = {c.name for c in columns_for(sheet)}
        extras[sheet] = [h for h in headers if h and h not in known]
        for cells in ws.iter_rows(min_row=2):
            values = {}
            for header, cell in zip(headers, cells):
                if not header:
                    continue
                value = cell.value
                if header == LINK and cell.hyperlink is not None:
                    value = cell.hyperlink.target
                values[header] = value
            if not any(v not in (None, "") for v in values.values()):
                continue
            key = str(values.get(JOB_ID) or "").strip()
            if not key:
                manual += 1
                key = f"manual-{manual}"
            rows[key] = OldRow(sheet, values)
    wb.close()
    return rows, extras


def has_tracking(values: dict) -> bool:
    """True if you have recorded anything about this job."""
    for name in TRACKING:
        value = values.get(name)
        if name == "Status":
            if value not in (None, "", "New"):
                return True
        elif name in ("Interview Scheduled", "Rejected", "Hired"):
            if value not in (None, "", "No"):
                return True
        elif value not in (None, ""):
            return True
    return False


# ---------------------------------------------------------------- new values from the ranking

def to_date(value):
    """date from an ISO string; other values pass through."""
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return value
    return value


def job_values(job: dict, seen: date) -> dict:
    """Workbook values computed from one ranked job (tracking columns are not touched)."""
    flags = job.get("flag_reasons") or []
    why = " ".join(job.get("reasons") or [])
    if flags:
        why = f"Filtered out: {flags[0]}" + (f" | {why}" if why else "")
    breakdown = job.get("score_breakdown") or {}
    values = {
        "Score": job.get("score", 0),
        "Title": job["title"],
        "Company": job["company"],
        "Location": job.get("location", ""),
        "Remote Type": (job.get("remote_type") or "").title(),
        "Salary": job.get("salary_text") or "",
        "Source": job.get("source", ""),
        "Date Posted": to_date(job.get("date_posted")),
        LINK: job.get("url", ""),
        "Why It Matched": why,
        "Missing Skills": ", ".join(job.get("missing_skills") or []),
        "Red Flags": "; ".join([*flags, *(job.get("red_flags") or [])]),
        "Location Check": job.get("location_note", ""),
        "Reason": flags[0] if flags else job.get("location_note", ""),
        LAST_SEEN: seen,
        JOB_ID: job["id"],
    }
    values.update({col: breakdown.get(key) for col, key in SUBSCORES.items()})
    return values


def target_sheet(job: dict, old: OldRow | None) -> str:
    """Where a ranked job belongs. Jobs you are tracking never go to the untracked Excluded sheet."""
    tracked = old is not None and has_tracking(old.values)
    if job["bucket"] == "excluded" and not tracked:
        return EXCLUDED
    if job["bucket"] == "unsure":
        return UNSURE
    return TOP if job.get("score", 0) >= TOP_THRESHOLD else LOW


def merge(ranked: list[dict], old_rows: dict[str, OldRow], seen: date) -> dict[str, list[dict]]:
    """Combine the new ranking with the old workbook into rows per sheet."""
    sheets: dict[str, list[dict]] = {name: [] for name in (*TRACKED_SHEETS, EXCLUDED)}
    done = set()
    for job in ranked:
        old = old_rows.get(job["id"])
        values = dict(old.values) if old else {"Status": "New"}
        values.update(job_values(job, seen))  # fresh job data; tracking columns stay as they were
        sheets[target_sheet(job, old)].append(values)
        done.add(job["id"])
    for key, old in old_rows.items():  # no longer listed, or added by hand: keep as it was
        if key not in done:
            sheets[old.sheet].append(dict(old.values))
    for name in TRACKED_SHEETS:
        sheets[name].sort(key=lambda v: -(v.get("Score") if isinstance(v.get("Score"), (int, float)) else -1))
    return sheets


# ---------------------------------------------------------------- writing

def clean(value):
    """Strip characters Excel cannot store."""
    return ILLEGAL_CHARACTERS_RE.sub("", value) if isinstance(value, str) else value


def write_header(ws, columns: list[Col]) -> None:
    for i, col in enumerate(columns, 1):
        cell = ws.cell(row=1, column=i, value=col.name)
        cell.font, cell.fill, cell.border = HEADER_FONT, HEADER_FILL, BORDER
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws.column_dimensions[get_column_letter(i)].width = col.width
    ws.row_dimensions[1].height = 30


def write_cell(ws, r: int, c: int, col: Col, value, letters: dict) -> None:
    """Write one body cell with the reference styling for its column."""
    cell = ws.cell(row=r, column=c)
    if col.kind == "formula":
        da = letters["Date Applied"]
        cell.value = f'=IF({da}{r}="","",TODAY()-{da}{r})'
        cell.number_format = "0"
    elif col.kind == "link":
        is_url = isinstance(value, str) and value.startswith(("http://", "https://"))
        cell.value = "Open posting" if is_url else clean(value)
        if is_url:
            cell.hyperlink = value
            cell.font = LINK_FONT
    elif col.name == "Rank":
        cell.value = r - 1
    else:
        cell.value = clean(value)
    if col.kind == "date" and isinstance(cell.value, (date, datetime)):
        cell.number_format = DATE_FORMAT
    if col.kind != "link":
        cell.font = {"score": BOLD_FONT, "sub": SUBSCORE_FONT}.get(col.kind, BODY_FONT)
    cell.alignment = CENTER if col.kind in ("center", "score", "sub", "date", "formula") else WRAP
    cell.border = BORDER


def write_rows(ws, columns: list[Col], rows: list[dict], extras: list[str]) -> None:
    """Header plus body rows. Extra columns you added keep their values at the end."""
    all_cols = columns + [Col(name, 16) for name in extras]
    write_header(ws, all_cols)
    letters = {c.name: get_column_letter(i) for i, c in enumerate(all_cols, 1)}
    for r, values in enumerate(rows, 2):
        for c, col in enumerate(all_cols, 1):
            write_cell(ws, r, c, col, values.get(col.name), letters)
        ws.row_dimensions[r].height = 25.5
    last_col = get_column_letter(len(all_cols))
    ws.auto_filter.ref = f"A1:{last_col}{max(len(rows) + 1, 2)}"
    ws.sheet_view.zoomScale = 85


def add_tracking_rules(ws, columns: list[Col], n_rows: int) -> None:
    """Dropdowns, date checks, score colors, and row highlights, as in the reference workbook."""
    letters = {c.name: get_column_letter(i) for i, c in enumerate(columns, 1)}
    last = max(n_rows + 1, 2)
    end = n_rows + 200  # room for rows you add by hand
    rng = lambda name: f"{letters[name]}2:{letters[name]}{end}"
    lists = [("Status", STATUSES), ("Applied Via", APPLIED_VIA)]
    for name, options in lists:
        dv = DataValidation(type="list", formula1=f'"{",".join(options)}"', allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(rng(name))
    yes_no = DataValidation(type="list", formula1='"Yes,No"', allow_blank=True)
    ws.add_data_validation(yes_no)
    for name in ("Interview Scheduled", "Rejected", "Hired"):
        yes_no.add(rng(name))
    dates = DataValidation(type="date", operator="greaterThan", formula1="45000", allow_blank=True,
                           showErrorMessage=True, error="Enter a date like 10/29/2026")
    ws.add_data_validation(dates)
    for name in ("Date Applied", "Last Contact", "Follow-Up Date"):
        dates.add(rng(name))

    score = f"{letters['Score']}2:{letters['Score']}{last}"
    ws.conditional_formatting.add(score, CellIsRule(operator="greaterThanOrEqual", formula=["65"],
                                                    fill=fill("FFC6EFCE")))
    ws.conditional_formatting.add(score, CellIsRule(operator="between", formula=["50", "64.99"],
                                                    fill=fill("FFFFEB9C")))
    ws.conditional_formatting.add(score, CellIsRule(operator="lessThan", formula=["50"],
                                                    fill=fill("FFFFC7CE")))
    whole = f"A2:{get_column_letter(len(columns))}{last}"
    hired, rejected, status = letters["Hired"], letters["Rejected"], letters["Status"]
    gray = dict(fill=fill("FFF2F2F2"), font=Font(color="FFA6A6A6"))
    ws.conditional_formatting.add(whole, FormulaRule(formula=[f'${hired}2="Yes"'], stopIfTrue=True,
                                                     fill=fill("FFC6EFCE"), font=Font(color="FF006100", bold=True)))
    ws.conditional_formatting.add(whole, FormulaRule(
        formula=[f'OR(${rejected}2="Yes",${status}2="Rejected",${status}2="Not Interested")'], **gray))
    ws.conditional_formatting.add(whole, FormulaRule(
        formula=[f'COUNTIF(${hired}$2:${hired}${last},"Yes")>0'], **gray))
    follow = letters["Follow-Up Date"]
    ws.conditional_formatting.add(f"{follow}2:{follow}{last}", FormulaRule(
        formula=[f'AND({follow}2<>"",{follow}2<=TODAY())'], fill=fill("FFFFEB9C"),
        font=Font(color="FF9C5700", bold=True)))


def write_tracked(wb, name: str, rows: list[dict], extras: list[str]) -> None:
    ws = wb[name]
    columns = columns_for(name)
    write_rows(ws, columns, rows, extras)
    add_tracking_rules(ws, columns, len(rows))
    ws.freeze_panes = "D2"


def write_excluded(wb, rows: list[dict], extras: list[str]) -> None:
    ws = wb[EXCLUDED]
    write_rows(ws, EXCLUDED_COLUMNS, rows, extras)
    ws.freeze_panes = "A2"


def sheet_ref(sheet: str, column: str, from_row: int = 2) -> str:
    """'Top Matches'!$S$2:$S$10000 for the named column."""
    letter = get_column_letter(next(i for i, c in enumerate(columns_for(sheet), 1) if c.name == column))
    return f"'{sheet}'!${letter}${from_row}:${letter}$10000"


def across(template: str, *columns: str) -> str:
    """Sum a COUNTIF-style formula over the three tracked sheets."""
    parts = [template.format(*(sheet_ref(s, c) for c in columns)) for s in TRACKED_SHEETS]
    return "=" + "+".join(parts)


def write_summary(wb, sheets: dict[str, list[dict]], meta: dict) -> None:
    """Live formulas (update as you edit in Excel) plus facts about the last run."""
    ws = wb[SUMMARY]
    write_header(ws, [Col("Measure", 44), Col("Count", 14), Col("", 3), Col("Top companies (score 50+)", 30),
                      Col("Jobs", 8)])
    ws.cell(row=1, column=3).fill = PatternFill(fill_type=None)
    rows = [
        ("Top matches (score 50+)", f"=COUNTA({sheet_ref(TOP, 'Title')})"),
        ("Low matches", f"=COUNTA({sheet_ref(LOW, 'Title')})"),
        ("Unsure location (check by hand)", f"=COUNTA({sheet_ref(UNSURE, 'Title')})"),
        ("Excluded (not remote US)", f"=COUNTA({sheet_ref(EXCLUDED, 'Title')})"),
        ("Average score (top + low matches)",
         f"=IFERROR(ROUND(AVERAGE({sheet_ref(TOP, 'Score')},{sheet_ref(LOW, 'Score')}),1),0)"),
        ("Applications sent (has Date Applied)", across("COUNT({0})", "Date Applied")),
        *[(f"Status: {s}", across(f'COUNTIF({{0}},"{s}")', "Status")) for s in STATUSES],
        ("Interviews scheduled", across('COUNTIF({0},"Yes")', "Interview Scheduled")),
        ("Rejected", across('COUNTIF({0},"Yes")', "Rejected")),
        ("Hired", across('COUNTIF({0},"Yes")', "Hired")),
        ("Still active (applied, not rejected)",
         across('COUNTIFS({0},"<>",{1},"<>Yes")', "Date Applied", "Rejected")),
        ("Follow-ups due today or overdue",
         across('COUNTIFS({0},"<="&TODAY(),{1},"<>Yes")', "Follow-Up Date", "Rejected")),
        ("Interview rate (interviews / applications)", "=IFERROR({Interviews scheduled}/"
                                                       "{Applications sent (has Date Applied)},0)"),
        ("Last run", meta.get("ranked_at", "")),
        ("Model", meta.get("model", "")),
        ("Jobs found in the last scrape", meta.get("found", "")),
    ]
    cell_of = {label: f"B{r}" for r, (label, _) in enumerate(rows, 2)}
    for r, (label, value) in enumerate(rows, 2):
        if isinstance(value, str) and value.startswith("=IFERROR({"):
            value = value.format(**cell_of)  # e.g. interview rate refers to other rows by name
        ws.cell(row=r, column=1, value=label).font = BODY_FONT
        cell = ws.cell(row=r, column=2, value=value)
        cell.font, cell.alignment = BOLD_FONT, CENTER
        if label.startswith("Interview rate"):
            cell.number_format = "0%"
        for c in (1, 2):
            ws.cell(row=r, column=c).border = BORDER
    note = ws.cell(row=len(rows) + 3, column=1,
                   value=f"Counts update from the {', '.join(TRACKED_SHEETS)} tabs as you edit them.")
    note.font = Font(name="Arial", size=10, italic=True)

    counts: dict[str, int] = {}
    for row in sheets[TOP]:
        counts[row.get("Company") or "?"] = counts.get(row.get("Company") or "?", 0) + 1
    for r, (company, n) in enumerate(sorted(counts.items(), key=lambda kv: -kv[1])[:10], 2):
        ws.cell(row=r, column=4, value=company).font = BODY_FONT
        cell = ws.cell(row=r, column=5, value=n)
        cell.font, cell.alignment = BOLD_FONT, CENTER
        for c in (4, 5):
            ws.cell(row=r, column=c).border = BORDER


def write_method(wb, weights: dict) -> None:
    """How the score is made, in plain words."""
    ws = wb[METHOD]
    write_header(ws, [Col("Part", 22), Col("Weight / max", 14), Col("What it measures", 95)])
    pct = lambda k: f"{weights.get(k, 0) * 100:.0f}% of score"
    rows = [
        ("LLM review", pct("llm"), "A local AI model (Ollama) reads your resume, your preferences, and the "
                                   "full posting, and scores the five parts below."),
        ("  Skill", "40", "How much of the required tech stack matches your resume"),
        ("  Experience", "20", "Years asked for vs. your experience in tech roles"),
        ("  Role Fit", "20", "How close the job is to your target titles"),
        ("  Pay", "10", "10 = listed pay meets your minimum; 5 = not listed; lower if under it"),
        ("  Constraints", "10", "Points off for travel, on-call, clearance, relocation, contract-only, "
                                "or location limits"),
        ("Resume similarity", pct("embedding"), "How close the posting's wording is to your resume and "
                                                "target titles (local embedding model)"),
        ("Keywords", pct("keywords"), "Your must-have skills found in the posting, and how much of its "
                                      "tech stack is on your resume"),
        ("Salary fit", pct("salary"), "Listed pay vs. your minimum (unknown pay is neutral)"),
        ("Level", "-5 to -20", "Points off when the title is above or below the levels you chose "
                               "(for example Senior or Staff when you want entry or mid level)"),
        ("Filters", "score 0", "Deal breakers in the title or confirmed in the posting, and pay below your "
                               "minimum. These jobs stay in Low Matches with the reason in Red Flags."),
        ("Location", "separate tabs", "Jobs not confirmed as remote and open to the US go to Unsure "
                                      "Location (still scored) or Excluded (with the reason)."),
        ("Note", "", "Change the weights in config/preferences.json (score_weights). Scores are judgment "
                     "calls by a local model; always read the posting."),
    ]
    for r, row in enumerate(rows, 2):
        for c, value in enumerate(row, 1):
            cell = ws.cell(row=r, column=c, value=value)
            cell.font, cell.border = BODY_FONT, BORDER
            cell.alignment = CENTER if c == 2 else WRAP


def save(wb, path: Path) -> Path | None:
    """Back up the old file, then write the new one atomically. Raises WorkbookLocked if Excel has it open."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"~tmp_{path.name}")
    wb.save(tmp)
    try:
        if path.exists():
            with open(path, "r+b"):  # fails while Excel has the file open
                pass
        backup = make_backup(path, path.parent / "backups")
        os.replace(tmp, path)
    except PermissionError as exc:
        tmp.unlink(missing_ok=True)
        raise WorkbookLocked(f"{path.name} is open in Excel. Close the workbook in Excel and "
                             f"run again to save the new rankings.") from exc
    return backup


def build(ranked_file: Path = RANKED_FILE, out: Path = OUTPUT_FILE) -> BuildResult:
    """Merge the latest ranking into the workbook and save it, holding the workbook lock
    so a dashboard edit cannot land between reading the old file and writing the new one."""
    ranked = load_json(ranked_file)
    if not ranked:
        raise FileNotFoundError(f"{ranked_file} not found. Run the ranking step first.")
    with workbook_lock():
        return _build(ranked, out)


def _build(ranked: dict, out: Path) -> BuildResult:
    seen = to_date(ranked.get("ranked_at")) or date.today()
    old_rows, extras = read_existing(out)
    sheets = merge(ranked["jobs"], old_rows, seen)

    wb = load_workbook(out) if out.exists() else Workbook()
    for name in [n for n in wb.sheetnames if n in SHEET_ORDER or n == "Sheet" and not out.exists()]:
        wb.remove(wb[name])
    for i, name in enumerate(SHEET_ORDER):
        wb.create_sheet(name, index=i)
    for name in TRACKED_SHEETS:
        write_tracked(wb, name, sheets[name], extras.get(name, []))
    write_excluded(wb, sheets[EXCLUDED], extras.get(EXCLUDED, []))
    meta = {"ranked_at": ranked.get("ranked_at", "").replace("T", " "), "model": ranked.get("model", ""),
            "found": sum(1 for j in ranked["jobs"])}
    write_summary(wb, sheets, meta)
    write_method(wb, ranked.get("weights", {}))
    wb.active = 0
    backup = save(wb, out)
    return BuildResult({name: len(rows) for name, rows in sheets.items()}, out, backup)


def main() -> int:
    """python -m src.excel_builder"""
    try:
        result = build()
    except (FileNotFoundError, WorkbookLocked, WorkbookBusy) as exc:
        print(f"Excel problem: {exc}")
        return 1
    print(result.summary_line())
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace", line_buffering=True)
    sys.exit(main())
