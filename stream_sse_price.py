"""stream_sse_price.py — Stream SSE equity prices and build 5-minute OHLCV bars.

Polls the JSONP endpoint that powers the "刷新" (refresh) button on
https://www.sse.com.cn/market/price/report/ every 60 seconds during trading
hours, collecting the latest price (``last``) and cumulative day volume
(``volume``) for every Shanghai-listed stock.

Every 5 one-minute samples are aggregated into one 5-minute OHLCV bar per stock
and upserted into ``stats.stock_intraday_5min``:
  * open / high / low / close  ← the 5 ``last`` prices ("collect latest per min")
  * volume                     ← last.cumvol - prev_bar.cumvol  (subtraction,
                                  because the endpoint returns today's
                                  cumulative volume, not per-bar volume)
  * change / change_pct        ← close - open, (close - open) / open * 100

Trading hours (Asia/Shanghai): 09:30-11:30, 13:00-15:00 on trading days only.
Outside trading hours the loop sleeps until the next session.

Skip logic: Once a stock's bar reaches 15:00 (CLOSE_TIME), it is marked as
finished and skipped in subsequent cycles for that trade_date. This prevents
re-processing stocks after the market closes. At startup, the script queries
stats.stock_intraday_5min to pre-populate finished_codes with stocks that
already have a 15:00 bar for today — preventing re-processing if the script
restarts after close.

Requires tables from database/sql/06_stock_baseline.sql (stock_identity +
stock_intraday_5min). Run that SQL first.

Usage:
  python stream_sse_price.py                  # stream all day (60s poll)
  python stream_sse_price.py --interval 10    # dev: 10s poll interval
  python stream_sse_price.py --once           # emit one 5-sample bar then exit
  python stream_sse_price.py --bar-window 3   # dev: 3-sample bars
"""
from __future__ import annotations

import argparse
import csv
import locale as _locale
import sys
import time as _time
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from _download_commons import (
    HostStatusTracker,
    add_exchange_suffix,
    build_default_session,
    is_trading_day,
    resolve_out_dir,
    setup_logger,
)
from _db_commons import (
    bulk_upsert,
    get_db_connection,
)
# Reuse the SSE JSONP fetch/parse helpers already proven in download_sse_price.
from download_sse_price import (
    PAGE_SIZE,
    SSE_HEADERS,
    SSE_LIST_URL,
    _extract_update_datetime,
    _fetch_page,
    _num,
)

# ---------------------------------------------------------------------------
# stdout encoding (Windows)
# ---------------------------------------------------------------------------
try:
    _locale.setlocale(_locale.LC_ALL, "")
except Exception:
    pass
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


logger = setup_logger("stream_sse")

# A snapshot sample: {bare_code: full_record} where full_record holds every
# field returned by the SSE list endpoint (name, open, high, low, last,
# prev_close, change, volume, amount). Carrying the full row lets us both
# aggregate OHLCV (last + volume) and archive the raw queried data to CSV.
Snapshot = Dict[str, dict]

TRADING_SESSIONS: List[Tuple[time, time]] = [
    (time(9, 30), time(11, 30)),
    (time(13, 0), time(15, 0)),
]

CLOSE_TIME = time(15, 0)

DEFAULT_POLL_INTERVAL_SEC = 60
DEFAULT_BAR_WINDOW = 5
INTER_PAGE_SLEEP_SEC = 0.3

# Local CSV archive: one file per poll, written under temps/sse_intraday/.
CSV_COLUMNS = [
    "update_time", "code", "name", "open", "high", "low", "last",
    "prev_close", "change", "volume", "amount",
]


# ---------------------------------------------------------------------------
# Trading-hours helpers
# ---------------------------------------------------------------------------
def in_trading_hours(dt: datetime) -> bool:
    t = dt.time()
    for start, end in TRADING_SESSIONS:
        if start <= t <= end:
            return True
    return False


