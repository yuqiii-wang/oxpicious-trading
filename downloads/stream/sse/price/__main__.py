"""Stream SSE prices and build 5-minute OHLCV bars.

Polls the JSONP endpoint that powers the "刷新" (refresh) button on
https://www.sse.com.cn/market/price/report/ every 60 seconds during trading
hours, collecting the latest price (``last``) and cumulative day volume
(``volume``) for every Shanghai-listed security.

The 报表 page exposes a 类型 selector with three tabs — 股票 / 基金 / 指数 —
which map to three list endpoints sharing the same JSONP schema (only the
path suffix differs): ``exchange/equity``, ``exchange/fund``, ``exchange/index``.
This service streams all three:

  * 股票 (equity)  → stats.stock_intraday_5min
    - code stored WITH exchange suffix (e.g. "600000.SS")
    - per-bar volume derived by subtracting cumulative volumes across the
      5 one-minute samples (the endpoint returns today's cumulative volume)
  * 基金 (fund)    → stats.etf_intraday_5min
    - same row layout as stocks (code WITH .SS suffix, per-bar trading_shares);
      etf_identity has no is_in_index_or_etf column, so that field is omitted
  * 指数 (index)   → stats.index_intraday_5min  (replaces the former CSIndex
    tick-resampling path in build_csindex.py)
    - code stored BARE (e.g. "000001"), matching index_identity's CHECK
      constraint ^(\\d{6}|H\\d{5})$
    - NO volume column (index_intraday_5min has no volume field)
    - filtered to indices that already exist in stats.index_identity, so
      only tracked indices are streamed (SSE publishes ~200 indices; we
      only care about the subset we already have daily history for)
  * 期权 (options) → stats.options_intraday_5min  (see ._options)
    - sweeps ALL (underlying, expiry-month) tstyle endpoints behind
      https://www.sse.com.cn/assortment/options/price/ each cycle (5 ETF
      underlyings × their live months ≈ 20 requests)
    - source volume (张) and amount (元) are DAY-CUMULATIVE → per-bar
      values are SUBTRACTED across samples
    - contract codes are the numeric 合约编码 (via the 当日合约 map); the
      stream writes its own options_identity rows (exchange='SSE')
    - ``--options-eod`` captures the post-close snapshot once as the
      15:00 EOD bars (idempotent; the daily build reuses this path)

Every 5 one-minute samples are aggregated into one 5-minute OHLCV bar per
security and upserted into the corresponding intraday table:
  * open / high / low / close  ← the 5 ``last`` prices ("collect latest per min")
  * volume (stocks/ETFs only) ← last.cumvol - prev_bar.cumvol  (subtraction,
                                  because the endpoint returns today's
                                  cumulative volume, not per-bar volume)
  * change / change_pct        ← close - open, (close - open) / open * 100

Trading hours (Asia/Shanghai): 09:30-11:30, 13:00-15:00 on trading days only.
Outside trading hours the loop sleeps until the next session.

Skip logic: Once a security's bar reaches 15:00 (CLOSE_TIME), it is marked as
finished and skipped in subsequent cycles for that trade_date. This prevents
re-processing after the market closes. At startup, the script queries the
intraday tables to pre-populate finished_codes with securities that already
have a 15:00 bar for today — preventing re-processing if the script restarts
after close.

Note: CSV backfill (recovering missed data from archived CSV files) is
handled by the download/archive modules, NOT by this streaming module.

Requires tables from database/sql/stats/02_etf_margin.sql (etf_identity +
etf_intraday_5min), 05_index_baseline.sql (index_identity + index_intraday_5min)
and 06_stock_baseline.sql (stock_identity + stock_intraday_5min). Run those
SQL first.

Usage:
  python -m downloads.stream.sse.price                  # stream all day (60s poll)
  python -m downloads.stream.sse.price --interval 10    # dev: 10s poll interval
  python -m downloads.stream.sse.price --once           # emit one 5-sample bar then exit
  python -m downloads.stream.sse.price --bar-window 3   # dev: 3-sample bars
  python -m downloads.stream.sse.price --no-index       # stream stocks + ETFs + options
  python -m downloads.stream.sse.price --no-etf         # stream stocks + indices + options
  python -m downloads.stream.sse.price --no-options     # stream stocks + ETFs + indices
  python -m downloads.stream.sse.price --options-eod    # capture today's options EOD
                                                        # bars once (post-close), exit
"""
from __future__ import annotations


