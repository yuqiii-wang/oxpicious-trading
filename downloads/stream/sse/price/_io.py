"""SSE stream IO — CSV archive writer and DB upsert / connection helpers.

Shared by the stock flow (``_stock.py``) and the index flow (``_index.py``).
``write_snapshot_csv`` appends each polled snapshot to a daily CSV file under
``temps/<subdir>/``; ``load_bars`` upserts identity rows (FK parent) then
intraday bars for one asset type.
"""
from __future__ import annotations

import csv
from datetime import datetime, time
from pathlib import Path
from typing import List, Optional

from downloads._common import (
    resolve_out_dir,
    setup_logger,
)
from _common.db_commons import (
    bulk_upsert,
    get_db_connection,
)

from ._model import AssetStream, CSV_COLUMNS, CLOSE_TIME

logger = setup_logger("stream_sse")


def write_snapshot_csv(
    update_dt: datetime,
    snapshot: dict,
    csv_subdir: str = "sse_intraday",
    csv_prefix: str = "sse_intraday",
) -> Path:
    """Append one polled snapshot to the daily CSV file under temps/<subdir>/.

    One file per trading day, named <prefix>_YYYYMMDD.csv (using the server
    update date). Every poll appends rows to the same daily file; the header
    is written only when the file is created (or is empty). Volume and amount
    are stored raw (shares / yuan) as returned by the endpoint.
    """
    out_dir = resolve_out_dir(__file__, csv_subdir, None)
    ds = update_dt.strftime("%Y%m%d")
    out_file = out_dir / f"{csv_prefix}_{ds}.csv"
    iso = update_dt.strftime("%Y-%m-%d %H:%M:%S")

    needs_header = (not out_file.exists()) or out_file.stat().st_size == 0
    with open(out_file, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        if needs_header:
            writer.writeheader()
        for code, rec in snapshot.items():
            row = {"update_time": iso, "code": code}
            for col in CSV_COLUMNS:
                if col in ("update_time", "code"):
                    continue
                row[col] = rec.get(col)
            writer.writerow(row)
    return out_file


def load_bars(conn, asset: AssetStream, identity_rows: List[dict], bar_rows: List[dict]) -> None:
    """Upsert identity rows (FK parent) then intraday bars for one asset type.

    Dedup identity rows by (date, code) — aggregate_bars emits one identity
    row per bar, so duplicate keys in a single INSERT ... ON CONFLICT raise
    "cannot affect row a second time".
    """
    if identity_rows:
        seen = set()
        uniq = []
        for r in identity_rows:
            k = (r["date"], r["code"])
            if k in seen:
                continue
            seen.add(k)
            uniq.append(r)
        bulk_upsert(conn, asset.identity_table, uniq, ["date", "code"])
    if bar_rows:
        bulk_upsert(conn, asset.intraday_table, bar_rows, ["date", "code", "time"])


def _ensure_conn(conn):
    """Return a live psycopg connection, reconnecting if closed."""
    if conn is None or getattr(conn, "closed", False):
        logger.info("Reconnecting to database …")
        return get_db_connection()
    return conn


def _prepopulate_finished_codes(
    conn,
    trade_date,
    finished_codes: set,
    table: str = "stats.stock_intraday_5min",
    exchange_filter: Optional[str] = "SS",
) -> None:
    """Query an intraday table to find securities that already have a 15:00
    bar for trade_date. Their bare codes are added to finished_codes.

    This is called at startup to prevent re-processing securities if the
    script restarts after the market has closed.

    ``table`` selects the intraday table (stock vs index). ``exchange_filter``
    restricts to one exchange for stocks ('SS'); pass None for indices
    (index_intraday_5min has no exchange column and codes are already bare).
    """
    query = f"SELECT DISTINCT code FROM {table} WHERE date = %s AND time = %s"
    params: list = [trade_date, CLOSE_TIME]
    if exchange_filter is not None:
        query += " AND exchange = %s"
        params.append(exchange_filter)
    try:
        with conn.cursor() as cur:
            cur.execute(query, tuple(params))
            for r in cur.fetchall():
                full_code = r[0]
                # Strip the exchange suffix to get bare code (no-op for indices).
                bare = full_code.split(".")[0]
                finished_codes.add(bare)
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to pre-populate finished_codes from %s: %s", table, e)


def is_intraday_complete(conn, asset: AssetStream, trade_date, threshold: float = 0.95) -> bool:
    """Check if the intraday table already has sufficient bars up to CLOSE_TIME
    for the given trade_date. Returns True if the ratio of distinct codes
    with a CLOSE_TIME bar to total identity codes >= threshold.

    This catches edge cases where only a few codes have 15:00 bars while
    hundreds of others are missing (suspended stocks, partial DB failures).
    A 95% threshold handles suspended stocks gracefully.
    """
    try:
        # Count identity rows for this date
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(DISTINCT code) FROM {asset.identity_table} WHERE date = %s",
                (trade_date,),
            )
            n_total = cur.fetchone()[0]
        if n_total == 0:
            return False

        # Count codes that have a CLOSE_TIME bar
        code_filter = ""
        params: list = [trade_date, CLOSE_TIME]
        if asset.exchange is not None:
            code_filter = " AND exchange = %s"
            params.append(asset.exchange)
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(DISTINCT code) FROM {asset.intraday_table} "
                f"WHERE date = %s AND time = %s{code_filter}",
                tuple(params),
            )
            n_done = cur.fetchone()[0]

        # Also verify latest bar time is at least CLOSE_TIME
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT MAX(time) FROM {asset.intraday_table} WHERE date = %s",
                (trade_date,),
            )
            max_time = cur.fetchone()[0]
        if max_time is None or max_time < CLOSE_TIME:
            return False

        ratio = n_done / n_total if n_total > 0 else 0.0
        return ratio >= threshold
    except Exception as e:
        logger.warning("is_intraday_complete(%s) failed: %s", asset.name, e)
        return False


