"""CLI entry point: python -m downloads.index.csindex.composition.

See ``runner.download_index_composition`` for the actual logic.
"""
from __future__ import annotations

import argparse

from ._config import SLEEP_SEC
from .runner import download_index_composition


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Download CSI index composition (closeweight) xls and convert to CSV."
    )
    ap.add_argument(
        "--index-codes", type=str, default=None,
        help="Comma-separated list of index codes (default: all from sec_classification.json). "
             "Example: --index-codes 930606,000300,399997",
    )
    ap.add_argument(
        "--out-root", type=str, default=None,
        help="Alternative output root directory",
    )
    ap.add_argument(
        "--no-skip-cached", action="store_true", default=False,
        help="Re-download even if a cached CSV exists",
    )
    ap.add_argument(
        "--force-month-start", action="store_true", default=False,
        help="Force monthly-refresh behavior: bypass cache and stamp CSVs with "
             "today's date (overrides xls snapshot_date). For testing the "
             "monthly refresh flow on any day.",
    )
    ap.add_argument(
        "--sleep-sec", type=float, default=SLEEP_SEC,
        help=f"Sleep seconds between downloads (default: {SLEEP_SEC})",
    )
    return ap


if __name__ == "__main__":
    args = _build_parser().parse_args()

    codes = None
    if args.index_codes:
        codes = [c.strip() for c in args.index_codes.split(",") if c.strip()]

    result = download_index_composition(
        index_codes=codes,
        out_root=args.out_root,
        skip_cached=not args.no_skip_cached,
        sleep_sec=args.sleep_sec,
        force_month_start=args.force_month_start,
    )
    print(result)
