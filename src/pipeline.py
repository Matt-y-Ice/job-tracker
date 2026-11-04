"""The whole pipeline: resume -> preferences -> scrape -> rank -> Excel, with status lines.

Used by run.py and by the dashboard's "Refresh jobs" button:
    python -m src.pipeline [--skip-scrape] [--rescore-all] [--reset-preferences] [--model NAME]

Each step prints one status line, e.g.
    [3/5] Scraping jobs... RemoteOK 34, Remotive 21, WWR 18 (12 duplicates removed)
If a step fails, the run stops with a plain explanation of what went wrong and how to fix it.
Details always go to logs/run.log.
"""
import argparse
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import ConfigError, load_json
from .excel_builder import OUTPUT_FILE, TOP, LOW, UNSURE, EXCLUDED, WorkbookLocked, build
from .log import setup_logging
from .ollama_client import OllamaError
from .paths import PREFERENCES_FILE, ROOT
from .questionnaire import QuestionnaireAborted, has_console, load_or_ask, with_defaults
from .resume_parser import ResumeError, parse_resume, summary_line as resume_line
from .scrapers.base import SourceError
from .scrapers.runner import bucket_line, scrape, summary_line as scrape_line
from .workbook_lock import WorkbookBusy

log = logging.getLogger("jobtracker.pipeline")
Report = Callable[[str], None]
STEPS = ["Reading resume", "Loading preferences", "Scraping jobs", "Ranking", "Building Excel"]
INDENT = "      "


@dataclass
class Options:
    """Choices for one run (the run.py flags)."""
    skip_scrape: bool = False
    rescore_all: bool = False
    reset_preferences: bool = False
    model: str | None = None
    prefs_file: Path | None = None   # testing: use these preferences instead of config/
    interactive: bool | None = None  # None = ask questions only when there is a console
    workbook: Path | None = None     # testing: build this workbook instead of output/


class StepFailed(Exception):
    """A step failed. The message says what went wrong and how to fix it."""


def head(n: int) -> str:
    return f"[{n}/{len(STEPS)}] {STEPS[n - 1]}..."


