"""Run the scrapers on their own:  python -m src.scrapers"""
import sys

from ..log import setup_logging
from .base import SourceError
from .runner import RAW_FILE, UNSURE_FILE, bucket_line, scrape, summary_line


def main() -> int:
    """Scrape all enabled sources and print a summary."""
    log_path = setup_logging()
    print("Scraping job sources (details in", log_path, ")")
    try:
        outcome = scrape(report=lambda line: print("  " + line))
    except SourceError as exc:
        print(f"Scrape problem: {exc}")
        return 1
    print(summary_line(outcome))
    print(bucket_line(outcome))
    print(f"Remote US jobs: {RAW_FILE}")
    print(f"Unsure jobs:    {UNSURE_FILE} (and jobs_excluded.json)")
    return 0 if any(not r.error for r in outcome.sources) else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace", line_buffering=True)
    sys.exit(main())
