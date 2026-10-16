"""Stream BSE (北交所) prices into intraday tables — EOD-update mode (default).

Two assets per EOD cycle, both sourced from www.bse.cn:

  * STOCKS (EOD update) — at 16:00 on trading days, each 北证50 (899050) /
    北证专精特新 (899601) constituent's per-minute time-sharing series is
    fetched from the chart behind
    ``https://www.bse.cn/products/neeq_listed_companies/company_time_sharing.html?companyCode=<code>&typename=G``
    (``companyEchartsController/getTimeSharingChart``, see
    ``fetch_bse_company_timesharing``) and folded into ONE whole-day OHLCV
    bar per stock — open/high/low from the series' running day fields,
    close from the last minute, ``trading_shares`` as the sum of
    per-minute volumes — stamped CLOSE_TIME (15:00) on the shared 5-minute
    grid and upserted into ``stats.stock_intraday_5min`` (FK-less by
    design; identity rows also go to ``stats.stock_identity`` with
    ``is_in_index_or_etf=TRUE``). The constituent union is loaded per EOD
    day from ``stats.sec_composition`` (written by
    ``downloads.index.bse.composition``), bounding the per-company request
    count to ~200 codes.
  * INDICES (北证50 899050, 北证专精特新 899601) — one GET per index per
    cycle (``neeqRealController/getRealBZ50.do`` / ``getRealZJTX.do``, see
    ``BSE_INDEX_ENDPOINTS``) returning the whole session's per-minute
    series; every 5-minute window is re-aggregated and idempotently
    upserted into ``stats.index_intraday_5min`` (no volume column there),
    with identity rows to ``stats.index_identity``. Restarts self-heal
    from any point in the session. One index's failure never blocks the
    stock flow or the other index.

Raw fetches are archived to ``temps/bse_intraday/``:
``bse_stock_intraday_{code}_YYYYMMDD.csv`` (per-code per-minute rows — the
only record of the per-minute volume/amount, since the DB stores just the
daily bar) and ``bse_index_intraday_{code}_YYYYMMDD.csv`` (per-index
per-minute rows). (Stock files dated before 2026-10-12 carry the old
quotation-snapshot schema ``bse_intraday_YYYYMMDD.csv``; index files before
2026-10-10 the old single-index name.)

EOD-update schedule (Asia/Shanghai) — the DEFAULT and only mode: bse.cn
finalizes the day's data at 16:00 (the BSE index archive applies the same
rule), so the poller idles until 16:00 on trading days — matching the
afternoon-start convention of the SZSE/CSIndex/CNIndex streamers — and
fetches inside [16:00, 17:30]. A pass that completes every constituent's
bar finishes the day (stocks + both indices) and it sleeps until the next
trading day's 16:00; the rest of the window is only a retry budget for
failed/partial fetches. Codes that already have a 15:00 bar are skipped
for the rest of the day (re-checked from the DB at each EOD day start, so
a restart does not reprocess).

Usage:
  python -m downloads.stream.bse.price                  # EOD update (60s retry cadence)
  python -m downloads.stream.bse.price --interval 90    # slower retry cadence
  python -m downloads.stream.bse.price --once           # one EOD pass then exit
  python -m downloads.stream.bse.price --index-only     # fetch only the index legs
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
from typing import Dict, Optional

import requests

from downloads._common import (
    AntiBotProxy,
    antibot_config_for,
    resolve_out_dir,
    setup_logger,
    sleep_sec_for,
)
from downloads._common.exchanges.bjs import (
    BSE_INDEX_CODES,
    fetch_bse_company_timesharing,
    fetch_bse_index_realtime,
)
from _common.db_commons import bulk_upsert, get_db_connection
from _common.service_heartbeat import register_service

from ._model import (
    BseStream,
    CLOSE_TIME,
    DEFAULT_POLL_INTERVAL_SEC,
    EOD_GIVE_UP_TIME,
    EOD_UPDATE_TIME,
    SESSION_COMPLETE_TIME,
    STOCK_MINUTE_CSV_COLUMNS,
    aggregate_index_bars,
    daily_bar_from_minutes,
    in_eod_window,
    next_eod_update,
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

# Anti-bot pacing — resolved once at import from the service's
# external_request_config JSON rows; the CLI overrides feed the proxy's base
# sleep so --page-sleep/--eod-sleep keep working.
#   "page" — index legs (one GET per index per cycle).
DEFAULT_PAGE_SLEEP_SEC = sleep_sec_for(SERVICE, "page")
#   "eod"  — per-company time-sharing GETs inside one EOD sweep (~200 codes).
DEFAULT_EOD_SLEEP_SEC = sleep_sec_for(SERVICE, "eod")

CSV_SUBDIR = "bse_intraday"

# Constituent union of the latest 北证50/北证专精特新 composition snapshots.
_TARGET_CODES_SQL = """
WITH latest AS (
    SELECT code, max(snapshot_date) AS snapshot_date
    FROM stats.sec_composition
    WHERE code = ANY(%s)
    GROUP BY code
)
SELECT DISTINCT c.stock_code
FROM stats.sec_composition c
JOIN latest l ON c.code = l.code AND c.snapshot_date = l.snapshot_date
"""

# Latest known name per constituent code (identity rows carry it).
_LATEST_NAMES_SQL = """
SELECT DISTINCT ON (code) code, name
FROM stats.stock_identity
WHERE code = ANY(%s)
ORDER BY code, date DESC
"""


# ---------------------------------------------------------------------------
# IO: CSV archives + DB upsert
# ---------------------------------------------------------------------------
def write_stock_minutes_csv(update_dt: datetime, records: list,
                            code: str) -> Path:
    """Append one code's time-sharing minute rows to its daily CSV under
    temps/bse_intraday/ (bse_stock_intraday_{code}_YYYYMMDD.csv).

    The DB keeps only the daily bar, so this CSV is the only record of the
    per-minute volume/amount (raw units: shares / yuan) — same archival
    role as the per-index minute CSVs.
    """
    out_dir = resolve_out_dir(__file__, CSV_SUBDIR, None)
    ds = update_dt.strftime("%Y%m%d")
    out_file = out_dir / f"bse_stock_intraday_{code}_{ds}.csv"
    rows = []
    for rec in records or []:
        gxsj = str(rec.get("HQGXSJ") or "").strip()
        if len(gxsj) < 4:
            continue
        rows.append({
            "update_time": (f"{update_dt.date().isoformat()} "
                            f"{gxsj[:2]}:{gxsj[2:4]}:{gxsj[4:6].ljust(2, '0')}"),
            "date": str(rec.get("HQJSRQ") or ""),
            "code": code,
            "price": rec.get("HQZJCJ"),
            "day_open": rec.get("HQJRKP"),
            "day_high": rec.get("HQZGCJ"),
            "day_low": rec.get("HQZDCJ"),
            "prev_close": rec.get("HQZRSP"),
            "volume": rec.get("HQCJSL"),
            "amount": rec.get("HQCJJE"),
        })
    if not rows:
        return out_file
    needs_header = (not out_file.exists()) or out_file.stat().st_size == 0
    with open(out_file, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=STOCK_MINUTE_CSV_COLUMNS,
                                extrasaction="ignore", lineterminator="\n")
        if needs_header:
            writer.writeheader()
        writer.writerows(rows)
    return out_file


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


def load_bars(conn, identity_rows: list, bar_rows: list) -> None:
    """Upsert identity rows then intraday bars.

    Identity rows are deduplicated by (date, code) — daily_bar_from_minutes
    emits one per bar, and duplicate keys in a single INSERT ... ON CONFLICT
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