def get_intraday_progress(conn, asset: AssetStream, trade_date) -> dict:
    """Get progress stats for an asset's intraday data for a given date.

    Returns dict with:
      - n_identity: number of identity rows
      - n_bars: number of intraday bars
      - max_time: latest bar time (or None)
      - n_codes_done: number of codes with CLOSE_TIME bar
      - n_total_codes: number of distinct codes in identity table
      - complete: whether all codes have CLOSE_TIME bar
    """
    result = {
        "n_identity": 0,
        "n_bars": 0,
        "max_time": None,
        "n_codes_done": 0,
        "n_total_codes": 0,
        "complete": False,
    }
    try:
        # Count identity rows
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {asset.identity_table} WHERE date = %s", (trade_date,))
            result["n_identity"] = cur.fetchone()[0]

        if result["n_identity"] == 0:
            return result

        # Count distinct codes in identity
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(DISTINCT code) FROM {asset.identity_table} WHERE date = %s", (trade_date,))
            result["n_total_codes"] = cur.fetchone()[0]

        # Get max bar time
        with conn.cursor() as cur:
            cur.execute(f"SELECT MAX(time) FROM {asset.intraday_table} WHERE date = %s", (trade_date,))
            max_t = cur.fetchone()[0]
            result["max_time"] = max_t

        # Count codes that have a CLOSE_TIME bar
        code_filter = ""
        params: list = [trade_date, CLOSE_TIME]
        if asset.exchange is not None:
            code_filter = " AND exchange = %s"
            params.append(asset.exchange)
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(DISTINCT code) FROM {asset.intraday_table} "
                f"WHERE date = %s AND time = %s{code_filter}",
                tuple(params),
            )
            result["n_codes_done"] = cur.fetchone()[0]

        result["n_bars"] = result["n_codes_done"]  # at least
        result["complete"] = result["max_time"] is not None and result["max_time"] >= CLOSE_TIME

    except Exception as e:
        logger.warning("get_intraday_progress(%s) failed: %s", asset.name, e)

    return result
