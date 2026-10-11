"""Stream BSE (北交所) stock prices into 5-minute OHLCV bars.

Two assets per cycle, both sourced from www.bse.cn:

  * STOCKS — polls the JSONP quotation table behind
    https://www.bse.cn/nq/quotation.html (``nqhqController/nqhq_en.do`` with
    the page's own BSE-tab filters — ``type_en='["B"]'`` + ``xxfcbj_en='[2]'``,
    see ``downloads/_common/exchanges/bjs.py``). One sweep = all ~348
    BSE-listed stocks across ~18 server pages (20 rows/page cap, stable code
    sort); the sweep duration (~30s at the default 1.5s inter-page sleep) is
    paced to fit inside the 60s poll interval. Every ``bar_window`` sweeps
    (default 5 × ~60s) aggregate into one 5-minute bar per stock —
    open/high/low/close from the sampled ``last`` prices, ``trading_shares``
    from the delta of the endpoint's cumulative volume — upserted into
    ``stats.stock_intraday_5min`` (FK-less by design; identity rows also go
    to ``stats.stock_identity``).
  * INDICES (北证50 899050, 北证专精特新 899601) — one extra GET per index
    per cycle (``neeqRealController/getRealBZ50.do`` /
    ``getRealZJTX.do``, see ``BSE_INDEX_ENDPOINTS``) returning the whole
    session's per-minute series; every 5-minute window is re-aggregated and
    idempotently upserted into ``stats.index_intraday_5min`` (no volume
    column there), with identity rows to ``stats.index_identity``. Restarts
    self-heal from any point in the session. One index's failure never
    blocks the stock flow or the other index.

Raw sweeps are archived to ``temps/bse_intraday/bse_intraday_YYYYMMDD.csv``
(stocks, SSE-compatible column schema) and
``temps/bse_intraday/bse_index_intraday_{code}_YYYYMMDD.csv`` (per-index
per-minute rows, the only record of the per-minute volume/amount) so missed
bars can be backfilled from CSV later. (Files before 2026-10-10 carry the
old single-index name ``bse_index_intraday_YYYYMMDD.csv`` — 899050 only.)

Trading hours (Asia/Shanghai): 09:30-11:30, 13:00-15:00 on trading days
only. Outside trading hours the loop sleeps until the next session; a
partial buffer is flushed as a final bar when the session ends. Codes that
already have a 15:00 bar are skipped for the rest of the day (re-checked
from the DB at startup, so a post-close restart does not reprocess).

Usage:
  python -m downloads.stream.bse.price                  # stream all day (60s sweeps)
  python -m downloads.stream.bse.price --interval 90    # slower poll cadence
  python -m downloads.stream.bse.price --once           # emit one 5-sample bar then exit
  python -m downloads.stream.bse.price --bar-window 3   # dev: 3-sample bars
"""
from __future__ import annotations


import argparse
import csv
import locale as _locale
import sys
import time as _time
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import requests

from downloads._common import (
    AntiBotProxy,
    antibot_config_for,
    is_trading_day,
    resolve_out_dir,
    setup_logger,
    sleep_sec_for,
)
from downloads._common.exchanges.bjs import (
    BSE_HEADERS,
    BSE_INDEX_CODES,
    fetch_bse_index_realtime,
    fetch_bse_snapshot,
)
from _common.db_commons import bulk_upsert, get_db_connection
from _common.service_heartbeat import register_service

