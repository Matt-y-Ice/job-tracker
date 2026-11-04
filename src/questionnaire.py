"""Ask the user about their job preferences and save them to config/preferences.json.

Returning users see their saved answers and can keep them with one keypress.
"""
import re
import sys
from datetime import datetime
from typing import Callable

from .config import load_json, save_json
from .paths import PREFERENCES_FILE

Ask = Callable[[str], str]

REMOTE_OPTIONS = {
    "remote_only": "Remote only",
    "remote_or_hybrid": "Remote or hybrid",
    "any": "Any (remote, hybrid, or onsite)",
}
SENIORITY_OPTIONS = {
    "intern": "Internship / co-op",
    "entry": "Entry level / junior",
    "mid": "Mid level",
    "senior": "Senior",
    "lead": "Lead / staff / principal",
    "manager": "Manager",
}
COMPANY_SIZE_OPTIONS = {
    "startup": "Startup (1-50 people)",
    "small": "Small (51-200)",
    "mid": "Mid-size (201-1,000)",
    "large": "Large (1,000+)",
    "any": "No preference",
}
DEFAULT_WEIGHTS = {"llm": 0.5, "embedding": 0.25, "salary": 0.1, "keywords": 0.15}
DEFAULTS = {
    "min_salary": 0,
    "remote_preference": "remote_only",
    "locations": ["United States"],
    "target_titles": [],
    "must_have_skills": [],
    "deal_breakers": [],
    "seniority": ["entry", "mid"],
    "company_size": ["any"],
    "days_back": 25,
}


def has_console() -> bool:
    """True if someone can type answers. On Windows, input redirected from NUL (Task Scheduler,
    scripts) still claims to be a terminal, so ask Windows whether it is a real console."""
    try:
        if not sys.stdin or not sys.stdin.isatty():
            return False
    except (AttributeError, ValueError):
        return False
    if sys.platform != "win32":
        return True
    import ctypes
    import msvcrt
    mode = ctypes.c_uint32()
    handle = msvcrt.get_osfhandle(sys.stdin.fileno())
    return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))


class QuestionnaireAborted(Exception):
    """The user closed the input or pressed Ctrl+C."""


# ---------------------------------------------------------------- input helpers

def read(ask: Ask, prompt: str) -> str:
    """Call the input function, turning Ctrl+C / closed input into one clear error."""
    try:
        return ask(prompt).strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise QuestionnaireAborted("Questionnaire cancelled. Nothing was saved.") from exc


def ask_list(ask: Ask, question: str, default: list[str], hint: str = "") -> list[str]:
    """Comma separated answer. Enter keeps the default, '-' clears it."""
    shown = ", ".join(default) if default else "none"
    print(f"\n{question}")
    if hint:
        print(f"  {hint}")
    answer = read(ask, f"  Separate with commas. Enter = [{shown}], '-' = none: ")
    if not answer:
        return list(default)
    if answer == "-":
        return []
    return [item.strip() for item in answer.split(",") if item.strip()]


def parse_salary(text: str) -> int | None:
    """Turn '100k', '$95,000', '120000' into an int. None if not a number."""
    text = text.lower().replace(",", "").replace("$", "").replace(" ", "")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(k?)", text)
    if not match:
        return None
    value = float(match[1]) * (1000 if match[2] else 1)
    if value < 1000:  # "95" almost certainly means 95k
        value *= 1000
    return int(value)


def ask_salary(ask: Ask, default: int) -> int:
    """Minimum yearly salary in US dollars. 0 means no minimum."""
    shown = f"${default:,}" if default else "no minimum"
    print("\nMinimum yearly salary (USD)? Jobs that list a lower maximum are flagged.")
    while True:
        answer = read(ask, f"  Example: 90k. Enter = [{shown}], 0 = no minimum: ")
        if not answer:
            return default
        if answer == "0":
            return 0
        value = parse_salary(answer)
        if value is not None:
            return value
        print("  Please type a number like 90000 or 90k.")


def ask_int(ask: Ask, question: str, default: int, low: int, high: int) -> int:
    """Whole number between low and high."""
    print(f"\n{question}")
    while True:
        answer = read(ask, f"  Enter = [{default}]: ")
        if not answer:
            return default
        if answer.isdigit() and low <= int(answer) <= high:
            return int(answer)
        print(f"  Please type a number from {low} to {high}.")


def print_options(options: dict[str, str]) -> list[str]:
    """Print numbered options and return their keys in order."""
    keys = list(options)
    for i, key in enumerate(keys, 1):
        print(f"  {i}. {options[key]}")
    return keys


def ask_choice(ask: Ask, question: str, options: dict[str, str], default: str) -> str:
    """Pick exactly one option by number."""
    print(f"\n{question}")
    keys = print_options(options)
    while True:
        answer = read(ask, f"  Number. Enter = [{options[default]}]: ")
        if not answer:
            return default
        if answer.isdigit() and 1 <= int(answer) <= len(keys):
            return keys[int(answer) - 1]
        print(f"  Please type a number from 1 to {len(keys)}.")


def ask_multi(ask: Ask, question: str, options: dict[str, str], default: list[str]) -> list[str]:
    """Pick one or more options by number, e.g. '2,3'."""
    print(f"\n{question}")
    keys = print_options(options)
    shown = ", ".join(options[k] for k in default if k in options)
    while True:
        answer = read(ask, f"  Numbers separated by commas. Enter = [{shown}]: ")
        if not answer:
            return list(default)
        picks = [p.strip() for p in answer.split(",") if p.strip()]
        if picks and all(p.isdigit() and 1 <= int(p) <= len(keys) for p in picks):
            chosen = [keys[int(p) - 1] for p in dict.fromkeys(picks)]
            return ["any"] if "any" in chosen else chosen
        print(f"  Please type numbers from 1 to {len(keys)}, like 2,3.")