import argparse
import locale as _locale
import sys
import time as _time
from datetime import date, datetime

from downloads._common import (
    HostStatusTracker,
    build_default_session,
    is_trading_day,
    setup_logger,
)
from downloads._common.exchanges.sse import SSE_HEADERS
from _common.db_commons import get_db_connection
from _common.study_and_select_stocks import (
    ETF_WEIGHT_THRESHOLD,
    load_etf_member_codes,
)

from ._csv_backfill import BACKFILL_INTERVAL_SEC, backfill_all_csvs
from ._fetch import fetch_snapshot
from ._io import (
    _ensure_conn,
    get_intraday_progress,
    is_intraday_complete,
    load_bars,
    write_snapshot_csv,
)
from ._model import (
    DEFAULT_BAR_WINDOW,
    DEFAULT_POLL_INTERVAL_SEC,
    aggregate_bars,
    in_trading_hours,
    next_trading_moment,
    sleep_until,
)
from ._stock import build_stock_asset, prepopulate_stock_finished_codes
from ._etf import build_etf_asset, prepopulate_etf_finished_codes
from ._index import (
    build_index_asset,
    load_existing_index_codes,
    prepopulate_index_finished_codes,
    sync_index_has_intraday_flag,
)
from ._options import (
    OptionsStream,
    aggregate_options_bars,
    build_options_asset,
    load_options_bars,
    load_options_oi_base,
    prepopulate_options_finished_codes,
    refresh_options_universe,
    run_options_eod_capture,
    write_options_snapshot_csv,
)
from downloads._common.exchanges.sse_options import fetch_options_snapshot

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


def _emit_options_bars(conn, asset: OptionsStream, trade_date: date, verb: str):
    """Aggregate + upsert one options bar window, isolated from the other assets.

    The options leg shares the polling loop with stock/etf/index, so an
    options-side failure here must never propagate — one used to kill the
    whole streamer at the day's first bar and starve every live page of
    ticks. Failures are logged and the window dropped (the CSV backfill
    recovers it on a later startup/periodic pass). Returns the (possibly
    reconnected) connection.
    """
    try:
        identity_rows, bar_rows, oi_rows, bar_time = aggregate_options_bars(asset, trade_date)
        if bar_rows:
            conn = _ensure_conn(conn)
            load_options_bars(conn, asset, identity_rows, bar_rows, oi_rows)
            logger.info(
                "%s %d %s bars for %s %s",
                verb, len(bar_rows), asset.name, trade_date, bar_time,
            )
    except Exception as e:  # noqa: BLE001
        logger.error(
            "options bar emission failed for %s: %s: %s",
            trade_date, type(e).__name__, e,
        )
    return conn