def load_target_stocks(conn) -> Dict[str, str]:
    """bare code -> name for the EOD download scope: the union of the two
    indexes' latest composition snapshots (stats.sec_composition, code
    899050/899601), names from each code's latest stats.stock_identity row.

    Returns {} (and logs) when the composition table has no snapshot for
    the indexes — the EOD stock flow then has no targets this pass.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(_TARGET_CODES_SQL, (list(BSE_INDEX_CODES),))
            codes = sorted(r[0].split(".", 1)[0] for r in cur.fetchall())
        if not codes:
            logger.error("No sec_composition snapshot for %s — EOD stock "
                         "update has no targets.", sorted(BSE_INDEX_CODES))
            return {}
        with conn.cursor() as cur:
            cur.execute(_LATEST_NAMES_SQL, ([f"{c}.BJ" for c in codes],))
            names = {r[0].split(".", 1)[0]: r[1] or "" for r in cur.fetchall()}
        return {c: names.get(c, "") for c in codes}
    except Exception as e:  # noqa: BLE001
        logger.error("load_target_stocks failed: %s: %s", type(e).__name__, e)
        return {}


def prepopulate_finished_codes(conn, trade_date: date, finished_codes: set) -> None:
    """Add BSE stocks that already have a 15:00 bar for trade_date to
    finished_codes (prevents reprocessing after a restart)."""
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


# ---------------------------------------------------------------------------
# EOD stock sweep — per-company time-sharing -> daily bar
# ---------------------------------------------------------------------------
def _eod_stock_sweep(conn, session, proxy, bse: BseStream,
                     trade_date: date) -> bool:
    """One EOD pass over the constituents still missing today's final bar.

    Per code: fetch the time-sharing minute series, archive it to CSV, fold
    it into the daily bar (a series whose last minute is below
    SESSION_COMPLETE_TIME is partial — post-16:00 lag — and stays pending),
    and upsert immediately so a mid-sweep crash loses nothing (idempotent
    ON CONFLICT). Returns True when every target has its bar.
    """
    if not bse.target_names:
        # Empty targets are usually a transient DB read failure — retry the
        # load each pass; the day cannot complete without it.
        bse.target_names.update(load_target_stocks(conn))

    pending = [c for c in sorted(bse.target_names) if c not in bse.finished_codes]
    n_emitted = 0
    for code in pending:
        try:
            records = fetch_bse_company_timesharing(session, code, proxy=proxy,
                                                    service=SERVICE)
            if records is None:
                logger.warning("[%s] time-sharing fetch failed; retrying next "
                               "pass.", code)
                continue
            csv_path = write_stock_minutes_csv(datetime.now(), records, code)
            identity_row, bar_row, latest = daily_bar_from_minutes(
                records, trade_date, code, bse.target_names.get(code, ""),
            )
            if bar_row is None:
                if latest is None:
                    logger.info("[%s] no time-sharing rows today (suspended?); "
                                "leaving pending.", code)
                else:
                    logger.info("[%s] series partial (latest minute %s < %s); "
                                "retrying next pass.", code, latest,
                                SESSION_COMPLETE_TIME)
                continue
            conn = _ensure_conn(conn)
            load_bars(conn, [identity_row], [bar_row])
        except Exception as e:  # noqa: BLE001 — one code's failure must not
            # kill the sweep; the code stays pending and the next pass
            # retries it.
            logger.error("[%s] EOD bar load failed: %s: %s; retrying next "
                         "pass.", code, type(e).__name__, e)
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001 — dead connection
                conn = _ensure_conn(None)
            continue
        bse.finished_codes.add(code)
        n_emitted += 1

    remaining = [c for c in bse.target_names if c not in bse.finished_codes]
    logger.info("EOD stock sweep %s: %d/%d final bars (%d this pass, "
                "%d pending) -> %s", trade_date, len(bse.finished_codes),
                len(bse.target_names), n_emitted, len(remaining),
                f"{CSV_SUBDIR}/bse_stock_intraday_*_{trade_date:%Y%m%d}.csv")
    return not remaining and bool(bse.target_names)


# ---------------------------------------------------------------------------
# Index legs — whole-session re-aggregation (unchanged semantics)
# ---------------------------------------------------------------------------
def _emit_one_index_bars(conn, proxy, bse: BseStream,
                         index_code: str, index_name: str):
    """Poll one index's realtime endpoint and upsert its 5-min bars (idempotent).

    Isolated per index: an index-leg failure must never kill the stock
    EOD flow or the other index's bars. Once a poll's re-aggregated series
    reaches SESSION_COMPLETE_TIME the index is done for the day and its
    GET is skipped on later passes. Returns the (possibly reconnected)
    connection.
    """
    if index_code in bse.indices_done:
        return conn
    try:
        session = requests.Session()
        records = fetch_bse_index_realtime(session, index_code, proxy=proxy,
                                           service=SERVICE)
        session.close()
        if not records:
            logger.warning("[%s] index sweep returned no data; skipping pass.",
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
                        " -> %s", len(bar_rows), index_code, trade_date, latest,
                        csv_path.name)
            if latest is not None and latest >= SESSION_COMPLETE_TIME:
                bse.indices_done.add(index_code)
    except Exception as e:  # noqa: BLE001
        logger.error("[%s] index bar emission failed: %s: %s",
                     index_code, type(e).__name__, e)
    return conn


def _emit_index_bars(conn, proxy, bse: BseStream):
    """Poll every BSE index's realtime endpoint and upsert 5-min bars.

    One GET per index per cycle, re-aggregating every 5-min window
    idempotently from the minute series. Returns the (possibly reconnected)
    connection.
    """
    for index_code, index_name in BSE_INDEX_CODES.items():
        conn = _emit_one_index_bars(conn, proxy, bse, index_code, index_name)
    return conn


# ---------------------------------------------------------------------------
# Main EOD-update loop
# ---------------------------------------------------------------------------
def stream(
    poll_interval: Optional[float] = None,
    once: bool = False,
    page_sleep: Optional[float] = None,
    eod_sleep: Optional[float] = None,
    index_only: bool = False,
) -> None:
    # Long-lived daemon: resolve pacing from the service's
    # external_request_config JSON rows ONCE at startup (None = CLI left the
    # default, which is the JSON row). The sleep overrides feed the proxies'
    # base sleeps so --page-sleep/--eod-sleep keep working.
    if poll_interval is None:
        poll_interval = DEFAULT_POLL_INTERVAL_SEC
    if page_sleep is None:
        page_sleep = DEFAULT_PAGE_SLEEP_SEC
    if eod_sleep is None:
        eod_sleep = DEFAULT_EOD_SLEEP_SEC

    session = requests.Session()
    # Index-leg pacing: the JSON "page" row (rotation off, timeouts,
    # toggles; the BSE endpoints need the fixed headers the row's
    # rotation-off preserves). Same BSE invariant the exchanges/bjs.py
    # adapters enforce.
    page_proxy = AntiBotProxy(replace(
        antibot_config_for(SERVICE, "page"),
        base_sleep_sec=page_sleep,
    ))
    # EOD per-company pacing: the JSON "eod" row (~2s across the ~200
    # constituent time-sharing GETs of one sweep).
    eod_proxy = AntiBotProxy(replace(
        antibot_config_for(SERVICE, "eod"),
        base_sleep_sec=eod_sleep,
    ))

    conn = get_db_connection()
    logger.info("DB ready (stats.stock_identity / stats.stock_intraday_5min expected to pre-exist).")

    bse = BseStream()
    current_trade_date: Optional[date] = None
    gave_up_warned = False

    logger.info(
        "stream_bse_price started (EOD update %02d:%02d-%02d:%02d, "
        "poll=%.0fs page_sleep=%.1fs eod_sleep=%.1fs once=%s index_only=%s)",
        EOD_UPDATE_TIME.hour, EOD_UPDATE_TIME.minute,
        EOD_GIVE_UP_TIME.hour, EOD_GIVE_UP_TIME.minute,
        poll_interval, page_sleep, eod_sleep, once, index_only,
    )

    def _start_eod_day(trade_date: date) -> None:
        """Reset per-day completion state, load the EOD download scope
        (constituent union) and re-check finished codes from the DB (a
        restart must not reprocess done codes)."""
        nonlocal gave_up_warned
        gave_up_warned = False
        bse.finished_codes.clear()
        bse.indices_done.clear()
        bse.target_names.clear()
        bse.target_names.update(load_target_stocks(conn))
        prepopulate_finished_codes(conn, trade_date, bse.finished_codes)
        logger.info("EOD day %s: %d target codes, %d already finished (DB).",
                    trade_date, len(bse.target_names), len(bse.finished_codes))

    try:
        while True:
            now = datetime.now()

            # Outside the EOD window: warn once if the window ended with
            # pending codes, then sleep until the next 16:00.
            if not in_eod_window(now):
                if (not gave_up_warned and current_trade_date == now.date()
                        and now.time() > EOD_GIVE_UP_TIME and bse.target_names):
                    pending = [c for c in sorted(bse.target_names)
                               if c not in bse.finished_codes]
                    if pending:
                        logger.warning(
                            "EOD window over for %s with %d pending codes: %s",
                            current_trade_date, len(pending),
                            ", ".join(pending[:20]),
                        )
                    gave_up_warned = True
                if once:
                    logger.info("--once set and outside the EOD window; exiting.")
                    break
                nxt = next_eod_update(now)
                logger.info("Outside the EOD window; sleeping until %s.",
                            nxt.strftime("%Y-%m-%d %H:%M"))
                sleep_until(nxt)
                continue

            # New EOD day (also the first windowed pass after startup).
            if current_trade_date != now.date():
                current_trade_date = now.date()
                _start_eod_day(current_trade_date)

            cycle_start = _time.time()

            # Index legs (北证50 + 北证专精特新): one GET per index per
            # pass, re-aggregating every 5-min window idempotently from the
            # minute series; done indices are skipped.
            conn = _emit_index_bars(conn, page_proxy, bse)
            indices_done = len(bse.indices_done) == len(BSE_INDEX_CODES)

            stocks_done = False
            if not index_only:
                stocks_done = _eod_stock_sweep(conn, session, eod_proxy, bse,
                                               current_trade_date)

            if once:
                logger.info("--once set; exiting after one EOD pass.")
                break
            if stocks_done and (index_only or indices_done):
                logger.info(
                    "EOD day %s complete (%d stock bars, %d/%d indices); "
                    "sleeping until the next trading day.",
                    current_trade_date, len(bse.finished_codes),
                    len(bse.indices_done), len(BSE_INDEX_CODES),
                )
                sleep_until(next_eod_update(now))
                continue

            # Sleep for the remainder of the poll interval before the next
            # retry pass (the sweep itself already slept eod_sleep per code).
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
        description="Stream BSE (北交所) prices — EOD update at 16:00: "
                    "daily OHLCV bars for 北证50/北证专精特新 constituents "
                    "from the per-company time-sharing series, plus the two "
                    "index legs' 5-min bars.")
    ap.add_argument("--interval", type=float, default=None,
                    help="EOD retry cadence in seconds "
                         f"(default: external_request_config JSON for {SERVICE}).")
    ap.add_argument("--page-sleep", type=float, default=None,
                    help="Anti-bot sleep for the index-leg GETs in seconds "
                         f"(default: external_request_config JSON for {SERVICE}).")
    ap.add_argument("--eod-sleep", type=float, default=None,
                    help="Anti-bot sleep between the per-company time-sharing "
                         "GETs of one EOD sweep in seconds "
                         f"(default: external_request_config JSON for {SERVICE}).")
    ap.add_argument("--once", action="store_true",
                    help="Run one EOD pass then exit (dev/test).")
    ap.add_argument("--index-only", action="store_true",
                    help="Fetch only the BSE index legs (北证50 + 北证专精特"
                         "新, 1 GET each/pass) — used to add the indices to a "
                         "pass where the stock flow is already complete.")
    args = ap.parse_args()
    # Liveness heartbeat for database/stream_watchdog.py — long-running
    # mode only (also --index-only); --once exits before a beat would matter.
    if not args.once:
        register_service("downloads.stream.bse.price")
    stream(
        poll_interval=args.interval,
        once=args.once,
        page_sleep=args.page_sleep,
        eod_sleep=args.eod_sleep,
        index_only=args.index_only,
    )


if __name__ == "__main__":
    main()
