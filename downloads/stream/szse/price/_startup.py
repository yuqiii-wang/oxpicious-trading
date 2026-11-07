"""SZSE streamer startup — DB connections, target stock lists, heavy imports.

Extracted from ``__main__.py`` so the main loop file stays small. Every
method keeps the exact timing log lines the old inline startup code
emitted (same logger name ``stream_szse``).

Class shape: :class:`StreamConns` (the conn-scoped scope, unchanged) gains
the former ``open_db_connections()`` factory as a classmethod; the
startup-orchestration functions (stock lists, heavy-module pre-warm, HTTP
stack, config summary) are methods of :class:`StreamStartup` — constructed
once per daemon run and passed nothing they did not take before (the live
``conn`` stays an explicit argument).
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

from ._constants import HOURLY_MODE_CUTOFF, INDEX_ALWAYS_CODES, TOP_TRADED_N

logger = setup_logger("stream_szse")


class StreamConns:
    """The three dedicated psycopg connections used by the streamer.

    One connection per worker: psycopg connections are NOT safe for
    concurrent use, so each async worker gets its own. ``conn`` is for
    load_target_stocks + index upserts.
    """

    def __init__(self, conn, conn_a, conn_b) -> None:
        self.conn = conn        # main + index upsert
        self.conn_a = conn_a    # sina
        self.conn_b = conn_b    # szse fallback

    def __iter__(self):
        return iter((self.conn, self.conn_a, self.conn_b))

    def close(self) -> None:
        for c in self:
            try:
                c.close()
            except Exception:
                pass

    @classmethod
    def open_db_connections(cls) -> "StreamConns":
        """Open the 3 per-worker DB connections with startup timing logs.

        (The former module-level ``open_db_connections()`` factory, promoted
        onto the conn-scoped class it constructs.)
        """
        t_stream = _time.time()
        logger.info("[startup] stream() entered; opening 3 DB connections...")

        t_step = _time.time()
        conn = get_db_connection()
        logger.info("[startup] DB conn (main + index upsert) ready in %.2fs.", _time.time() - t_step)

        t_step = _time.time()
        conn_a = get_db_connection()
        logger.info("[startup] DB conn_a (sina) ready in %.2fs.", _time.time() - t_step)

        t_step = _time.time()
        conn_b = get_db_connection()
        logger.info("[startup] DB conn_b (szse) ready in %.2fs.", _time.time() - t_step)

        logger.info("[startup] all 3 DB connections ready in %.2fs total (stats.stock_intraday_5min expected to pre-exist).",
                    _time.time() - t_stream)
        return cls(conn, conn_a, conn_b)


class StreamStartup:
    """Startup-orchestration scope for the SZSE streamer — the former
    module-level functions (stock lists, heavy-module pre-warm, HTTP stack,
    config summary) grouped as one stateless object. ``conn`` stays an
    explicit per-call argument."""

    def load_stock_lists(self, conn) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
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

    def import_heavy_modules(self) -> None:
        """Pre-create the network stack: the Sina source's requests.Session."""
        t0 = _time.time()
        logger.info("[startup] creating Sina requests.Session (source A)...")
        from ._sina_source import SINA_SOURCE
        SINA_SOURCE._get_sina_session()
        logger.info("[startup] Sina Session created (source A ready) in %.2fs.", _time.time() - t0)

    def build_http_stack(self) -> Tuple[object, HostStatusTracker]:
        """Build the shared requests session and host status tracker."""
        t0 = _time.time()
        session = build_default_session()
        logger.info("[startup] build_default_session() ready in %.2fs.", _time.time() - t0)

        t0 = _time.time()
        host_tracker = HostStatusTracker()
        logger.info("[startup] HostStatusTracker() ready in %.2fs.", _time.time() - t0)
        return session, host_tracker

    def describe_startup(self, hourly_count: int, full_count: int, poll_interval: float,
                         once: bool, b_top_n: int) -> None:
        """Log the one-line stream configuration summary."""
        logger.info(
            "[startup] stream_szse_price started: hourly=%d stocks (top-%d by amt), "
            "full=%d stocks (ETF members); %d always-on indices=%s; "
            "cutoff=%s; cooldown=%.0fs once=%s b_top_n=%d",
            hourly_count, TOP_TRADED_N, full_count,
            len(INDEX_ALWAYS_CODES), INDEX_ALWAYS_CODES,
            HOURLY_MODE_CUTOFF.strftime("%H:%M"), poll_interval, once, b_top_n,
        )
