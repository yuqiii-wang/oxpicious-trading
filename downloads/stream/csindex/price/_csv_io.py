"""CSIndex stream CSV IO — archive bar rows to CSV and load CSV back to DB.

``write_bars_csv`` archives one fetch's 5-min bar rows to a CSV under
``temps/csindex_intraday/``. ``backfill_csvs`` scans all archived CSVs
and upserts bars to ``stats.index_intraday_5min``.

CSV archival is important for CSIndex because the streamer makes ~15-30s
antibot-sleep-delayed fetches — if the DB connection drops mid-sweep, the
fetched data is lost without CSV backup. Backfill recovers it.
"""
from __future__ import annotations

import csv
import time as _time
from datetime import datetime
from pathlib import Path
from typing import List

from downloads._common.core import resolve_out_dir, setup_logger
from _common.db_commons import bulk_upsert

from ._constants import CSV_COLUMNS

logger = setup_logger("csindex_stream")


def write_bars_csv(cycle_dt: datetime, bar_rows: List[dict]) -> Path | None:
    """Archive one fetch's bar rows to a CSV under temps/csindex_intraday/.

    File name: ``csindex_intraday_<YYYYMMDD>_<code>.csv`` — one file per
    (date, code) so multiple fetches for the same code on the same day
    APPEND to the same file (idempotent backfill via ON CONFLICT).

    Actually, to avoid append-complexity, we use a per-fetch timestamp:
    ``csindex_intraday_<YYYYMMDD_HHMMSS>_<code>.csv``.
    """
    if not bar_rows:
        return None

    out_dir = resolve_out_dir(None, "csindex_intraday", None)
    code = bar_rows[0].get("code", "unknown")
    ts = cycle_dt.strftime("%Y%m%d_%H%M%S")
    out_file = out_dir / f"csindex_intraday_{ts}_{code}.csv"
    iso = cycle_dt.strftime("%Y-%m-%d %H:%M:%S")

    with open(out_file, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in bar_rows:
            out = {"update_time": iso}
            out.update(row)
            # Convert date/time to ISO strings for CSV portability
            if hasattr(out.get("date"), "isoformat"):
                out["date"] = out["date"].isoformat()
            if hasattr(out.get("time"), "isoformat"):
                out["time"] = out["time"].isoformat()
            writer.writerow(out)
    return out_file


def backfill_csvs(conn) -> int:
    """Scan all CSIndex intraday CSV files and upsert bars to DB.

    Each CSV row has: update_time, date, code, name, time,
    open, high, low, close, change, change_pct.

    All rows go to ``stats.index_intraday_5min`` (+ ``stats.index_identity``
    FK parent). Idempotent: ON CONFLICT just updates existing rows.

    Returns total number of bars upserted.
    """
    out_dir = resolve_out_dir(None, "csindex_intraday", None)
    csv_files = sorted(out_dir.glob("csindex_intraday_*.csv"))
    if not csv_files:
        return 0

    t0 = _time.time()
    total_bars = 0

    # Accumulate rows across all CSVs, then batch-upsert once.
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
                    date_str = (row.get("date") or "").strip()[:10]
                    time_str = (row.get("time") or "").strip()[:8]
                    name = row.get("name") or code
                    if not date_str or not time_str:
                        continue
                    try:
                        from datetime import date as _date, time as _time_mod
                        bar_date = _date.fromisoformat(date_str)
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
                    change = _f(row.get("change"))
                    change_pct = _f(row.get("change_pct"))

                    if close is None:
                        continue

                    index_identity.append({
                        "date": bar_date, "code": code, "name": name,
                    })
                    index_bars.append({
                        "date": bar_date, "code": code, "time": bar_time,
                        "open": open_, "high": high, "low": low,
                        "close": close, "change": change,
                        "change_pct": change_pct,
                    })
        except Exception as e:
            logger.warning("backfill csindex: %s failed: %s", csv_path.name, e)

    # Dedup identity rows and batch-upsert
    if index_identity:
        seen = set()
        uniq = []
        for r in index_identity:
            k = (r["date"], r["code"])
            if k in seen:
                continue
            seen.add(k)
            uniq.append(r)
        bulk_upsert(conn, "stats.index_identity", uniq, ["date", "code"])
    if index_bars:
        bulk_upsert(conn, "stats.index_intraday_5min", index_bars, ["date", "code", "time"])
        total_bars = len(index_bars)

    elapsed = _time.time() - t0
    logger.info(
        "backfill csindex: %d CSVs → %d index bars in %.1fs",
        len(csv_files), len(index_bars), elapsed,
    )
    return total_bars