# ---------------------------------------------------------------- questionnaire

def ask_all(ask: Ask, current: dict, resume_titles: list[str] | None = None) -> dict:
    """Ask every question, using `current` answers as defaults."""
    titles_default = current.get("target_titles") or (resume_titles or [])[:3]
    answers = {
        "min_salary": ask_salary(ask, current["min_salary"]),
        "remote_preference": ask_choice(
            ask, "Which work arrangements are OK?", REMOTE_OPTIONS, current["remote_preference"]),
        "locations": ask_list(
            ask, "Preferred locations or time zones?", current["locations"],
            "Examples: United States, Canada, EST, Europe, Anywhere"),
        "target_titles": ask_list(
            ask, "Job titles you are looking for?", titles_default,
            "Examples: Software Engineer, Backend Developer, Data Engineer"),
        "must_have_skills": ask_list(
            ask, "Must-have skills or languages? Jobs using these score higher.",
            current["must_have_skills"], "Examples: Python, SQL, React"),
        "deal_breakers": ask_list(
            ask, "Deal breakers? Jobs mentioning these get a score of 0 (they are kept, not deleted).",
            current["deal_breakers"],
            "Keywords or role types to avoid. Examples: AI/ML development, PHP, on-call, travel"),
        "seniority": ask_multi(
            ask, "Which seniority levels fit you?", SENIORITY_OPTIONS, current["seniority"]),
        "company_size": ask_multi(
            ask, "Company size preference?", COMPANY_SIZE_OPTIONS, current["company_size"]),
        "days_back": ask_int(
            ask, "How many days back should the job search look? (1-90)", current["days_back"], 1, 90),
    }
    return answers


def format_preferences(prefs: dict) -> str:
    """Human-readable summary of saved answers."""
    def join(values, labels=None):
        return ", ".join(labels.get(v, v) if labels else v for v in values) or "none"

    salary = f"${prefs['min_salary']:,}" if prefs["min_salary"] else "no minimum"
    rows = [
        ("Minimum salary", salary),
        ("Work arrangement", REMOTE_OPTIONS.get(prefs["remote_preference"], prefs["remote_preference"])),
        ("Locations / time zones", join(prefs["locations"])),
        ("Target job titles", join(prefs["target_titles"])),
        ("Must-have skills", join(prefs["must_have_skills"])),
        ("Deal breakers", join(prefs["deal_breakers"])),
        ("Seniority", join(prefs["seniority"], SENIORITY_OPTIONS)),
        ("Company size", join(prefs["company_size"], COMPANY_SIZE_OPTIONS)),
        ("Search back", f"{prefs['days_back']} days"),
    ]
    return "\n".join(f"  {label:<24} {value}" for label, value in rows)


def with_defaults(saved: dict | None) -> dict:
    """Fill any missing keys (e.g. from an older version) with defaults."""
    prefs = {**DEFAULTS, **(saved or {})}
    prefs["score_weights"] = {**DEFAULT_WEIGHTS, **prefs.get("score_weights", {})}
    return prefs


def save_preferences(prefs: dict) -> None:
    """Write preferences.json with a timestamp."""
    prefs["updated"] = datetime.now().isoformat(timespec="seconds")
    save_json(PREFERENCES_FILE, prefs)


def load_or_ask(ask: Ask = input, interactive: bool | None = None, reset: bool = False,
                resume_titles: list[str] | None = None) -> tuple[dict, str]:
    """Return (preferences, how) where how is 'saved', 'updated', or 'new'.

    When there is no console to ask (e.g. the dashboard's refresh button), saved
    answers are used without asking.
    """
    interactive = has_console() if interactive is None else interactive
    saved = None if reset else load_json(PREFERENCES_FILE)
    prefs = with_defaults(saved)

    if saved and not interactive:
        return prefs, "saved"
    if not saved and not interactive:
        raise QuestionnaireAborted(
            "No saved preferences yet. Double-click run.bat once and answer the questions; "
            "after that, runs without a keyboard (like the dashboard's Refresh button) work too.")

    if saved:
        print("\nYour saved preferences:")
        print(format_preferences(prefs))
        if read(ask, "\nUse these again? (Y/n): ").lower() in ("", "y", "yes"):
            return prefs, "saved"
        print("\nPress Enter on any question to keep its current answer.")
    else:
        print("\nA few questions about the jobs you want. Press Enter to accept the value in [brackets].")

    prefs.update(ask_all(ask, prefs, resume_titles))
    save_preferences(prefs)
    print(f"\nSaved to {PREFERENCES_FILE}")
    return prefs, "updated" if saved else "new"


def main() -> int:
    """Run the questionnaire on its own: python -m src.questionnaire [--reset]"""
    from .resume_parser import ResumeError, parse_resume
    try:
        titles = parse_resume().titles
    except ResumeError:
        titles = []  # suggestions are optional; the questionnaire still works
    try:
        prefs, how = load_or_ask(interactive=True, reset="--reset" in sys.argv,
                                 resume_titles=titles)
    except QuestionnaireAborted as exc:
        print(f"\n{exc}")
        return 1
    if how == "saved":
        print("Using saved answers.")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace", line_buffering=True)
    sys.exit(main())
