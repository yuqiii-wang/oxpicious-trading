"""SZSE streamer startup — DB connections, target stock lists, heavy imports.

Extracted from ``__main__.py`` so the main loop file stays small. Every
function keeps the exact timing log lines the old inline startup code
emitted (same logger name ``stream_szse``).
"""
from __future__ import annotations

import time as _time
from typing import List, Tuple

from downloads._common import (
    HostStatusTracker,
    build_default_session,
    setup_logger,
)

from _common.db_commons import get_db_connection
from _common.study_and_select_stocks import (
    load_target_stocks,
    load_yesterday_top_traded_stocks,
)

from ._akshare_source import _get_akshare
from ._constants import HOURLY_MODE_CUTOFF, INDEX_ALWAYS_CODES, TOP_TRADED_N

logger = setup_logger("stream_szse")


class StreamConns:
    """The five dedicated psycopg connections used by the streamer.

    One connection per worker: psycopg connections are NOT safe for
    concurrent use, so each async worker gets its own. ``conn`` is for
    load_target_stocks + index upserts.
    """

    def __init__(self, conn, conn_a, conn_b, conn_c, conn_d) -> None:
        self.conn = conn        # main + index upsert
        self.conn_a = conn_a    # akshare
        self.conn_b = conn_b    # szse fallback
        self.conn_c = conn_c    # em_push2his
        self.conn_d = conn_d    # em_push2

    def __iter__(self):
        return iter((self.conn, self.conn_a, self.conn_b, self.conn_c, self.conn_d))

    def close(self) -> None:
        for c in self:
            try:
                c.close()
            except Exception:
                pass


def open_db_connections() -> StreamConns:
    """Open the 5 per-worker DB connections with startup timing logs."""
    t_stream = _time.time()
    logger.info("[startup] stream() entered; opening 5 DB connections...")

    t_step = _time.time()
    conn = get_db_connection()
    logger.info("[startup] DB conn (main + index upsert) ready in %.2fs.", _time.time() - t_step)

    t_step = _time.time()
    conn_a = get_db_connection()
    logger.info("[startup] DB conn_a (akshare) ready in %.2fs.", _time.time() - t_step)

    t_step = _time.time()
    conn_b = get_db_connection()
    logger.info("[startup] DB conn_b (szse) ready in %.2fs.", _time.time() - t_step)

    t_step = _time.time()
    conn_c = get_db_connection()
    logger.info("[startup] DB conn_c (em_push2his) ready in %.2fs.", _time.time() - t_step)

    t_step = _time.time()
    conn_d = get_db_connection()
    logger.info("[startup] DB conn_d (em_push2) ready in %.2fs.", _time.time() - t_step)
    logger.info("[startup] all 5 DB connections ready in %.2fs total (stats.stock_intraday_5min expected to pre-exist).",
                _time.time() - t_stream)
    return StreamConns(conn, conn_a, conn_b, conn_c, conn_d)


def load_stock_lists(conn) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
    """Load (full ETF-member list, hourly top-traded list) with empty-list
    fallbacks applied exactly as before. Returns (full_stocks, hourly_stocks).
    """
    t0 = _time.time()
    logger.info("[startup] calling load_target_stocks(conn) [full ETF-member list]...")
    full_stocks: List[Tuple[str, str]] = load_target_stocks(conn)
    logger.info("[startup] Loaded %d full ETF-member SZSE stocks in %.2fs.",
                len(full_stocks), _time.time() - t0)

    t0 = _time.time()
    logger.info("[startup] calling load_yesterday_top_traded_stocks(conn, n=%d)...", TOP_TRADED_N)
    hourly_stocks: List[Tuple[str, str]] = load_yesterday_top_traded_stocks(conn, n=TOP_TRADED_N)
    logger.info("[startup] Loaded %d top-traded SZSE stocks (yesterday by amount) in %.2fs.",
                len(hourly_stocks), _time.time() - t0)

    if not hourly_stocks and full_stocks:
        logger.warning("hourly_stocks empty (no stock_basic_stats data?); "
                       "falling back to full_stocks for hourly mode.")
        hourly_stocks = full_stocks
    if not full_stocks and hourly_stocks:
        logger.warning("full_stocks empty; using hourly_stocks for full mode too.")
        full_stocks = hourly_stocks
    return full_stocks, hourly_stocks


def import_heavy_modules() -> None:
    """Import AkShare up-front (heavy module: pandas/numpy/requests + V8) and
    create the curl_cffi session that drives the East Money sources C/D.
    """
    t0 = _time.time()
    logger.info("[startup] importing AkShare (heavy: pandas/numpy/requests + V8)...")
    _get_akshare()
    logger.info("[startup] AkShare imported (V8 ready) in %.2fs.", _time.time() - t0)

    t0 = _time.time()
    logger.info("[startup] creating curl_cffi Session (EM sources C/D)...")
    from ._em_source import _get_em_session
    _get_em_session()
    logger.info("[startup] curl_cffi Session created (EM sources C/D ready) in %.2fs.", _time.time() - t0)


def build_http_stack() -> Tuple[object, HostStatusTracker]:
    """Build the shared requests session and host status tracker."""
    t0 = _time.time()
    session = build_default_session()
    logger.info("[startup] build_default_session() ready in %.2fs.", _time.time() - t0)

    t0 = _time.time()
    host_tracker = HostStatusTracker()
    logger.info("[startup] HostStatusTracker() ready in %.2fs.", _time.time() - t0)
    return session, host_tracker


def describe_startup(hourly_count: int, full_count: int, poll_interval: float, once: bool) -> None:
    """Log the one-line stream configuration summary."""
    logger.info(
        "[startup] stream_szse_price started: hourly=%d stocks (top-%d by amt), "
        "full=%d stocks (ETF members); %d always-on indices=%s; "
        "cutoff=%s; cooldown=%.0fs once=%s",
        hourly_count, TOP_TRADED_N, full_count,
        len(INDEX_ALWAYS_CODES), INDEX_ALWAYS_CODES,
        HOURLY_MODE_CUTOFF.strftime("%H:%M"), poll_interval, once,
    )
