"""SZSE CSV backfill — load aggregated 5-min bar CSVs to DB.

Scans temps/szse_intraday/ for per-cycle CSV files (each contains
already-aggregated 5-min OHLCV bars written by write_cycle_csv), loads
bar rows, splits them into stock vs index rows based on code format,
and upserts to the appropriate DB tables.

Called at startup and every 5 minutes (even outside trading hours) so
CSV data that wasn't loaded during live streaming is recovered
automatically. Idempotent: ON CONFLICT just updates existing rows.
"""
from __future__ import annotations

import csv
import time as _time
from datetime import datetime
from pathlib import Path
from typing import List

from downloads._common.core import resolve_out_dir, setup_logger
from _common.db_commons import bulk_upsert

logger = setup_logger("stream_szse")

BACKFILL_INTERVAL_SEC = 5 * 60  # 5 minutes


def _is_index_code(code: str) -> bool:
    """True if code is a bare 6-digit index code (no exchange suffix)."""
    return "." not in code and code.isdigit() and len(code) == 6


def backfill_szse_csvs(conn) -> int:
    """Scan all SZSE intraday CSV files and upsert bars to DB.

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

    t0 = _time.time()
    total_bars = 0

    # Accumulate rows across all CSVs, then batch-upsert once per table.
    stock_identity: List[dict] = []
    stock_bars: List[dict] = []
    index_identity: List[dict] = []
    index_bars: List[dict] = []

    for csv_path in csv_files:
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
                        from datetime import date as _date, time as _time_mod
                        bar_date = _date.fromisoformat(date_str)
                        # Parse HH:MM:SS
                        parts = time_str.split(":")
                        h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
                        bar_time = _time_mod(h, m, s)
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
                        code_suffix = "SZ" if ".SZ" in code else None
                        stock_identity.append({
                            "date": bar_date, "code": code,
                            "code_suffix": code_suffix, "name": name,
                        })
                        stock_bars.append({
                            "date": bar_date, "code": code, "time": bar_time,
                            "code_suffix": code_suffix,
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
        "backfill szse: %d CSVs → %d stock bars + %d index bars in %.1fs",
        len(csv_files), len(stock_bars), len(index_bars), elapsed,
    )
    return total_bars
