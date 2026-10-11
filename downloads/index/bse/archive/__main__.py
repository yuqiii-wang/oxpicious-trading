"""Entry point: ``python -m downloads.index.bse.archive``.

Refreshes the BSE index daily archive CSVs (temps/bse_index_archive) from
www.bse.cn — each index page's own kline endpoint (北证50 bse_bz50.html,
北证专精特新 bse_zjtx.html; see ``BSE_INDEX_ENDPOINTS``). Serves BOTH the
historical archive and the daily trend: each endpoint always returns the
full daily series (899050 base date 2022-04-29, 899601 base date 2024-06-28
→ now), the run appends missing dates, and the 19:00 daily schedule lands
each session's final row — the same one-CSV-per-code convention as
``downloads.index.cnindex.archive``.
"""
from __future__ import annotations

import argparse

from .runner import SERVICE, download_bse_index_archive

from _common.log_setup import setup_logging  # noqa: E402
logger = setup_logging("bse_index_archive")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--code", type=str, default=None,
        help="Download a single index code then exit (e.g. --code 899050 or "
             "--code 899601).",
    )
    ap.add_argument(
        "--force", action="store_true",
        help="Rewrite the history CSV entirely from the API (default: "
             "append-only merge).",
    )
    ap.add_argument(
        "--sleep-sec", type=float, default=None,
        help="Sleep seconds between requests "
             f"(default: external_request_config JSON for {SERVICE})",
    )
    args = ap.parse_args()
    from _common.service_heartbeat import autoregister  # lazy: keeps pandas behind the cudf hook
    autoregister()  # heartbeat + run audit (monitor.*)
    logger.info(download_bse_index_archive(
        index_codes=[args.code] if args.code else None,
        force=args.force,
        sleep_sec=args.sleep_sec,
    ))