def relative(path: Path) -> str:
    """output/Job_Rankings.xlsx rather than a long absolute path, when inside the project."""
    try:
        return str(path.resolve().relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


# ---------------------------------------------------------------- steps

def read_resume(opts: Options, state: dict, report: Report) -> str:
    try:
        state["resume"] = parse_resume()
    except ResumeError as exc:
        raise StepFailed(str(exc)) from exc
    return resume_line(state["resume"])


def load_preferences(opts: Options, state: dict, report: Report) -> str:
    if opts.prefs_file:
        state["prefs"] = with_defaults(load_json(opts.prefs_file))
        return f"using {opts.prefs_file.name}"
    interactive = has_console() if opts.interactive is None else opts.interactive
    if interactive:
        report(head(2))  # the questions (or "Use these again?") come next
    try:
        state["prefs"], how = load_or_ask(interactive=interactive, reset=opts.reset_preferences,
                                          resume_titles=state["resume"].titles)
    except QuestionnaireAborted as exc:
        raise StepFailed(str(exc)) from exc
    except ConfigError as exc:
        raise StepFailed(f"{exc} (or run with --reset-preferences to answer the questions again)") from exc
    return {"saved": "using saved answers", "updated": "updated your answers",
            "new": f"saved your answers to {relative(PREFERENCES_FILE)}"}[how]


def scrape_jobs(opts: Options, state: dict, report: Report) -> str:
    if opts.skip_scrape:
        return "skipped (--skip-scrape), using the jobs from the last scrape"

    def source_line(line: str) -> None:
        log.info(line)
        if "FAILED" in line or "[" in line:  # failures and warnings only; the rest is in the log
            report(f"{INDENT}! {line}")

    try:
        outcome = scrape(report=source_line, progress_desc=head(3)[:-3])
    except (SourceError, ConfigError) as exc:
        raise StepFailed(str(exc)) from exc
    if outcome.sources and all(r.error for r in outcome.sources):
        raise StepFailed("Could not reach any job site. Check your internet connection and try again. "
                         "Details are in logs/run.log.")
    state["scrape_note"] = bucket_line(outcome)
    return scrape_line(outcome)


def rank_jobs(opts: Options, state: dict, report: Report) -> str:
    from .ranker import rank, summary_line as rank_line  # imported here: loads tqdm and the cache

    try:
        result = rank(state["resume"], state["prefs"], opts.model, opts.rescore_all,
                      report=lambda line: report(f"{INDENT}{line}"), progress_desc=head(4)[:-3])
    except OllamaError as exc:
        raise StepFailed(str(exc)) from exc
    except FileNotFoundError as exc:
        raise StepFailed("There are no scraped jobs to rank yet. Run again without --skip-scrape.") from exc
    line = rank_line(result)
    if result.failed:
        line += f" ({result.failed} could not be reviewed; they will be retried next run)"
    return line


def build_excel(opts: Options, state: dict, report: Report) -> str:
    try:
        built = build(out=opts.workbook) if opts.workbook else build()
    except (WorkbookLocked, WorkbookBusy) as exc:
        raise StepFailed(f"{exc} Your rankings are saved; run again with --skip-scrape to only redo "
                         f"this step.") from exc
    c = built.counts
    state["excel_note"] = f"{c[UNSURE]} unsure location and {c[EXCLUDED]} excluded jobs are on their own tabs"
    return f"{c[TOP]} top matches, {c[LOW]} low matches -> {relative(built.path)}"


STEP_FUNCTIONS = [read_resume, load_preferences, scrape_jobs, rank_jobs, build_excel]
NOTES = {3: "scrape_note", 5: "excel_note"}  # an extra indented line after some steps


# ---------------------------------------------------------------- running

def elapsed(seconds: float) -> str:
    minutes, secs = divmod(round(seconds), 60)
    return f"{minutes} min {secs} s" if minutes else f"{secs} s"


def run(opts: Options, report: Report = print) -> int:
    """Run every step. Returns 0 on success, 1 if a step failed, 130 if stopped with Ctrl+C."""
    started = time.monotonic()
    log.info("Run started: %s", opts)
    state: dict = {}
    for n, step in enumerate(STEP_FUNCTIONS, 1):
        try:
            result = step(opts, state, report)
        except StepFailed as exc:
            log.warning("Step %d (%s) failed: %s", n, STEPS[n - 1], exc)
            report(f"{head(n)} STOPPED")
            report(f"{INDENT}{exc}")
            return 1
        except KeyboardInterrupt:
            log.warning("Stopped with Ctrl+C during step %d", n)
            report(f"\nStopped during step {n} ({STEPS[n - 1].lower()}). Jobs already ranked are saved; "
                   f"run again to pick up where it stopped.")
            return 130
        except Exception as exc:  # unexpected: full details in the log, a plain line here
            log.exception("Step %d (%s) crashed", n, STEPS[n - 1])
            report(f"{head(n)} STOPPED")
            report(f"{INDENT}Something unexpected went wrong ({exc.__class__.__name__}: {exc}). "
                   f"The details are in logs/run.log. Running again often helps; if it keeps "
                   f"happening, share that log file.")
            return 1
        report(f"{head(n)} {result}")
        log.info("%s %s", head(n), result)
        if n in NOTES and state.get(NOTES[n]):
            report(f"{INDENT}{state[NOTES[n]]}")
    out = opts.workbook or OUTPUT_FILE
    report(f"Done in {elapsed(time.monotonic() - started)}. Open {relative(out)}, "
           f"or double-click dashboard.bat to track your applications.")
    log.info("Run finished in %.0fs", time.monotonic() - started)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Command line entry point (run.py calls this)."""
    parser = argparse.ArgumentParser(
        prog="run.py", description="Find remote jobs, rank them against your resume, and update the "
                                   "Excel tracker. Everything runs on this computer.")
    parser.add_argument("--skip-scrape", action="store_true",
                        help="don't search job sites; rank the jobs from the last search again")
    parser.add_argument("--rescore-all", action="store_true",
                        help="ignore saved scores and review every job again (slow)")
    parser.add_argument("--reset-preferences", action="store_true",
                        help="answer the preference questions again")
    parser.add_argument("--model", metavar="NAME",
                        help="Ollama model to use for this run, e.g. qwen2.5:7b")
    parser.add_argument("--prefs", type=Path, help=argparse.SUPPRESS)       # testing only
    parser.add_argument("--workbook", type=Path, help=argparse.SUPPRESS)    # testing only
    parser.add_argument("--no-input", action="store_true", help=argparse.SUPPRESS)  # dashboard refresh
    args = parser.parse_args(argv)
    log_file = setup_logging()
    opts = Options(args.skip_scrape, args.rescore_all, args.reset_preferences, args.model, args.prefs,
                   False if args.no_input else None, args.workbook)
    code = run(opts)
    if code == 1:
        print(f"(Full details: {relative(log_file)})")
    return code


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace", line_buffering=True)
    sys.exit(main())