def next_trading_moment(now: datetime) -> datetime:
    """Return the next datetime at which trading is active (today or next trading day)."""
    d = now.date()
    t = now.time()
    if is_trading_day(d):
        if t < time(9, 30):
            return datetime.combine(d, time(9, 30))
        if time(9, 30) <= t <= time(11, 30):
            return now
        if time(11, 30) < t < time(13, 0):
            return datetime.combine(d, time(13, 0))
        if time(13, 0) <= t <= time(15, 0):
            return now
        # after 15:00 -> next trading day
    nd = d + timedelta(days=1)
    while not is_trading_day(nd):
        nd += timedelta(days=1)
    return datetime.combine(nd, time(9, 30))


def sleep_until(target_dt: datetime, chunk_sec: float = 60.0) -> None:
    """Sleep until target_dt, in chunks so KeyboardInterrupt stays responsive."""
    while True:
        now = datetime.now()
        if now >= target_dt:
            return
        remaining = (target_dt - now).total_seconds()
        _time.sleep(min(chunk_sec, max(0.0, remaining)))


# ---------------------------------------------------------------------------
# Snapshot fetching (paginates the SSE list endpoint exactly like the webpage)
# ---------------------------------------------------------------------------
def _parse_snapshot_row(row: list) -> Optional[Dict[str, object]]:
    """Map one SSE list row to a full record dict.

    Row order matches SELECT_FIELDS in download_sse_price:
    code, name, open, high, low, last, prev_close, change, volume, amount
    """
    if not row:
        return None
    code = str(row[0]).strip() if row[0] is not None else ""
    if not code:
        return None
    return {
        "code": code,
        "name": str(row[1]).strip() if len(row) > 1 and row[1] is not None else "",
        "open": _num(row[2]) if len(row) > 2 else None,
        "high": _num(row[3]) if len(row) > 3 else None,
        "low": _num(row[4]) if len(row) > 4 else None,
        "last": _num(row[5]) if len(row) > 5 else None,
        "prev_close": _num(row[6]) if len(row) > 6 else None,
        "change": _num(row[7]) if len(row) > 7 else None,
        "volume": _num(row[8]) if len(row) > 8 else None,
        "amount": _num(row[9]) if len(row) > 9 else None,
    }


def fetch_snapshot(
    session: requests.Session,
    page_size: int = PAGE_SIZE,
    host_tracker: Optional[HostStatusTracker] = None,
) -> Tuple[Optional[datetime], Snapshot]:
    """Fetch all SSE equities in one snapshot.

    Returns (update_dt, {bare_code: (name, last, cumvol)}) or (None, {}).
    The ``update_dt`` comes from the API's date+time fields (the "更新时间" on
    the webpage), not from local clock — consistent with download_sse_price.
    """
    first = _fetch_page(session, 0, page_size, host_tracker=host_tracker)
    if first is None:
        return None, {}
    update_dt = _extract_update_datetime(first)
    if update_dt is None:
        return None, {}

    snapshot: Snapshot = {}
    for row in first.get("list", []) or []:
        rec = _parse_snapshot_row(row)
        if rec:
            snapshot[rec["code"]] = rec

    total = int(first.get("total", 0))
    written = len(first.get("list", []) or [])
    page_index = 1
    while written < total:
        if host_tracker and host_tracker.is_blocked(SSE_LIST_URL):
            logger.warning("SSE host blocked mid-pagination, using partial snapshot (%d stocks)", len(snapshot))
            break
        begin = page_index * page_size
        end = begin + page_size
        _time.sleep(INTER_PAGE_SLEEP_SEC)
        payload = _fetch_page(session, begin, end, host_tracker=host_tracker)
        if payload is None:
            logger.warning("page %d (begin=%d) failed; using partial snapshot", page_index + 1, begin)
            break
        page_rows = payload.get("list", []) or []
        if not page_rows:
            break
        for row in page_rows:
            rec = _parse_snapshot_row(row)
            if rec:
                snapshot[rec["code"]] = rec
        written += len(page_rows)
        page_index += 1

    return update_dt, snapshot


