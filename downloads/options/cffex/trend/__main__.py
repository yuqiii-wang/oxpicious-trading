"""downloads.options.cffex.trend — Download CFFEX daily options trend data.

This module downloads CFFEX options-specific data by:
  1. Checking stats.options_identity for the latest date (DB skip)
  2. Scanning existing _options.csv files for available dates (CSV skip)
  3. Backfilling missing dates from shared archive/trend CSVs
  4. Downloading remaining missing dates via Playwright browser automation
  5. Saving to temps/cffex_options_trend/YYYYMM/YYYYMMDD_options.csv

Usage:
  python -m downloads.options.cffex.trend
  python -m downloads.options.cffex.trend --start-date 2026-08-01 --end-date 2026-08-15
  python -m downloads.options.cffex.trend --force
  python -m downloads.options.cffex.trend --backfill
"""

from __future__ import annotations

import argparse
import shutil
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

from _common.build_commons import (
    setup_utf8_stdout,
    print_build_header,
    print_wall_time,
    TODAY_STR,
)
from _common._holidays_and_weekdays import (
    is_trading_day,
    business_days,
)
from _common.db_commons import get_db_connection

setup_utf8_stdout()

from downloads._common.core import setup_logger, resolve_out_dir
from downloads.options.cffex.trend.config import (
    CFFEX_TREND_URL,
    DOWNLOAD_SLEEP_SEC,
    _last_completed_archive_month,
)
from downloads.options.cffex.trend.paths import (
    get_trend_dir,
    get_archive_dir,
    get_futures_trend_dir,
    list_trend_dates,
    list_shared_options_dates,
    shared_options_csv_paths_for_date,
    trend_options_csv_path,
    get_trend_month_dir,
)
from downloads.options.cffex.trend.downloader import (
    download_trend_batch,
)

logger = setup_logger("cffex_options_trend")


# ---------------------------------------------------------------------------
# Step 1: Check SQL for latest date
# ---------------------------------------------------------------------------

def get_latest_db_date() -> Optional[date]:
    """Query stats.options_identity for the latest date.

    Returns:
        Latest date in the database, or None if table is empty/missing.
    """
    try:
        conn = get_db_connection()
        row = conn.execute(
            "SELECT MAX(date) as max_date FROM stats.options_identity"
        ).fetchone()
        conn.close()
        if row and row[0]:
            return row[0]
    except Exception as e:
        logger.warning("DB check failed (table may not exist): %s", e)
    return None


# ---------------------------------------------------------------------------
# Step 2: Backfill from shared CSVs (archive + futures trend)
# ---------------------------------------------------------------------------

