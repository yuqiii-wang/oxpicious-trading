"""SZSE ETF archive entry point.

Dispatches to sub-commands:

  python -m downloads.etf.szse.archive              # market data (default)
  python -m downloads.etf.szse.archive market       # market data (explicit)
  python -m downloads.etf.szse.archive reports      # quarterly reports + CSV extraction

The default (no args) preserves backwards compatibility with main.sh which
calls ``python -m downloads.etf.szse.archive`` for the daily market archive.
"""
from __future__ import annotations

import argparse
import sys


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="downloads.etf.szse.archive",
        description="SZSE ETF archive: market data and quarterly reports.",
    )
    ap.add_argument(
        "subcommand",
        nargs="?",
        default="market",
        choices=["market", "reports"],
        help="Sub-command to run (default: market).",
    )
    # Market sub-command options
    ap.add_argument("--start-date", type=str, default="2020-01-01",
                    help="Market: start date (default: 2020-01-01)")
    ap.add_argument("--end-date", type=str, default=None,
                    help="Market: end date (default: today)")
    ap.add_argument("--out-root", type=str, default=None,
                    help="Override output root directory")
    ap.add_argument("--sleep-sec", type=float, default=None,
                    help="Override sleep seconds between requests")
    # Reports sub-command options
    ap.add_argument("--etf-code", type=str, action="append", default=None,
                    help="Reports: process only this ETF code (bare 6-digit). "
                         "Repeatable. Default: all SZ ETFs from DB.")
    ap.add_argument("--max-etfs", type=int, default=None,
                    help="Reports: limit to N ETFs (dev/testing)")
    ap.add_argument("--no-extract", action="store_true", default=False,
                    help="Reports: skip PDF->CSV extraction")
    ap.add_argument("--no-other-only", action="store_true", default=False,
                    help="Reports: disable the default OTHER-classification "
                         "pre-filter (download ALL SZ ETFs, not just "
                         "sector_id='OTHER'). Ignored when --etf-code is given.")
    ap.add_argument("--no-lof", action="store_true", default=False,
                    help="Reports: do NOT add SZSE LOFs (16xxxx.SZ) to the "
                         "download universe. By default all active LOFs are "
                         "included even when --no-other-only is unset, since "
                         "LOF quarterly reports publish the same holdings "
                         "sections as ETF reports. Ignored when --etf-code "
                         "is given or when --no-other-only is set (the "
                         "all-active branch already contains LOFs).")
    args = ap.parse_args()

    if args.subcommand == "market":
        from downloads.etf.szse.archive.market import download_szse_archive_etf
        sleep = args.sleep_sec if args.sleep_sec is not None else 5.0
        result = download_szse_archive_etf(
            out_root=args.out_root,
            end_date=args.end_date,
            start_date=args.start_date,
            sleep_sec=sleep,
        )
        print(result)
    elif args.subcommand == "reports":
        from downloads._common.core import LONG_SLEEP_INTERVAL
        from downloads.etf.szse.archive.reports import download_szse_etf_reports
        sleep = args.sleep_sec if args.sleep_sec is not None else LONG_SLEEP_INTERVAL
        result = download_szse_etf_reports(
            out_root=args.out_root,
            etf_codes=args.etf_code,
            sleep_sec=sleep,
            extract=not args.no_extract,
            max_etfs=args.max_etfs,
            other_only=not args.no_other_only,
            include_lof=not args.no_lof,
            start_date=args.start_date,
        )
        print(result)


if __name__ == "__main__":
    main()