from ._model import (
    CSV_COLUMNS,
    CLOSE_TIME,
    DEFAULT_BAR_WINDOW,
    DEFAULT_POLL_INTERVAL_SEC,
    BseStream,
    aggregate_bars,
    aggregate_index_bars,
    in_trading_hours,
    next_trading_moment,
    sleep_until,
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


logger = setup_logger("stream_bse")

SERVICE = "downloads.stream.bse.price"

# Anti-bot spacing between the ~18 pages of one sweep — resolved once at
# import from the service's external_request_config JSON row (request_type
# "page"); --page-sleep overrides it for one run. The stream sweeps every
# poll interval, so this short spacing keeps one sweep well inside the
# poll row's cycle period.
DEFAULT_PAGE_SLEEP_SEC = sleep_sec_for(SERVICE, "page")

CSV_SUBDIR = "bse_intraday"
CSV_PREFIX = "bse_intraday"


# ---------------------------------------------------------------------------
# IO: CSV archive + DB upsert
# ---------------------------------------------------------------------------
def write_snapshot_csv(update_dt: datetime, snapshot: dict) -> Path:
    """Append one polled sweep to the daily CSV file under temps/bse_intraday/.

    One file per trading day, named bse_intraday_YYYYMMDD.csv (using the
    exchange update date). Volume and amount are stored raw (shares / yuan).
    """
    out_dir = resolve_out_dir(__file__, CSV_SUBDIR, None)
    ds = update_dt.strftime("%Y%m%d")
    out_file = out_dir / f"{CSV_PREFIX}_{ds}.csv"
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


def load_bars(conn, identity_rows: list, bar_rows: list) -> None:
    """Upsert identity rows then intraday bars.

    Identity rows are deduplicated by (date, code) — aggregate_bars emits
    one per bar, and duplicate keys in a single INSERT ... ON CONFLICT
    raise "cannot affect row a second time".
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


def _ensure_conn(conn):
    """Return a live psycopg connection, reconnecting if closed."""
    if conn is None or getattr(conn, "closed", False):
        logger.info("Reconnecting to database …")
        return get_db_connection()
    return conn


def prepopulate_finished_codes(conn, trade_date: date, finished_codes: set) -> None:
    """Add BSE stocks that already have a 15:00 bar for trade_date to
    finished_codes (prevents reprocessing after a post-close restart)."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT code FROM stats.stock_intraday_5min "
                "WHERE date = %s AND time = %s AND exchange = 'BJ'",
                (trade_date, CLOSE_TIME),
            )
            for r in cur.fetchall():
                finished_codes.add(r[0].split(".", 1)[0])
    except Exception as e:  # noqa: BLE001
        logger.warning("finished_codes prepopulate failed for %s: %s", trade_date, e)


INDEX_CSV_COLUMNS = [
    "update_time", "date", "code", "name", "price", "prev_close",
    "change", "change_pct", "volume", "amount",
]


def write_index_minutes_csv(update_dt: datetime, records: list,
                            code: str, name: str) -> Path:
    """Append one polled minute-series to the per-index daily CSV archive.

    One file per trading day per index (bse_index_intraday_{code}_YYYYMMDD.csv
    under temps/bse_intraday/), storing the raw per-minute rows — CJL/CJJE are
    per-minute volume/amount and never reach the DB (index_intraday_5min has
    no volume column), so the CSV is their only record.
    """
    out_dir = resolve_out_dir(__file__, CSV_SUBDIR, None)
    ds = update_dt.strftime("%Y%m%d")
    out_file = out_dir / f"bse_index_intraday_{code}_{ds}.csv"
    rows = []
    for rec in records or []:
        gxsj = str(rec.get("GXSJ") or "").strip()
        if len(gxsj) < 4:
            continue
        price = rec.get("SSZS")
        prev_close = rec.get("ZRSP")
        change = rec.get("ZD")
        rows.append({
            "update_time": (f"{update_dt.date().isoformat()} "
                            f"{gxsj[:2]}:{gxsj[2:4]}:{gxsj[4:6].ljust(2, '0')}"),
            "date": str(rec.get("JSRQ") or update_dt.date().isoformat()),
            "code": code,
            "name": name,
            "price": price,
            "prev_close": prev_close,
            "change": change,
            "change_pct": rec.get("ZDF"),
            "volume": rec.get("CJL"),
            "amount": rec.get("CJJE"),
        })
    if not rows:
        return out_file
    needs_header = (not out_file.exists()) or out_file.stat().st_size == 0
    with open(out_file, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=INDEX_CSV_COLUMNS,
                                extrasaction="ignore", lineterminator="\n")
        if needs_header:
            writer.writeheader()
        writer.writerows(rows)
    return out_file