# ---------------------------------------------------------------------------
# Main streaming loop
# ---------------------------------------------------------------------------
def stream(
    poll_interval: float = DEFAULT_POLL_INTERVAL_SEC,
    bar_window: int = DEFAULT_BAR_WINDOW,
    once: bool = False,
    enable_index: bool = True,
    enable_etf: bool = True,
    enable_options: bool = True,
) -> None:
    session = build_default_session(SSE_HEADERS)
    host_tracker = HostStatusTracker()

    conn = get_db_connection()
    logger.info("DB ready (stats.stock_intraday_5min / stats.etf_intraday_5min / stats.index_intraday_5min expected to pre-exist).")

    # Load the current ETF-member set once per session (snapshots change
    # quarterly). Used by aggregate_bars to set is_in_index_or_etf on new stock identity
    # rows so they don't default to FALSE before the next backfill runs.
    t0 = _time.time()
    etf_member_codes = load_etf_member_codes(conn)
    logger.info(
        "Loaded %d ETF-member codes (latest snapshot, weight_pct > %.1f%%) in %.1fs",
        len(etf_member_codes), ETF_WEIGHT_THRESHOLD, _time.time() - t0,
    )

    # Build the asset-stream contexts. Stocks stream unconditionally; ETFs
    # and indices are gated on --enable-etf / --enable-index (both default on).
    # Indices are additionally filtered to codes already present in
    # stats.index_identity ("only load to existing index").
    stock_asset = build_stock_asset()
    assets = [stock_asset]

    if enable_etf:
        etf_asset = build_etf_asset()
        assets.append(etf_asset)

    if enable_index:
        t0 = _time.time()
        existing_index_codes = load_existing_index_codes(conn)
        logger.info(
            "Loaded %d existing index codes from stats.index_identity in %.1fs",
            len(existing_index_codes), _time.time() - t0,
        )
        if existing_index_codes:
            index_asset = build_index_asset(existing_index_codes)
            assets.append(index_asset)
        else:
            logger.warning(
                "No existing index codes found in stats.index_identity; "
                "index streaming disabled (run build_csindex.py first to seed "
                "index_identity, then restart this service)."
            )

    # Options stream on by default: sweeps the (underlying, expiry-month)
    # tstyle endpoints each cycle. Universe discovery (underlyings, months,
    # contract-code map) happens here and again on every trade-date rollover.
    options_asset = None
    if enable_options:
        t0 = _time.time()
        options_asset = build_options_asset(session, host_tracker)
        if options_asset is not None:
            assets.append(options_asset)
        else:
            logger.warning(
                "Options universe discovery failed; options streaming "
                "disabled for this run."
            )

    current_trade_date = None
    last_backfill_time = 0.0

    # Run initial CSV backfill at startup to catch up on any missed data
    # before entering the polling loop. Uses DB-completeness guard internally.
    logger.info("Running initial CSV backfill at startup ...")
    t0 = _time.time()
    try:
        n_backfilled = backfill_all_csvs(conn, assets, etf_member_codes=etf_member_codes)
        if n_backfilled > 0:
            logger.info(
                "Initial backfill: %d bars upserted in %.1fs.",
                n_backfilled, _time.time() - t0,
            )
        else:
            logger.info("Initial backfill: no new bars needed (DB already up to date).")
    except Exception as e:
        logger.warning("Initial backfill failed: %s", e)
    last_backfill_time = _time.time()

    # Pre-populate finished_codes from DB: securities that already have a
    # 15:00 bar for today. This prevents re-processing if the script restarts
    # after close. Done per asset (stock/ETF filter exchange='SS'; index
    # has no exchange column).
    today = datetime.now().date()
    if is_trading_day(today):
        for asset in assets:
            t0 = _time.time()
            if asset.name == "stock":
                prepopulate_stock_finished_codes(conn, today, asset.finished_codes)
            elif asset.name == "etf":
                prepopulate_etf_finished_codes(conn, today, asset.finished_codes)
            elif asset.name == "options":
                # Same isolation as the streaming loop: a failed options
                # start must not abort the whole streamer before it begins.
                try:
                    prepopulate_options_finished_codes(conn, asset, today)
                    # OI estimator base: prev-trading-day daily OI per contract.
                    load_options_oi_base(conn, asset, today)
                except Exception as e:  # noqa: BLE001
                    logger.error(
                        "options startup prepopulate failed for %s: %s: %s",
                        today, type(e).__name__, e,
                    )
            else:
                prepopulate_index_finished_codes(conn, today, asset.finished_codes)
            logger.info(
                "Pre-populated %d finished %s codes from DB for %s in %.1fs",
                len(asset.finished_codes), asset.name, today, _time.time() - t0,
            )
        # Set current_trade_date to avoid clearing finished_codes on first iteration.
        current_trade_date = today

    logger.info(
        "stream_sse_price started (poll=%.0fs bar_window=%d once=%s assets=[%s])",
        poll_interval, bar_window, once, ",".join(a.name for a in assets),
    )

    try:
        while True:
            now = datetime.now()

            # Outside trading hours: flush any partial buffers, then sleep
            # until the next trading moment. CSV backfill is handled by
            # download/archive modules (not the streaming loop).
            if not (is_trading_day(now.date()) and in_trading_hours(now)):
                for asset in assets:
                    if not asset.buffer:
                        continue
                    logger.info("Session ended; flushing %d partial %s samples.", len(asset.buffer), asset.name)
                    update_dt = asset.buffer[-1][0]
                    trade_date = update_dt.date()
                    if asset.name == "options":
                        conn = _emit_options_bars(conn, asset, trade_date, "flushed")
                    else:
                        identity_rows, bar_rows, bar_time = aggregate_bars(
                            asset, trade_date, etf_member_codes=etf_member_codes,
                        )
                        if bar_rows:
                            conn = _ensure_conn(conn)
                            load_bars(conn, asset, identity_rows, bar_rows)
                            logger.info(
                                "flushed %d %s bars for %s %s",
                                len(bar_rows), asset.name, trade_date, bar_time,
                            )
                    asset.buffer.clear()
                if once:
                    logger.info("--once set and outside trading hours; exiting.")
                    break
                nxt = next_trading_moment(now)
                logger.info(
                    "Outside trading hours; sleeping until %s.",
                    nxt.strftime("%Y-%m-%d %H:%M"),
                )
                sleep_until(nxt)
                continue

            # --- DB completeness check: if all assets already have complete
            #     intraday data for today (latest bar >= CLOSE_TIME), skip
            #     polling entirely and sleep until the next trading hour.
            today = now.date()
            if is_trading_day(today):
                all_complete = True
                progress_msgs = []
                for asset in assets:
                    p = get_intraday_progress(conn, asset, today)
                    pct = (p["n_codes_done"] / p["n_total_codes"] * 100) if p["n_total_codes"] else 0.0
                    progress_msgs.append(
                        f"{asset.name}: {p['n_codes_done']}/{p['n_total_codes']} codes "
                        f"({pct:.1f}%), max_time={p['max_time']}"
                    )
                    if not p["complete"]:
                        all_complete = False
                if all_complete:
                    logger.info(
                        "All assets have complete intraday data for %s "
                        "(latest bar >= 15:00); skipping polling and sleeping "
                        "until next trading moment. Details: %s",
                        today, "; ".join(progress_msgs),
                    )
                    # Run a final backfill to ensure CSV files are fully
                    # reconciled with DB, then sleep.
                    try:
                        backfill_all_csvs(conn, assets, etf_member_codes=etf_member_codes)
                    except Exception:
                        pass
                    if once:
                        logger.info("--once set and data complete; exiting.")
                        break
                    nxt = next_trading_moment(now)
                    logger.info(
                        "Data complete for %s; sleeping until %s.",
                        today, nxt.strftime("%Y-%m-%d %H:%M"),
                    )
                    sleep_until(nxt)
                    continue

            # --- Periodic CSV backfill (every BACKFILL_INTERVAL_SEC):
            #     Recovers missed data from CSV files, guarded by DB
            #     completeness check inside backfill_csv_file.
            if _time.time() - last_backfill_time >= BACKFILL_INTERVAL_SEC:
                logger.info("Periodic CSV backfill check ...")
                try:
                    n_bf = backfill_all_csvs(conn, assets, etf_member_codes=etf_member_codes)
                    if n_bf > 0:
                        logger.info("Periodic backfill: %d bars upserted.", n_bf)
                except Exception as e:
                    logger.warning("Periodic backfill failed: %s", e)
                last_backfill_time = _time.time()

            # New trading day: reset per-asset streaming state.
            if current_trade_date != now.date():
                current_trade_date = now.date()
                for asset in assets:
                    asset.prev_bar_cumvol.clear()
                    asset.finished_codes.clear()
                    asset.buffer.clear()
                    if asset.name == "options":
                        asset.prev_bar_cumamt.clear()
                if options_asset is not None:
                    # New listings/expiries roll daily — re-discover the
                    # universe (underlyings, months, contract-code map)
                    # and advance the OI base to the fresh daily snapshot.
                    # Same isolation as _emit_options_bars: the options leg
                    # must not take the other assets' day down with it.
                    try:
                        refresh_options_universe(options_asset, session, host_tracker)
                        load_options_oi_base(conn, options_asset, current_trade_date)
                    except Exception as e:  # noqa: BLE001
                        logger.error(
                            "options daily refresh failed for %s: %s: %s",
                            current_trade_date, type(e).__name__, e,
                        )
                logger.info("New trading day %s; per-asset state reset.", current_trade_date)

            cycle_start = _time.time()
            once_done = False  # set when --once emits a bar this cycle
            for asset in assets:
                if asset.name == "options":
                    update_dt, snapshot = fetch_options_snapshot(
                        session, asset.months, host_tracker=host_tracker,
                    )
                else:
                    update_dt, snapshot = fetch_snapshot(
                        session,
                        list_url=asset.list_url,
                        host_tracker=host_tracker,
                        allowed_codes=asset.allowed_codes,
                    )

                if update_dt is None or not snapshot:
                    logger.warning("Poll returned no %s data; skipping cycle.", asset.name)
                    continue

                asset.buffer.append((update_dt, snapshot))
                if asset.name == "options":
                    csv_path = write_options_snapshot_csv(
                        update_dt, snapshot, asset.contract_map,
                        csv_subdir=asset.csv_subdir, csv_prefix=asset.csv_prefix,
                    )
                else:
                    csv_path = write_snapshot_csv(
                        update_dt, snapshot,
                        csv_subdir=asset.csv_subdir, csv_prefix=asset.csv_prefix,
                    )
                logger.info(
                    "sample %d/%d @ %s: %d %ss -> %s",
                    len(asset.buffer), bar_window,
                    update_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    len(snapshot), asset.name,
                    csv_path.name,
                )
                if len(asset.buffer) >= bar_window:
                    trade_date = update_dt.date()
                    if asset.name == "options":
                        conn = _emit_options_bars(conn, asset, trade_date, "emitted")
                    else:
                        identity_rows, bar_rows, bar_time = aggregate_bars(
                            asset, trade_date, etf_member_codes=etf_member_codes,
                        )
                        if bar_rows:
                            conn = _ensure_conn(conn)
                            load_bars(conn, asset, identity_rows, bar_rows)
                            logger.info(
                                "emitted %d %s bars for %s %s (vol baseline=%d codes)",
                                len(bar_rows), asset.name, trade_date, bar_time,
                                len(asset.prev_bar_cumvol),
                            )
                            # Index bars landed: sync the has_intraday_5mins flag
                            # on index_basic_stats for this date so the frontend
                            # knows intraday data is available (replaces the former
                            # csindex.sync_has_intraday_flag post-build step).
                            if asset.name == "index":
                                n = sync_index_has_intraday_flag(conn, trade_date)
                                if n:
                                    logger.info("synced has_intraday_5mins=TRUE for %d index rows", n)
                    asset.buffer.clear()
                    if once:
                        logger.info("--once set; exiting after first bar.")
                        once_done = True
                        break

            if once_done:
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
    ap = argparse.ArgumentParser(description="Stream SSE prices into 5-min OHLCV bars (stocks + ETFs + indices + options).")
    ap.add_argument("--interval", type=float, default=DEFAULT_POLL_INTERVAL_SEC,
                    help=f"Poll interval in seconds (default {DEFAULT_POLL_INTERVAL_SEC}).")
    ap.add_argument("--bar-window", type=int, default=DEFAULT_BAR_WINDOW,
                    help=f"Samples per 5-min bar (default {DEFAULT_BAR_WINDOW}).")
    ap.add_argument("--once", action="store_true",
                    help="Emit one bar then exit (dev/test).")
    ap.add_argument("--no-index", action="store_true",
                    help="Skip the 指数 tab / index_intraday_5min (stream stocks + ETFs + options only).")
    ap.add_argument("--no-etf", action="store_true",
                    help="Skip the 基金 tab / etf_intraday_5min (stream stocks + indices + options only).")
    ap.add_argument("--no-options", action="store_true",
                    help="Skip the 期权 endpoints / options_intraday_5min (stream stocks + ETFs + indices only).")
    ap.add_argument("--options-eod", action="store_true",
                    help="Capture the current SSE options snapshot once as today's 15:00 EOD bars, then exit (post-close daily capture; idempotent).")
    args = ap.parse_args()

    if args.options_eod:
        session = build_default_session(SSE_HEADERS)
        conn = get_db_connection()
        try:
            run_options_eod_capture(session, conn, host_tracker=HostStatusTracker())
        finally:
            try:
                conn.close()
            except Exception:
                pass
            session.close()
        return

    stream(
        poll_interval=args.interval,
        bar_window=args.bar_window,
        once=args.once,
        enable_index=not args.no_index,
        enable_etf=not args.no_etf,
        enable_options=not args.no_options,
    )


if __name__ == "__main__":
    main()
