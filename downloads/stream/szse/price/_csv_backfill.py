"""SZSE CSV backfill — load aggregated 5-min bar CSVs to DB.

Scans temps/szse_intraday/ for per-cycle CSV files (each contains
already-aggregated 5-min OHLCV bars written by write_cycle_csv), loads
bar rows, splits them into stock vs index rows based on code format,
and upserts to the appropriate DB tables.

Called at startup and every 5 minutes (even outside trading hours) so
CSV data that wasn't loaded during live streaming is recovered
automatically. Idempotent: ON CONFLICT just updates existing rows.

Date-check pattern (fast-path before reading any CSV content):
  1. Glob szse_intraday_*.csv files (filenames only — no reading yet)
  2. Extract dates from filenames (YYYYMMDD prefix)
  3. Query DB for dates already complete (latest bar >= 15:00)
  4. Filter files to only those with incomplete dates
  5. Read ONLY the filtered files
"""
from __future__ import annotations

import csv
import re
import time as _time
from datetime import date, datetime, time as dtime
from pathlib import Path
from typing import Dict, List, Optional, Set

from downloads._common import resolve_out_dir, setup_logger
from _common.db_commons import bulk_upsert

logger = setup_logger("stream_szse")

BACKFILL_INTERVAL_SEC = 5 * 60  # 5 minutes

# Market close time — a date is "complete" when the latest bar time >= this.
CLOSE_TIME = dtime(15, 0)


def _is_index_code(code: str) -> bool:
    """True if code is a bare 6-digit index code (no exchange suffix)."""
    return "." not in code and code.isdigit() and len(code) == 6


