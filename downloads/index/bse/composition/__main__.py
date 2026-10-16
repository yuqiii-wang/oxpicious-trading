"""CLI entry point: python -m downloads.index.bse.composition.

Downloads the 北证50 (899050) / 北证专精特新 (899601) composition 成分股列表
(constituent + weight closeweight snapshots, CSI co-published autofile) —
see ``runner.download_bse_index_composition`` for the logic.
"""
from __future__ import annotations

import argparse

from ._config import SERVICE
from .runner import download_bse_index_composition

from _common.log_setup import setup_logging  # noqa: E402
logger = setup_logging("bse_index_composition")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Download BSE index composition (CSI co-published closeweight) "
                    "xls and convert to CSV.",
    )
    ap.add_argument(
        "--code", type=str, default=None,
        help="Download a single index code then exit (e.g. --code 899050 or "
             "--code 899601). Default: both BSE_INDEX_CODES.",
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
        "--sleep-sec", type=float, default=None,
        help="Sleep seconds between downloads "
             f"(default: external_request_config JSON for {SERVICE})",
    )
    args = ap.parse_args()
    from _common.service_heartbeat import autoregister  # lazy: keeps pandas behind the cudf hook
    autoregister()  # heartbeat + run audit (monitor.*)

    result = download_bse_index_composition(
        index_codes=[args.code] if args.code else None,
        out_root=args.out_root,
        skip_cached=not args.no_skip_cached,
        sleep_sec=args.sleep_sec,
        force_month_start=args.force_month_start,
    )
    logger.info(result)
