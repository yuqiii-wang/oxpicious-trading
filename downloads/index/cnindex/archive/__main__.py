"""Entry point: ``python -m downloads.index.cnindex.archive``.

Refreshes the CNINDEX daily archive CSVs (temps/cnindex_archive) from
hq.cnindex.com.cn — the only source covering 国证2000 (399303), 国证A50
(399310) and 国证1000 (399311). Content-gated incremental: codes whose
history CSV already covers the newest publishable session are skipped;
lags the publish by design at most one run.
"""
from __future__ import annotations

import argparse

from .runner import download_cnindex_archive

from _common.log_setup import setup_logging  # noqa: E402
logger = setup_logging("cnindex_archive")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--code", type=str, default=None,
        help="Download a single index code then exit (e.g. --code 399303).",
    )
    ap.add_argument(
        "--force", action="store_true",
        help="Rewrite the history CSV entirely from the API (default: "
             "append-only merge).",
    )
    args = ap.parse_args()
    logger.info(download_cnindex_archive(
        index_codes=[args.code] if args.code else None,
        force=args.force,
    ))