def backfill_from_shared_csvs(
    trend_dir: Path,
    target_dates: Set[date],
) -> int:
    """Copy options CSV files from shared directories to options trend directory.

    Args:
        trend_dir: Path to options trend output directory.
        target_dates: Set of dates to backfill.

    Returns:
        Number of files copied.
    """
    copied = 0
    archive_dir = get_archive_dir()
    futures_trend_dir = get_futures_trend_dir()

    for d in sorted(target_dates):
        ym = d.strftime("%Y%m")
        ymd = d.strftime("%Y%m%d")

        # Check our own directory first
        dst_dir = get_trend_month_dir(ym)
        dst = dst_dir / f"{ymd}_options.csv"
        if dst.exists() and dst.stat().st_size > 100:
            continue

        # Check shared sources in order of preference
        for src_dir, label in [
            (archive_dir, "archive"),
            (futures_trend_dir, "futures_trend"),
        ]:
            if not src_dir.exists():
                continue
            src = src_dir / ym / f"{ymd}_options.csv"
            if src.exists():
                dst_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                copied += 1
                logger.info("  Copied %s from %s", ymd, label)
                break
            # Also check combined CSV (fallback)
            src_combined = src_dir / ym / f"{ymd}_1.csv"
            if src_combined.exists():
                # Split combined CSV into options-only
                import csv as csv_mod
                from downloads.futures.cffex.archive.__main__ import (
                    _split_csv_futures_options,
                )
                # Use archive's split logic but only keep options
                try:
                    _split_csv_futures_options(src_combined, dst_dir, logger_tag=f"[backfill {ymd}]")
                    # After splitting, check if options file was created
                    if dst.exists() and dst.stat().st_size > 100:
                        copied += 1
                        logger.info("  Split %s from combined CSV in %s", ymd, label)
                        break
                except Exception as e:
                    logger.warning("  Failed to split %s from %s: %s", ymd, label, e)

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

    A date is missing if:
      1. It's a trading day
      2. It's not in the database
      3. It's not in the local trend files
      4. It's within the specified date range

    Args:
        latest_db_date: Latest date in the database (None if empty).
        trend_dates: Set of dates with local trend files.
        start_date: Start of date range (None = unlimited).
        end_date: End of date range (None = today).

    Returns:
        Sorted list of dates to download.
    """
    today = date.today()
    if end_date is None:
        end_date = today

    # Determine the earliest date to consider
    if start_date is None:
        last_archive = _last_completed_archive_month()
        if last_archive.month == 12:
            start_from = date(last_archive.year + 1, 1, 1) - timedelta(days=1)
        else:
            start_from = date(last_archive.year, last_archive.month + 1, 1) - timedelta(days=1)
        if latest_db_date and latest_db_date > start_from:
            start_from = latest_db_date
    else:
        start_from = start_date

    missing: List[date] = []
    d = start_from
    while d <= end_date:
        if is_trading_day(d):
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
        description="Download CFFEX options daily trend data using Playwright.",
    )
    parser.add_argument(
        "--start-date",
        default=None,
        help="Start date (YYYY-MM-DD). Default: day after last archive month.",
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
        help="Backfill from shared archive/trend CSVs before downloading.",
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
        "DOWNLOAD CFFEX OPTIONS TREND DATA  ·  Playwright browser automation",
        **{
            "Trend URL": CFFEX_TREND_URL,
            "Date range": f"{start_date or '(auto)'} → {end_date or '(today)'}",
            "Sleep interval": f"{args.sleep}s",
            "Force": args.force,
        }
    )

    trend_dir = get_trend_dir(args.out_root)
    archive_dir = get_archive_dir(args.out_root)
    futures_trend_dir = get_futures_trend_dir(args.out_root)

    print(f"\n  Options trend dir: {trend_dir}")
    print(f"  Shared archive dir: {archive_dir}")
    print(f"  Shared futures trend dir: {futures_trend_dir}")

    # ------------------------------------------------------------------
    # Step 1: Check SQL for latest date
    # ------------------------------------------------------------------
    print("\n[1/4] Checking database (stats.options_identity) for latest date ...", flush=True)
    latest_db_date = get_latest_db_date()
    if latest_db_date:
        print(f"    Latest DB date: {latest_db_date}", flush=True)
    else:
        print("    No data in database (will download everything)", flush=True)

    # ------------------------------------------------------------------
    # Step 2: Check local + shared trend files
    # ------------------------------------------------------------------
    print("\n[2/4] Checking local + shared options CSV files ...", flush=True)
    trend_dates = list_trend_dates(args.out_root)
    shared_dates = list_shared_options_dates(args.out_root)
    all_available = trend_dates | shared_dates

    latest_trend = max(trend_dates) if trend_dates else None
    if latest_trend:
        print(f"    Own trend files: {len(trend_dates)} dates (latest: {latest_trend})", flush=True)
    else:
        print("    No own trend files found", flush=True)

    if shared_dates:
        latest_shared = max(shared_dates) if shared_dates else None
        print(f"    Shared CSV files: {len(shared_dates)} dates (latest: {latest_shared})", flush=True)
    else:
        print("    No shared CSV files found", flush=True)

    # ------------------------------------------------------------------
    # Step 3: Backfill from shared CSVs (if requested or own dir is empty)
    # ------------------------------------------------------------------
    if args.backfill or not trend_dates:
        print("\n[3/4] Backfilling from shared CSVs ...", flush=True)
        if not archive_dir.exists() and not futures_trend_dir.exists():
            print("    Neither archive nor futures trend dir exists, skipping backfill", flush=True)
        else:
            last_archive = _last_completed_archive_month()
            backfill_dates: Set[date] = set()

            if not trend_dates:
                # First run: backfill ALL dates up to last completed month
                d = date(2020, 1, 1)
                while d <= last_archive:
                    if is_trading_day(d):
                        backfill_dates.add(d)
                    d += timedelta(days=1)
            else:
                # Only backfill dates up to the latest trend date
                latest = latest_trend or date(2020, 1, 1)
                d = date(2020, 1, 1)
                while d <= max(latest, last_archive):
                    if is_trading_day(d):
                        backfill_dates.add(d)
                    d += timedelta(days=1)

            missing_backfill = backfill_dates - trend_dates
            if missing_backfill:
                print(f"    {len(missing_backfill)} dates to backfill from shared CSVs", flush=True)
                n_copied = backfill_from_shared_csvs(trend_dir, missing_backfill)
                print(f"    Copied {n_copied} files from shared CSVs", flush=True)
            else:
                print("    No dates need backfilling", flush=True)

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
        all_trading_days: List[date] = []
        d = start_date or date(2020, 1, 1)
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