def _extract_date_from_filename(filename: Path) -> Optional[date]:
    """Extract trading date from 'szse_intraday_{YYYYMMDD}_{HHMMSS}.csv'."""
    m = re.match(r"szse_intraday_(\d{8})_\d{6}\.csv", filename.name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def _get_incomplete_dates(conn, all_dates: Set[date]) -> Set[date]:
    """Return subset of dates that are NOT yet complete in the DB.

    A date is complete when the latest bar time in either
    stats.stock_intraday_5min or stats.index_intraday_5min
    is >= 15:00 (CLOSE_TIME).

    Uses a single SQL query with MAX(time) per date for efficiency.
    """
    if not all_dates:
        return set()

    date_list = list(all_dates)

    # Check stock intraday table
    cur = conn.execute(
        "SELECT date, MAX(time) AS max_time "
        "FROM stats.stock_intraday_5min "
        "WHERE date = ANY(%s) "
        "GROUP BY date",
        (date_list,),
    )
    rows = cur.fetchall()

    complete_dates: Set[date] = set()
    for row in rows:
        max_time = row[1]
        if max_time is not None and max_time >= CLOSE_TIME:
            complete_dates.add(row[0])

    # Also check index intraday table (some dates may have only index data)
    cur2 = conn.execute(
        "SELECT date, MAX(time) AS max_time "
        "FROM stats.index_intraday_5min "
        "WHERE date = ANY(%s) "
        "GROUP BY date",
        (date_list,),
    )
    rows2 = cur2.fetchall()
    for row in rows2:
        max_time = row[1]
        if max_time is not None and max_time >= CLOSE_TIME:
            complete_dates.add(row[0])

    return all_dates - complete_dates


def backfill_szse_csvs(conn) -> int:
    """Scan all SZSE intraday CSV files and upsert bars to DB.

    Date-check fast-path: filenames are checked first to identify dates
    that are already complete in the DB (latest bar >= 15:00). Only CSV
    files for incomplete dates are read.

    Each CSV row has: update_time, date, code, name, time,
    open, high, low, close, trading_shares, change, change_pct.

    Stock rows (code WITH .SZ suffix) → stats.stock_intraday_5min.
    Index rows (bare 6-digit code) → stats.index_intraday_5min.

    Returns total number of bars upserted.
    """
    out_dir = resolve_out_dir(None, "szse_intraday", None)
    csv_files = sorted(out_dir.glob("szse_intraday_*.csv"))
    if not csv_files:
        return 0

    # --- FAST-PATH: Extract dates from filenames (no CSV reading) ---
    file_dates: Dict[Path, Optional[date]] = {}
    all_dates: Set[date] = set()
    for f in csv_files:
        d = _extract_date_from_filename(f)
        file_dates[f] = d
        if d is not None:
            all_dates.add(d)

    if not all_dates:
        logger.info("backfill szse: no parseable dates in %d CSVs", len(csv_files))
        return 0

    logger.info("backfill szse: %d CSVs → %d unique dates, checking completeness",
                len(csv_files), len(all_dates))

    # --- Filter: only read CSVs for dates NOT yet complete in DB ---
    incomplete_dates = _get_incomplete_dates(conn, all_dates)
    if not incomplete_dates:
        logger.info("backfill szse: all %d dates already complete in DB — skipping all reads",
                    len(all_dates))
        return 0

    n_skipped = len(all_dates) - len(incomplete_dates)
    logger.info("backfill szse: %d dates incomplete (skipping %d complete dates)",
                len(incomplete_dates), n_skipped)

    # Filter files to only those with incomplete dates
    filtered_files: List[Path] = []
    for f in csv_files:
        d = file_dates.get(f)
        if d is not None and d in incomplete_dates:
            filtered_files.append(f)

    if not filtered_files:
        logger.info("backfill szse: no files for incomplete dates")
        return 0

    logger.info("backfill szse: reading %d/%d CSV files", len(filtered_files), len(csv_files))

    t0 = _time.time()
    total_bars = 0

    # Accumulate rows across filtered CSVs, then batch-upsert once per table.
    stock_identity: List[dict] = []
    stock_bars: List[dict] = []
    index_identity: List[dict] = []
    index_bars: List[dict] = []

    for csv_path in filtered_files:
        try:
            with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    code = (row.get("code") or "").strip()
                    if not code:
                        continue
                    date_str = row.get("date", "").strip()[:10]
                    time_str = (row.get("time") or "").strip()[:8]
                    name = row.get("name") or code
                    if not date_str or not time_str:
                        continue
                    try:
                        bar_date = date.fromisoformat(date_str)
                        # Parse HH:MM:SS
                        parts = time_str.split(":")
                        h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
                        bar_time = dtime(h, m, s)
                    except (ValueError, IndexError):
                        continue

                    def _f(v):
                        try:
                            return float(v) if v not in (None, "") else None
                        except (ValueError, TypeError):
                            return None

                    open_ = _f(row.get("open"))
                    high = _f(row.get("high"))
                    low = _f(row.get("low"))
                    close = _f(row.get("close"))
                    trading_shares = _f(row.get("trading_shares"))
                    change = _f(row.get("change"))
                    change_pct = _f(row.get("change_pct"))

                    if close is None:
                        continue

                    if _is_index_code(code):
                        index_identity.append({
                            "date": bar_date, "code": code, "name": name,
                        })
                        index_bars.append({
                            "date": bar_date, "code": code, "time": bar_time,
                            "open": open_, "high": high, "low": low,
                            "close": close, "change": change,
                            "change_pct": change_pct,
                        })
                    else:
                        # Stock: code has .SZ suffix
                        exchange = "SZ" if ".SZ" in code else None
                        stock_identity.append({
                            "date": bar_date, "code": code,
                            "exchange": exchange, "name": name,
                        })
                        stock_bars.append({
                            "date": bar_date, "code": code, "time": bar_time,
                            "exchange": exchange,
                            "open": open_, "high": high, "low": low,
                            "close": close, "trading_shares": trading_shares,
                            "change": change, "change_pct": change_pct,
                        })
        except Exception as e:
            logger.warning("backfill szse: %s failed: %s", csv_path.name, e)

    # Dedup identity rows and batch-upsert
    def _dedup(rows, key_fn):
        seen = set()
        uniq = []
        for r in rows:
            k = key_fn(r)
            if k in seen:
                continue
            seen.add(k)
            uniq.append(r)
        return uniq

    if stock_identity:
        uniq = _dedup(stock_identity, lambda r: (r["date"], r["code"]))
        bulk_upsert(conn, "stats.stock_identity", uniq, ["date", "code"])
    if stock_bars:
        bulk_upsert(conn, "stats.stock_intraday_5min", stock_bars, ["date", "code", "time"])
        total_bars += len(stock_bars)
    if index_identity:
        uniq = _dedup(index_identity, lambda r: (r["date"], r["code"]))
        bulk_upsert(conn, "stats.index_identity", uniq, ["date", "code"])
    if index_bars:
        bulk_upsert(conn, "stats.index_intraday_5min", index_bars, ["date", "code", "time"])
        total_bars += len(index_bars)

    elapsed = _time.time() - t0
    logger.info(
        "backfill szse: %d/%d CSVs → %d stock bars + %d index bars in %.1fs",
        len(filtered_files), len(csv_files), len(stock_bars), len(index_bars), elapsed,
    )
    return total_bars