def _emit_one_index_bars(conn, proxy, index_code: str, index_name: str):
    """Poll one index's realtime endpoint and upsert its 5-min bars (idempotent).

    Isolated per index: an index-leg failure must never kill the stock
    streaming loop or the other index's bars. Returns the (possibly
    reconnected) connection.
    """
    try:
        session = requests.Session()
        records = fetch_bse_index_realtime(session, index_code, proxy=proxy,
                                           service=SERVICE)
        session.close()
        if not records:
            logger.warning("[%s] index sweep returned no data; skipping cycle.",
                           index_code)
            return conn
        trade_date_str = str(records[-1].get("JSRQ") or "").strip()
        trade_date = datetime.strptime(trade_date_str[:10], "%Y-%m-%d").date()
        identity_rows, bar_rows, latest = aggregate_index_bars(
            records, trade_date, code=index_code, name=index_name,
        )
        csv_path = write_index_minutes_csv(datetime.now(), records,
                                           index_code, index_name)
        if bar_rows:
            conn = _ensure_conn(conn)
            bulk_upsert(conn, "stats.index_identity", identity_rows,
                        ["date", "code"])
            bulk_upsert(conn, "stats.index_intraday_5min", bar_rows,
                        ["date", "code", "time"])
            logger.info("emitted %d BSE index bars for %s %s (latest minute=%s)"
                        " -> %s", index_code, trade_date, len(bar_rows), latest,
                        csv_path.name)
    except Exception as e:  # noqa: BLE001
        logger.error("[%s] index bar emission failed: %s: %s",
                     index_code, type(e).__name__, e)
    return conn


def _emit_index_bars(conn, proxy):
    """Poll every BSE index's realtime endpoint and upsert 5-min bars.

    One GET per index per cycle, re-aggregating every 5-min window
    idempotently from the minute series. Returns the (possibly reconnected)
    connection.
    """
    for index_code, index_name in BSE_INDEX_CODES.items():
        conn = _emit_one_index_bars(conn, proxy, index_code, index_name)
    return conn


