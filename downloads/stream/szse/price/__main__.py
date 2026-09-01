"""Stream SZSE equity AND index prices into 5-min OHLCV bars (entry point).

Two operating modes, switched by wall-clock time on trading days:

  * HOURLY MODE (before 15:30): Streams 2 always-on SZSE indices
    (399001 深证成指, 399006 创业板指) PLUS yesterday's
    top-500 traded SZSE stocks (by trading_amount from stock_basic_stats).
    One round per hour (sleeps until the next HH:00 boundary, or until the
    next trading session if all stocks have already reached CLOSE_TIME).
    Indices go to stats.index_intraday_5min; stocks go to stats.stock_intraday_5min.

  * FULL MODE (at/after 15:30): Streams the full ETF-member stock list
    (load_target_stocks — all SZSE stocks with is_in_index_or_etf=TRUE),
    looping rounds until every stock reaches CLOSE_TIME (15:00). This is
    the original end-of-day sweep behavior.

Afternoon-only start: the streamer does NOT run during the trading session.
On trading days it waits until 16:00 (after close) before entering the main
loop, so all API calls are focused on post-close data.

Architecture (split across this package):
  * ``_constants.py``  — modes cutoffs, sampling sizes, per-source cooldowns.
  * ``_startup.py``    — DB connections, target stock lists, heavy imports,
                         HTTP session + host tracker.
  * ``_rounds.py``     — one stock round via the 4 parallel workers
                         (queue/state/task construction + shutdown protocol).
  * ``_stream.py``     — main loop: biz-day anchoring, hourly/full cycles,
                         pacing/backoff.
  * ``_workers.py``    — async fetch dispatch, circuit breakers, index rounds:
      proc A (akshare)     — AkShare ``ak.stock_zh_a_minute`` (Sina 1-min).
      proc C (em_push2his) — East Money push2his trends2 (ndays=5).
      proc D (em_push2)    — East Money push2 trends2 (ndays=1, iscr=1;
                             a DIFFERENT host from C).
      proc B (szse)        — SZSE ``/api/market/ssjjhq/getTimeData``.
    A, C, D all pull from the primary queue; B drains the fallback queue first
    then the primary. Whoever finishes a group first takes the next group
    (dynamic dispatch). Each worker applies its own anti-bot cooldown between
    fetches; the four hit different hosts (Sina, push2his.eastmoney.com,
    push2.eastmoney.com, szse.cn) so they don't contend on rate limits.
    Crucially only proc A ever touches V8, so the old
    partition_address_space.cc(243) race cannot happen. Index streaming
    (hourly mode only) fetches the always-on indices sequentially via the
    SZSE ssjjhq API; index bars have NO volume column (index_intraday_5min).
  * ``_aggregation.py`` — 1-minute samples → 5-minute OHLCV bars:
      open       = first minute's close (last price) in the window
      high       = max of all minute closes
      low        = min of all minute closes
      close      = last minute's close
      volume     = sum of per-minute volumes across the window (stocks only)
      change     = close - open
      change_pct = (close - open) / open * 100
    Bars are archived to CSV (temps/szse_intraday/) and upserted into
    stats.stock_intraday_5min / stats.index_intraday_5min.

Anti-bot: reuses safe_get(), build_headers_with_referer(), random_sleep,
HostStatusTracker and build_default_session() from _download_commons.py.
Cooldown: per-source anti-bot sleep between fetches (jittered ±50%) —
A (akshare/Sina) uses LONG_SLEEP_INTERVAL (90s); C/D (East Money push2his /
push2) use VERY_LONG_SLEEP_INTERVAL (300s); B (szse fallback) keeps
--interval (DEFAULT_SLEEP_SEC). NO_ADVANCE_BACKOFF_SEC (60s) when no stock
advanced.

Termination: Ctrl-C cancels all async workers (state.stop + task.cancel) and
exits cleanly after reaping them; the finally block closes the DB and session.

Note: CSV backfill (recovering missed data from archived CSV files) is
handled by the download/archive modules, NOT by this streaming module.

Requires tables from database/sql/05_index_baseline.sql (index_identity +
index_intraday_5min) and database/sql/06_stock_baseline.sql (stock_identity +
stock_intraday_5min) and database/sql/03_sec_composition.sql (sec_composition).

Usage:
  python -m downloads.stream.szse.price              # stream (hourly + full modes)
  python -m downloads.stream.szse.price --once       # one round then exit
  python -m downloads.stream.szse.price --groups 5   # 5 groups (sequential, dev)
  python -m downloads.stream.szse.price --interval 1 # 1s cooldown between fetches (dev)
"""
from __future__ import annotations



import argparse
import asyncio
import locale as _locale
import sys
import time as _time

from downloads._common import DEFAULT_SLEEP_SEC, setup_logger

from ._constants import (
    COOLDOWN_A_SEC,
    COOLDOWN_C_SEC,
    COOLDOWN_D_SEC,
    DEFAULT_GROUPS,
)
from ._stream import stream

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


logger = setup_logger("stream_szse")

# Module-load timestamp — used by main() to log total time from import to
# stream() entry, so we can see whether top-level imports (pandas via
# _download_commons, requests, etc.) are the slow part.
_MODULE_LOAD_T0 = _time.time()
logger.info("[startup] module loaded; top-level imports done @ %.2fs.",
            _time.time() - _MODULE_LOAD_T0)


def main() -> None:
    t_main = _time.time()
    logger.info("[startup] main() entered @ %.2fs after module load.",
                _time.time() - _MODULE_LOAD_T0)
    ap = argparse.ArgumentParser(
        description="Stream SZSE equity prices into 5-min OHLCV bars "
                    "(4 parallel procs: A=akshare %.0fs, C=em_push2his %.0fs, "
                    "D=em_push2 %.0fs, B=szse fallback --interval)."
                    % (COOLDOWN_A_SEC, COOLDOWN_C_SEC, COOLDOWN_D_SEC)
    )
    ap.add_argument("--interval", type=float, default=DEFAULT_SLEEP_SEC,
                    help=f"Cooldown between fetches for worker B (szse) in seconds "
                         f"(default {DEFAULT_SLEEP_SEC}).")
    ap.add_argument("--groups", type=int, default=DEFAULT_GROUPS,
                    help=f"Number of groups to split stocks into (default {DEFAULT_GROUPS}).")
    ap.add_argument("--once", action="store_true",
                    help="Run one round then exit (dev/test).")
    args = ap.parse_args()
    logger.info("[startup] args parsed (interval=%.1f groups=%d once=%s) in %.2fs; calling asyncio.run(stream)...",
                args.interval, args.groups, args.once, _time.time() - t_main)
    try:
        asyncio.run(stream(
            poll_interval=args.interval,
            n_groups=max(1, args.groups),
            once=args.once,
        ))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
