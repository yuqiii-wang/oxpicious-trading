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

from downloads._common import setup_logger, resolve_out_dir
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

# CFFEX option contract code prefixes (matched by the build module too)
_CFFEX_CONTRACT_PREFIXES = ("IO%", "HO%", "MO%", "CO%")


# ---------------------------------------------------------------------------
# Step 1: Check SQL for latest date
# ---------------------------------------------------------------------------

def get_latest_db_date() -> Optional[date]:
    """Query stats.options_identity for the latest date with CFFEX data.

    Returns:
        Latest date with CFFEX entries in the database, or None if absent.
    """
    try:
        conn = get_db_connection()
        conditions = " OR ".join(
            ["contract_code LIKE %s" for _ in _CFFEX_CONTRACT_PREFIXES]
        )
        row = conn.execute(
            f"SELECT MAX(date) FROM stats.options_identity WHERE {conditions}",
            list(_CFFEX_CONTRACT_PREFIXES),
        ).fetchone()
        conn.close()
        if row and row[0]:
            return row[0]
    except Exception as e:
        logger.warning("DB check failed (table may not exist): %s", e)
    return None


def _cffex_missing_dates_in_range(
    start_date: date,
    end_date: date,
) -> Optional[Set[date]]:
    """Find trading days in [start_date, end_date] that lack CFFEX data.

    Queries stats.options_identity for dates with CFFEX contract codes
    (IO%, HO%, MO%, CO%) and returns the complement set of expected
    trading days that are completely absent.

    Returns None when the DB is unreachable — NOT an empty set, which
    would (wrongly) tell the caller that every date is already stored and
    silently turn the run into a no-op.

    This mirrors the build module's find_missing_cffex_dates() logic but
    works with the sync psycopg2 connection used by the downloader.
    """
    expected: Set[date] = set(business_days(start_date, end_date, reverse=False))
    if not expected:
        return set()

    try:
        conn = get_db_connection()
        conditions = " OR ".join(
            ["contract_code LIKE %s" for _ in _CFFEX_CONTRACT_PREFIXES]
        )
        sql = (
            f"SELECT DISTINCT date FROM stats.options_identity "
            f"WHERE date BETWEEN %s AND %s AND ({conditions})"
        )
        params = [start_date, end_date] + list(_CFFEX_CONTRACT_PREFIXES)
        rows = conn.execute(sql, params).fetchall()
        present: Set[date] = {r[0] for r in rows if r[0] is not None}
        conn.close()
        return expected - present
    except Exception as e:
        logger.warning("CFFEX-specific DB check failed: %s", e)
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

    Default behaviour (no explicit --start-date): scan the **current month**
    from day 1 to today, flagging every trading day that is missing CFFEX
    data in the database (not SZSE) OR missing from local/shared CSVs.

    When an explicit --start-date is supplied, the original
    "latest-DB-date forward" logic is retained for historical ranges.

    Args:
        latest_db_date: Latest date with CFFEX data in the DB.
        trend_dates: Set of dates with local (or shared) trend files.
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
        # Default: scan the **entire current month** (day 1 → today)
        start_from = today.replace(day=1)
    else:
        start_from = start_date

    # ------------------------------------------------------------------
    # Build the set of dates that are missing CFFEX data from the DB.
    # We use CFFEX-specific contract code filters (IO%, HO%, MO%, CO%)
    # so that SZSE entries in the same table don't mask CFFEX gaps.
    # ------------------------------------------------------------------
    cffex_missing_from_db: Optional[Set[date]] = None
    if start_date is None and start_from.month == today.month:
        # None = DB unreachable — NOT "nothing missing", which would
        # silently skip the whole month. When None, the logic below falls
        # back to the MAX(date) comparison against latest_db_date, which on
        # an unreachable DB is also None -> every date without a local or
        # shared CSV file is queued (the CSV files decide).
        cffex_missing_from_db = _cffex_missing_dates_in_range(
            start_from, end_date,
        )
        if cffex_missing_from_db is None:
            logger.warning(
                "DB unreachable — using local/shared CSV dates to find "
                "missing dates"
            )
    use_cffex_scan: bool = cffex_missing_from_db is not None

    missing: List[date] = []
    d = start_from
    while d <= end_date:
        if not is_trading_day(d):
            d += timedelta(days=1)
            continue

        if use_cffex_scan and d.month == today.month:
            # Current-month logic: use the CFFEX-specific gap set
            not_in_db = d in cffex_missing_from_db
        else:
            # Historical / fallback logic: date must be after the latest
            # CFFEX entry in the DB
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

    logger.info(f"\n  Options trend dir: {trend_dir}")
    logger.info(f"  Shared archive dir: {archive_dir}")
    logger.info(f"  Shared futures trend dir: {futures_trend_dir}")

    # ------------------------------------------------------------------
    # Step 1: Check SQL for latest date
    # ------------------------------------------------------------------
    logger.info("\n[1/4] Checking database (stats.options_identity) for latest date ...")
    latest_db_date = get_latest_db_date()
    if latest_db_date:
        logger.info(f"    Latest DB date: {latest_db_date}")
    else:
        logger.info(
            "    DB empty or unreachable — missing dates come from the "
            "local/shared CSV files"
        )

    # ------------------------------------------------------------------
    # Step 2: Check local + shared trend files
    # ------------------------------------------------------------------
    logger.info("\n[2/4] Checking local + shared options CSV files ...")
    trend_dates = list_trend_dates(args.out_root)
    shared_dates = list_shared_options_dates(args.out_root)
    all_available = trend_dates | shared_dates

    latest_trend = max(trend_dates) if trend_dates else None
    if latest_trend:
        logger.info(f"    Own trend files: {len(trend_dates)} dates (latest: {latest_trend})")
    else:
        logger.info("    No own trend files found")

    if shared_dates:
        latest_shared = max(shared_dates) if shared_dates else None
        logger.info(f"    Shared CSV files: {len(shared_dates)} dates (latest: {latest_shared})")
    else:
        logger.info("    No shared CSV files found")

    # ------------------------------------------------------------------
    # Step 3: Backfill from shared CSVs (if requested or own dir is empty)
    # ------------------------------------------------------------------
    if args.backfill or not trend_dates:
        logger.info("\n[3/4] Backfilling from shared CSVs ...")
        if not archive_dir.exists() and not futures_trend_dir.exists():
            logger.info("    Neither archive nor futures trend dir exists, skipping backfill")
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
                logger.info(f"    {len(missing_backfill)} dates to backfill from shared CSVs")
                n_copied = backfill_from_shared_csvs(trend_dir, missing_backfill)
                logger.info(f"    Copied {n_copied} files from shared CSVs")
            else:
                logger.info("    No dates need backfilling")

            trend_dates = list_trend_dates(args.out_root)
    else:
        logger.info("\n[3/4] Skipping backfill (--backfill not requested)")

    # ------------------------------------------------------------------
    # Step 4: Find missing dates and download
    # ------------------------------------------------------------------
    logger.info("\n[4/4] Finding missing dates to download ...")
    missing = find_missing_dates(
        latest_db_date, all_available, start_date, end_date,
    )

    if args.force:
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
        logger.info("    No dates need downloading!")
        print_wall_time(t0)
        return

    logger.info(f"    {len(missing)} dates to download:")
    if len(missing) <= 20:
        for d in missing:
            logger.info(f"      {d}")
    else:
        logger.info(f"      First: {missing[0]}, Last: {missing[-1]}")

    # ------------------------------------------------------------------
    # Download using Playwright
    # ------------------------------------------------------------------
    logger.info(f"\n[Download] Starting Playwright download for {len(missing)} dates ...")
    logger.info("    (This may take a while — CFFEX anti-bot protection requires delays)")

    result = download_trend_batch(
        missing,
        out_root=args.out_root,
        sleep_sec=args.sleep,
    )

    logger.info(f"\n[Done] Download summary:")
    logger.info(f"  Downloaded: {result['downloaded']}")
    logger.info(f"  Skipped:    {result['skipped']}")
    logger.info(f"  No data:    {result['no_data']} (holidays/weekends)")
    logger.error(f"  Failed:     {result['failed']}")

    print_wall_time(t0)


if __name__ == "__main__":
    main()