# ---------------------------------------------------------------------------
# Main streaming loop
# ---------------------------------------------------------------------------
def stream(
    poll_interval: Optional[float] = None,
    bar_window: int = DEFAULT_BAR_WINDOW,
    once: bool = False,
    page_sleep: Optional[float] = None,
    index_only: bool = False,
) -> None:
    # Long-lived daemon: resolve pacing from the service's
    # external_request_config JSON rows ONCE at startup (None = CLI left the
    # default, which is the JSON row). page_sleep feeds the proxy's base
    # sleep so a --page-sleep override keeps working.
    if poll_interval is None:
        poll_interval = DEFAULT_POLL_INTERVAL_SEC
    if page_sleep is None:
        page_sleep = DEFAULT_PAGE_SLEEP_SEC

    session = requests.Session()
    # Pacing comes from the JSON "page" row (rotation off, timeouts, toggles;
    # the BSE endpoints need the fixed headers the row's rotation-off
    # preserves); page_sleep feeds the base so a --page-sleep override keeps
    # working. Same BSE invariant the exchanges/bjs.py adapters enforce.
    proxy = AntiBotProxy(replace(
        antibot_config_for(SERVICE, "page"),
        base_sleep_sec=page_sleep,
    ))

    conn = get_db_connection()
    logger.info("DB ready (stats.stock_identity / stats.stock_intraday_5min expected to pre-exist).")

    bse = BseStream()
    current_trade_date = None

    today = datetime.now().date()
    if is_trading_day(today):
        prepopulate_finished_codes(conn, today, bse.finished_codes)
        logger.info("Pre-populated %d finished codes from DB for %s",
                    len(bse.finished_codes), today)
        current_trade_date = today

    logger.info(
        "stream_bse_price started (poll=%.0fs bar_window=%d page_sleep=%.1fs once=%s)",
        poll_interval, bar_window, page_sleep, once,
    )

    def _flush(trade_date: date, verb: str) -> None:
        """Aggregate + upsert the current buffer as one bar window."""
        nonlocal conn
        try:
            identity_rows, bar_rows, bar_time = aggregate_bars(bse, trade_date)
            if bar_rows:
                conn = _ensure_conn(conn)
                load_bars(conn, identity_rows, bar_rows)
                logger.info("%s %d BSE bars for %s %s (vol baseline=%d codes)",
                            verb, len(bar_rows), trade_date, bar_time,
                            len(bse.prev_bar_cumvol))
        except Exception as e:  # noqa: BLE001
            logger.error("bar emission failed for %s: %s: %s",
                         trade_date, type(e).__name__, e)

    try:
        while True:
            now = datetime.now()

            # Outside trading hours: flush any partial buffer, then sleep
            # until the next trading moment.
            if not (is_trading_day(now.date()) and in_trading_hours(now)):
                if bse.buffer:
                    logger.info("Session ended; flushing %d partial samples.",
                                len(bse.buffer))
                    _flush(bse.buffer[-1][0].date(), "flushed")
                    bse.buffer.clear()
                if once:
                    logger.info("--once set and outside trading hours; exiting.")
                    break
                nxt = next_trading_moment(now)
                logger.info("Outside trading hours; sleeping until %s.",
                            nxt.strftime("%Y-%m-%d %H:%M"))
                sleep_until(nxt)
                continue

            # New trading day: reset per-day streaming state.
            if current_trade_date != now.date():
                current_trade_date = now.date()
                bse.prev_bar_cumvol.clear()
                bse.finished_codes.clear()
                bse.buffer.clear()
                logger.info("New trading day %s; state reset.", current_trade_date)

            cycle_start = _time.time()

            # Index legs (北证50 + 北证专精特新): one extra GET per index
            # per cycle, re-aggregating every 5-min window idempotently from
            # the minute series.
            conn = _emit_index_bars(conn, proxy)

            if index_only:
                if once:
                    logger.info("--once set; exiting after one index cycle.")
                    break
                elapsed = _time.time() - cycle_start
                sleep_sec = poll_interval - elapsed
                if sleep_sec > 0:
                    _time.sleep(sleep_sec)
                continue

            update_dt, snapshot = fetch_bse_snapshot(session, proxy=proxy,
                                                     service=SERVICE)

            if update_dt is None or not snapshot:
                logger.warning("Sweep returned no data; skipping cycle.")
            else:
                bse.buffer.append((update_dt, snapshot))
                csv_path = write_snapshot_csv(update_dt, snapshot)
                logger.info("sample %d/%d @ %s: %d stocks -> %s",
                            len(bse.buffer), bar_window,
                            update_dt.strftime("%Y-%m-%d %H:%M:%S"),
                            len(snapshot), csv_path.name)
                if len(bse.buffer) >= bar_window:
                    _flush(update_dt.date(), "emitted")
                    bse.buffer.clear()
                    if once:
                        logger.info("--once set; exiting after first bar.")
                        break

            # Sleep for the remainder of the poll interval to keep cadence
            # (the sweep itself already took page_sleep per page).
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
    ap = argparse.ArgumentParser(
        description="Stream BSE (北交所) stock prices into 5-min OHLCV bars "
                    "(quotation-table snapshot polling).")
    ap.add_argument("--interval", type=float, default=None,
                    help="Target sweep cycle period in seconds "
                         f"(default: external_request_config JSON for {SERVICE}).")
    ap.add_argument("--bar-window", type=int, default=DEFAULT_BAR_WINDOW,
                    help=f"Samples per 5-min bar (default {DEFAULT_BAR_WINDOW}).")
    ap.add_argument("--page-sleep", type=float, default=None,
                    help="Anti-bot sleep between the sweep's pages in seconds "
                         f"(default: external_request_config JSON for {SERVICE}).")
    ap.add_argument("--once", action="store_true",
                    help="Emit one bar then exit (dev/test).")
    ap.add_argument("--index-only", action="store_true",
                    help="Stream only the BSE index legs (北证50 + 北证专精特"
                         "新, 1 GET each/cycle) — used to add the indices to a "
                         "session where the stock streamer is already running.")
    args = ap.parse_args()
    # Liveness heartbeat for database/stream_watchdog.py — long-running
    # mode only (also --index-only); --once exits before a beat would matter.
    if not args.once:
        register_service("downloads.stream.bse.price")
    stream(
        poll_interval=args.interval,
        bar_window=args.bar_window,
        once=args.once,
        page_sleep=args.page_sleep,
        index_only=args.index_only,
    )


if __name__ == "__main__":
    main()