def write_snapshot_csv(update_dt: datetime, snapshot: Snapshot) -> Path:
    """Archive one polled snapshot to a CSV file under temps/sse_intraday/.

    One file per poll, named sse_intraday_YYYYMMDD_HHMMSS.csv (using the
    server update time), so every queried snapshot is persisted verbatim.
    Volume/amount are stored raw (shares / yuan) as returned by the endpoint.
    """
    out_dir = resolve_out_dir(__file__, "sse_intraday", None)
    ts = update_dt.strftime("%Y%m%d_%H%M%S")
    out_file = out_dir / f"sse_intraday_{ts}.csv"
    iso = update_dt.strftime("%Y-%m-%d %H:%M:%S")

    with open(out_file, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for code, rec in snapshot.items():
            row = {"update_time": iso, "code": code}
            for col in CSV_COLUMNS:
                if col in ("update_time", "code"):
                    continue
                row[col] = rec.get(col)
            writer.writerow(row)
    return out_file


# ---------------------------------------------------------------------------
# 5-minute OHLCV aggregation
# ---------------------------------------------------------------------------
def aggregate_bars(
    buffer: List[Tuple[datetime, Snapshot]],
    prev_bar_cumvol: Dict[str, float],
    finished_codes: set,
    trade_date,
) -> Tuple[List[dict], List[dict], Optional[time]]:
    """Aggregate 5 one-minute samples into per-stock OHLCV bars.

    Args:
        buffer: list of (update_dt, snapshot), one per minute.
        prev_bar_cumvol: mutable dict bare_code -> cumulative volume at the end
            of the previous bar. Updated in place. Reset to {} at the start of
            each trading day by the caller.
        finished_codes: mutable set of bare codes that have already reached
            CLOSE_TIME for this trade_date. Stocks in this set are skipped.
            Updated in place when bar_time reaches CLOSE_TIME.
        trade_date: datetime.date for the bars.

    Returns (identity_rows, bar_rows, bar_time).
      * identity_rows feed stats.stock_identity (satisfies the FK).
      * bar_rows feed stats.stock_intraday_5min.
    """
    if not buffer:
        return [], [], None

    last_dt = buffer[-1][0]
    # Bar end time = last sample's clock time, truncated to the minute.
    bar_time = last_dt.time().replace(second=0, microsecond=0)

    # Collect every code seen across the window.
    all_codes: set = set()
    for _, snap in buffer:
        all_codes.update(snap.keys())

    identity_rows: List[dict] = []
    bar_rows: List[dict] = []
    n_skipped = 0
    for code in sorted(all_codes):
        # Skip stocks that have already reached CLOSE_TIME for this trade_date.
        if code in finished_codes:
            n_skipped += 1
            continue

        lasts: List[float] = []
        cumvols: List[float] = []
        name = ""
        for _, snap in buffer:
            entry = snap.get(code)
            if entry is None:
                continue
            last = entry.get("last")
            cumvol = entry.get("volume")
            if last is not None:
                lasts.append(last)
            if cumvol is not None:
                cumvols.append(cumvol)
            nm = entry.get("name") or ""
            if nm:
                name = nm
        if not lasts:
            # Suspended / no-trade stock in this window: still track cumvol.
            if cumvols:
                prev_bar_cumvol[code] = cumvols[-1]
            continue

        o = lasts[0]
        h = max(lasts)
        low = min(lasts)
        c = lasts[-1]

        end_cumvol = cumvols[-1] if cumvols else 0.0
        prev_cumvol = prev_bar_cumvol.get(code, 0.0)
        vol = end_cumvol - prev_cumvol
        if vol < 0:
            # Cumulative volume should never decrease; if it does (e.g. a new
            # trading day rolled over without a reset), trust the new value.
            vol = end_cumvol
        prev_bar_cumvol[code] = end_cumvol

        change = round(c - o, 4)
        change_pct = round((c - o) / o * 100, 4) if o else None

        full_code = add_exchange_suffix(code, "上海")
        # Exchange suffix: only SZ, SS, or BJ are valid.
        parts = full_code.rsplit(".", 1)
        code_suffix = parts[-1] if len(parts) == 2 and parts[-1] in ("SZ", "SS", "BJ") else None
        identity_rows.append({"date": trade_date, "code": full_code, "code_suffix": code_suffix, "name": name})
        bar_rows.append({
            "date": trade_date,
            "code": full_code,
            "code_suffix": code_suffix,
            "time": bar_time,
            "open": o,
            "high": h,
            "low": low,
            "close": c,
            "volume": vol,
            "change": change,
            "change_pct": change_pct,
        })

        # Mark this stock as finished if the bar reaches CLOSE_TIME.
        if bar_time >= CLOSE_TIME:
            finished_codes.add(code)

    if n_skipped > 0:
        logger.debug("aggregate_bars: skipped %d already-finished stocks (bar_time=%s)", n_skipped, bar_time)

    return identity_rows, bar_rows, bar_time


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def _ensure_conn(conn):
    """Return a live psycopg connection, reconnecting if closed."""
    if conn is None or getattr(conn, "closed", False):
        logger.info("Reconnecting to database …")
        return get_db_connection()
    return conn


def _prepopulate_finished_codes(conn, trade_date, finished_codes: set) -> None:
    """Query stats.stock_intraday_5min to find stocks that already have a
    15:00 bar for trade_date. Their bare codes are added to finished_codes.

    This is called at startup to prevent re-processing stocks if the script
    restarts after the market has closed.
    """
    query = """
        SELECT DISTINCT ON (code) code
          FROM stats.stock_intraday_5min
         WHERE date = %s
           AND time = %s
           AND code_suffix = 'SS'
    """
    try:
        with conn.cursor() as cur:
            cur.execute(query, (trade_date, CLOSE_TIME))
            rows = cur.fetchall()
            for r in rows:
                full_code = r[0]
                # Strip the exchange suffix to get bare code
                bare = full_code.split(".")[0]
                finished_codes.add(bare)
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to pre-populate finished_codes from DB: %s", e)


def load_bars(conn, identity_rows: List[dict], bar_rows: List[dict]) -> None:
    """Upsert identity rows (FK parent) then intraday bars.

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
        bulk_upsert(conn, "stats.stock_identity", uniq, ["date", "code"])
    if bar_rows:
        bulk_upsert(conn, "stats.stock_intraday_5min", bar_rows, ["date", "code", "time"])


# ---------------------------------------------------------------------------
# Main streaming loop
# ---------------------------------------------------------------------------
def stream(
    poll_interval: float = DEFAULT_POLL_INTERVAL_SEC,
    bar_window: int = DEFAULT_BAR_WINDOW,
    once: bool = False,
) -> None:
    session = build_default_session(SSE_HEADERS)
    host_tracker = HostStatusTracker()

    conn = get_db_connection()
    logger.info("DB ready (stats.stock_intraday_5min expected to pre-exist).")

    buffer: List[Tuple[datetime, Snapshot]] = []
    prev_bar_cumvol: Dict[str, float] = {}
    finished_codes: set = set()
    current_trade_date = None

    # Pre-populate finished_codes from DB: stocks that already have a 15:00 bar
    # for today. This prevents re-processing if the script restarts after close.
    today = datetime.now().date()
    if is_trading_day(today):
        t0 = _time.time()
        _prepopulate_finished_codes(conn, today, finished_codes)
        logger.info(
            "Pre-populated %d finished codes from DB for %s in %.1fs",
            len(finished_codes), today, _time.time() - t0,
        )
        # Set current_trade_date to avoid clearing finished_codes on first iteration.
        current_trade_date = today

    logger.info(
        "stream_sse_price started (poll=%.0fs bar_window=%d once=%s)",
        poll_interval, bar_window, once,
    )

    try:
        while True:
            now = datetime.now()

            # Outside trading hours: flush any partial buffer, then wait.
            if not (is_trading_day(now.date()) and in_trading_hours(now)):
                if buffer:
                    logger.info("Session ended; flushing %d partial samples.", len(buffer))
                    update_dt = buffer[-1][0]
                    trade_date = update_dt.date()
                    identity_rows, bar_rows, bar_time = aggregate_bars(
                        buffer, prev_bar_cumvol, finished_codes, trade_date,
                    )
                    if bar_rows:
                        conn = _ensure_conn(conn)
                        load_bars(conn, identity_rows, bar_rows)
                        logger.info(
                            "flushed %d bars for %s %s",
                            len(bar_rows), trade_date, bar_time,
                        )
                    buffer.clear()
                if once:
                    logger.info("--once set and outside trading hours; exiting.")
                    break
                nxt = next_trading_moment(now)
                wait = (nxt - now).total_seconds()
                logger.info("Outside trading hours; waiting %.0fs until %s", wait, nxt)
                sleep_until(nxt)
                continue

            # New trading day: reset cumulative-volume baseline and finished_codes.
            if current_trade_date != now.date():
                current_trade_date = now.date()
                prev_bar_cumvol.clear()
                finished_codes.clear()
                buffer.clear()
                logger.info("New trading day %s; cumulative-volume baseline and finished_codes reset.", current_trade_date)

            cycle_start = _time.time()
            update_dt, snapshot = fetch_snapshot(session, host_tracker=host_tracker)

            if update_dt is None or not snapshot:
                logger.warning("Poll returned no data; skipping cycle.")
            else:
                buffer.append((update_dt, snapshot))
                csv_path = write_snapshot_csv(update_dt, snapshot)
                logger.info(
                    "sample %d/%d @ %s: %d stocks -> %s",
                    len(buffer), bar_window,
                    update_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    len(snapshot),
                    csv_path.name,
                )
                if len(buffer) >= bar_window:
                    trade_date = update_dt.date()
                    identity_rows, bar_rows, bar_time = aggregate_bars(
                        buffer, prev_bar_cumvol, finished_codes, trade_date,
                    )
                    if bar_rows:
                        conn = _ensure_conn(conn)
                        load_bars(conn, identity_rows, bar_rows)
                        logger.info(
                            "emitted %d bars for %s %s (vol baseline=%d codes)",
                            len(bar_rows), trade_date, bar_time, len(prev_bar_cumvol),
                        )
                    buffer.clear()
                    if once:
                        logger.info("--once set; exiting after first bar.")
                        break

            # Sleep for the remainder of the poll interval to keep cadence.
            elapsed = _time.time() - cycle_start
            sleep_sec = poll_interval - elapsed
            if sleep_sec > 0:
                _time.sleep(sleep_sec)
    except KeyboardInterrupt:
        logger.info("Interrupted by user; exiting.")
    finally:
        try:
            conn.close()
        except Exception:
            pass
        session.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Stream SSE equity prices into 5-min OHLCV bars.")
    ap.add_argument("--interval", type=float, default=DEFAULT_POLL_INTERVAL_SEC,
                    help=f"Poll interval in seconds (default {DEFAULT_POLL_INTERVAL_SEC}).")
    ap.add_argument("--bar-window", type=int, default=DEFAULT_BAR_WINDOW,
                    help=f"Samples per 5-min bar (default {DEFAULT_BAR_WINDOW}).")
    ap.add_argument("--once", action="store_true",
                    help="Emit one bar then exit (dev/test).")
    args = ap.parse_args()
    stream(poll_interval=args.interval, bar_window=args.bar_window, once=args.once)


if __name__ == "__main__":
    main()
