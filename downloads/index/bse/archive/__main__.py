"""Entry point: ``python -m downloads.index.bse.archive``.

Refreshes the BSE index daily archive CSVs (temps/bse_index_archive) from
www.bse.cn — the 北证50 page's own kline endpoint. Serves BOTH the historical
archive and the daily trend: the endpoint always returns the full daily
series (base date 2022-04-29 → now), the run appends missing dates, and the
19:00 daily schedule lands each session's final row — the same
one-CSV-per-code convention as ``downloads.index.cnindex.archive``.
"""
from __future__ import annotations

import argparse

from .runner import download_bse_index_archive

from _common.log_setup import setup_logging  # noqa: E402
logger = setup_logging("bse_index_archive")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--code", type=str, default=None,
        help="Download a single index code then exit (e.g. --code 899050).",
    )
    ap.add_argument(
        "--force", action="store_true",
        help="Rewrite the history CSV entirely from the API (default: "
             "append-only merge).",
    )
    args = ap.parse_args()
    logger.info(download_bse_index_archive(
        index_codes=[args.code] if args.code else None,
        force=args.force,
    ))
