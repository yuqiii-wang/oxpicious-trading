"""downloads.futures.cffex.trend — Download CFFEX daily trend data using Playwright.

This module downloads daily futures/options settlement data from the CFFEX
"日统计" (Daily Statistics) page and saves it as CSV files.

Workflow:
  1. Check SQL (stats.futures_identity) for the latest date
  2. Check local trend CSV files for the latest date
  3. If trend dir is empty or has gaps, backfill from archive CSVs
  4. From today backwards, find missing dates not in DB
  5. Use Playwright to download missing dates one by one
  6. Output: temps/cffex_trend/YYYYMM/YYYYMMDD_futures.csv + _options.csv

Usage:
  python -m downloads.futures.cffex.trend
  python -m downloads.futures.cffex.trend --start-date 2026-08-01 --end-date 2026-08-15
  python -m downloads.futures.cffex.trend --force
  python -m downloads.futures.cffex.trend --backfill  # backfill from archive first
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import warnings
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional, Set

warnings.filterwarnings("ignore")

# Project root setup — MUST be before any project imports
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import numpy as np
import pandas as pd

from _common.build_commons import (
    setup_utf8_stdout,
    print_build_header,
    print_wall_time,
    TODAY_STR,
)
from _common._holidays_and_weekdays import (
    is_trading_day,
    last_business_day,
    business_days,
)
from _common.db_commons import get_db_connection
from _common.pre_check_and_load.identity import check_identity

setup_utf8_stdout()

from downloads._common.core import setup_logger, resolve_out_dir
from downloads.futures.cffex.trend.config import (
    CFFEX_TREND_URL,
    DOWNLOAD_SLEEP_SEC,
    _last_completed_archive_month,
)
from downloads.futures.cffex.trend.paths import (
    get_trend_dir,
    get_trend_month_dir,
    list_trend_dates,
    trend_futures_csv_path,
    trend_options_csv_path,
)
from downloads.futures.cffex.trend.downloader import (
    download_trend_batch,
)

logger = setup_logger("cffex_trend")


# ---------------------------------------------------------------------------
# Step 1: Check SQL for latest date
# ---------------------------------------------------------------------------

def get_latest_db_date() -> Optional[date]:
    """Query stats.futures_identity for the latest date.

    Returns:
        Latest date in the database, or None if table is empty/missing.
    """
    try:
        conn = get_db_connection()
        row = conn.execute(
            "SELECT MAX(date) as max_date FROM stats.futures_identity"
        ).fetchone()
        conn.close()
        if row and row[0]:
            return row[0]
    except Exception as e:
        logger.warning("DB check failed (table may not exist): %s", e)
    return None


# ---------------------------------------------------------------------------
# Step 2: Backfill from archive
# ---------------------------------------------------------------------------

def backfill_from_archive(
    trend_dir: Path,
    archive_dir: Path,
    target_dates: Set[date],
) -> int:
    """Copy archive CSV files to trend directory for missing dates.

    This ensures the trend directory has historical data to work with.

    Args:
        trend_dir: Path to trend output directory.
        archive_dir: Path to archive source directory.
        target_dates: Set of dates to backfill.

    Returns:
        Number of files copied.
    """
    import shutil

    copied = 0
    for d in sorted(target_dates):
        ym = d.strftime("%Y%m")
        ymd = d.strftime("%Y%m%d")
        archive_month_dir = archive_dir / ym

        if not archive_month_dir.exists():
            continue

        # Check for futures and options files
        for suffix in ["_futures.csv", "_options.csv"]:
            src = archive_month_dir / f"{ymd}{suffix}"
            if src.exists():
                dst_dir = trend_dir / ym
                dst_dir.mkdir(parents=True, exist_ok=True)
                dst = dst_dir / f"{ymd}{suffix}"
                if not dst.exists():
                    shutil.copy2(src, dst)
                    copied += 1
                    logger.info("  Copied %s%s from archive", ymd, suffix)

    return copied


# ---------------------------------------------------------------------------
# Step 3: Find missing dates
# ---------------------------------------------------------------------------

def find_missing_dates(
    latest_db_date: Optional[date],
    trend_dates: Set[date],
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> List[date]:
    """Find dates that need to be downloaded.

    Default behaviour (no explicit --start-date): scan the **current month**
    from day 1 to today, flagging every trading day that is missing from
    either the database or the local trend files.

    When an explicit --start-date is supplied, the original
    "latest-DB-date forward" logic is retained for historical ranges.

    Args:
        latest_db_date: Latest date in the database (None if empty).
        trend_dates: Set of dates with local trend files.
        start_date: Start of date range (None = current-month scan).
        end_date: End of date range (None = today).

    Returns:
        Sorted list of dates to download.
    """
    today = date.today()
    if end_date is None:
        end_date = today

    # Determine the earliest date to consider
    if start_date is None:
        # Default: scan the **entire current month** (day 1 → today) so
        # that gap dates earlier in the month are not silently skipped.
        start_from = today.replace(day=1)
    else:
        start_from = start_date

    # ------------------------------------------------------------------
    # Build the set of dates already present in the DB for the target
    # window.  For the current month we query the DB directly (catches
    # mid-month gaps); for historical ranges we keep the lightweight
    # MAX(date) comparison.
    # ------------------------------------------------------------------
    current_month_missing_from_db: Set[date] = set()
    use_current_month_scan: bool = False
    if start_date is None and start_from.month == today.month:
        # Query ALL dates in the current month from stats.futures_identity
        # This returns trading days that are completely absent from the DB
        try:
            current_month_missing_from_db = check_identity(
                "stats.futures_identity",
                start_from,
                end_date,
            )
            use_current_month_scan = True
        except Exception:
            # Table may not exist yet — fall back to MAX(date) comparison
            logger.warning(
                "check_identity query failed, falling back to MAX(date) logic"
            )

    missing: List[date] = []
    d = start_from
    while d <= end_date:
        if not is_trading_day(d):
            d += timedelta(days=1)
            continue

        if use_current_month_scan and d.month == today.month:
            # Current-month logic: use the pre-computed gap set from check_identity
            not_in_db = d in current_month_missing_from_db
        else:
            # Historical / fallback logic: date must be after the latest DB entry
            not_in_db = latest_db_date is None or d > latest_db_date

        not_in_trend = d not in trend_dates
        if not_in_db and not_in_trend:
            missing.append(d)

        d += timedelta(days=1)

    return sorted(missing)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download CFFEX daily trend data using Playwright browser automation.",
    )
    parser.add_argument(
        "--start-date",
        default=None,
        help="Start date (YYYY-MM-DD). Default: 1st of current month (scan for gaps).",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="End date (YYYY-MM-DD). Default: today.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download all dates even if already cached locally.",
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Backfill from archive before downloading trend data.",
    )
    parser.add_argument(
        "--out-root",
        default=None,
        help="Override output directory root.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=DOWNLOAD_SLEEP_SEC,
        help=f"Sleep seconds between downloads (default: {DOWNLOAD_SLEEP_SEC}).",
    )
    args = parser.parse_args()

    t0 = time.time()

    start_date = None
    end_date = None
    if args.start_date:
        start_date = datetime.strptime(args.start_date, "%Y-%m-%d").date()
    if args.end_date:
        end_date = datetime.strptime(args.end_date, "%Y-%m-%d").date()

    print_build_header(
        "DOWNLOAD CFFEX TREND DATA  ·  Playwright browser automation",
        **{
            "Trend URL": CFFEX_TREND_URL,
            "Date range": f"{start_date or '(auto)'} → {end_date or '(today)'}",
            "Sleep interval": f"{args.sleep}s",
            "Force": args.force,
        }
    )

    # Get directories
    trend_dir = get_trend_dir(args.out_root)
    archive_dir = _PROJECT_ROOT / "temps" / "cffex_archive"

    print(f"\n  Trend dir: {trend_dir}")
    print(f"  Archive dir: {archive_dir}")

    # ------------------------------------------------------------------
    # Step 1: Check SQL for latest date
    # ------------------------------------------------------------------
    print("\n[1/4] Checking database for latest date ...", flush=True)
    latest_db_date = get_latest_db_date()
    if latest_db_date:
        print(f"    Latest DB date: {latest_db_date}", flush=True)
    else:
        print("    No data in database (will download everything)", flush=True)

    # ------------------------------------------------------------------
    # Step 2: Check local trend files
    # ------------------------------------------------------------------
    print("\n[2/4] Checking local trend files ...", flush=True)
    trend_dates = list_trend_dates(args.out_root)
    latest_trend = max(trend_dates) if trend_dates else None
    if latest_trend:
        print(f"    Latest trend file: {latest_trend} ({len(trend_dates)} files)", flush=True)
    else:
        print("    No trend files found", flush=True)

    # ------------------------------------------------------------------
    # Step 3: Backfill from archive (if requested or trend dir is empty)
    # ------------------------------------------------------------------
    if args.backfill or not trend_dates:
        print("\n[3/4] Backfilling from archive ...", flush=True)
        if not archive_dir.exists():
            print("    Archive directory not found, skipping backfill", flush=True)
        else:
            # Determine dates to backfill: all dates up to the last completed month
            last_archive = _last_completed_archive_month()
            backfill_dates: Set[date] = set()

            # Find all trading days from 2020-01-01 to last_archive
            d = date(2020, 1, 1)
            while d <= last_archive:
                if is_trading_day(d):
                    backfill_dates.add(d)
                d += timedelta(days=1)

            # Only backfill dates not already in trend
            missing_backfill = backfill_dates - trend_dates
            if missing_backfill:
                print(f"    {len(missing_backfill)} dates to backfill from archive", flush=True)
                n_copied = backfill_from_archive(trend_dir, archive_dir, missing_backfill)
                print(f"    Copied {n_copied} files from archive", flush=True)
            else:
                print("    No dates need backfilling", flush=True)

            # Refresh trend_dates
            trend_dates = list_trend_dates(args.out_root)
    else:
        print("\n[3/4] Skipping backfill (--backfill not requested)", flush=True)

    # ------------------------------------------------------------------
    # Step 4: Find missing dates and download
    # ------------------------------------------------------------------
    print("\n[4/4] Finding missing dates to download ...", flush=True)
    missing = find_missing_dates(
        latest_db_date, trend_dates, start_date, end_date,
    )

    if args.force:
        # In force mode, re-download all dates in range
        all_trading_days: List[date] = []
        if start_date is None:
            d = date.today().replace(day=1)
        else:
            d = start_date
        end_d = end_date or date.today()
        while d <= end_d:
            if is_trading_day(d):
                all_trading_days.append(d)
            d += timedelta(days=1)
        missing = all_trading_days

    if not missing:
        print("    No dates need downloading!", flush=True)
        print_wall_time(t0)
        return

    print(f"    {len(missing)} dates to download:", flush=True)
    if len(missing) <= 20:
        for d in missing:
            print(f"      {d}", flush=True)
    else:
        print(f"      First: {missing[0]}, Last: {missing[-1]}", flush=True)

    # ------------------------------------------------------------------
    # Download using Playwright
    # ------------------------------------------------------------------
    print(f"\n[Download] Starting Playwright download for {len(missing)} dates ...", flush=True)
    print("    (This may take a while — CFFEX anti-bot protection requires delays)", flush=True)

    result = download_trend_batch(
        missing,
        out_root=args.out_root,
        sleep_sec=args.sleep,
    )

    print(f"\n[Done] Download summary:", flush=True)
    print(f"  Downloaded: {result['downloaded']}", flush=True)
    print(f"  Skipped:    {result['skipped']}", flush=True)
    print(f"  No data:    {result['no_data']} (holidays/weekends)", flush=True)
    print(f"  Failed:     {result['failed']}", flush=True)

    print_wall_time(t0)


if __name__ == "__main__":
    main()