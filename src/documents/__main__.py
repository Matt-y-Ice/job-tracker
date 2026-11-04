"""Make documents for one job from the command line:  python -m src.documents JOB_ID"""
import argparse
import sys
import time

from ..log import setup_logging
from .generator import DocumentError
from .service import produce_documents
from .writer import DOCS_DIR


def main() -> int:
    parser = argparse.ArgumentParser(description="Write a tailored resume and cover letter for one job.")
    parser.add_argument("job_id", help="the Job ID from the workbook (last column)")
    args = parser.parse_args()
    setup_logging()
    started = time.monotonic()
    try:
        meta = produce_documents(args.job_id, progress=print)
    except DocumentError as exc:
        print(f"Could not make documents: {exc}")
        return 1
    print(f"Done in {time.monotonic() - started:.0f}s. Files in {DOCS_DIR}:")
    for f in meta["files"]:
        print(f"  {f['kind']} ({f['format']}): {f['name']}")
    if meta["pdf_note"]:
        print(f"  {meta['pdf_note']}")
    for note in meta["notes"]:
        print(f"  fact check: {note}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace", line_buffering=True)
    sys.exit(main())
