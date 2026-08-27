"""SZSE streamer constants — modes, sampling, pacing and per-source cooldowns.

Split out of ``__main__.py`` so the loop/entry files stay small.
"""
from __future__ import annotations

from datetime import time

from downloads._common import (
    DEFAULT_SLEEP_SEC,
    LONG_SLEEP_INTERVAL,
    VERY_LONG_SLEEP_INTERVAL,
)

# Hourly-mode cutoff: before 15:30 on a trading day the streamer runs in
# "hourly" mode (3 always-on indices + yesterday's top-500 traded SZSE stocks,
# one round per hour). At/after 15:30 it switches to "full" mode (all ETF-member
# stocks, rounds until every stock reaches CLOSE_TIME — the original behavior).
HOURLY_MODE_CUTOFF = time(15, 30)

# Afternoon-only start: the streamer waits until this time on trading days
# before entering the main loop. Morning-session data (09:30–11:30) is
# already captured by other streamers; we focus on the afternoon session.
AFTERNOON_START = time(13, 30)

# SZSE indices always streamed every hour during hourly mode. These are the
# flagship SZSE indices that were missing from live data (399001 深证成指,
# 399006 创业板指).
INDEX_ALWAYS_CODES: list = ["399001", "399006"]

# Number of top-traded SZSE stocks (by yesterday's trading_amount) to sample
# each hour during hourly mode.
TOP_TRADED_N = 500

# When a round completes but no stock advanced (e.g. pre-open or lunch break),
# back off this long before the next round instead of hammering the API.
NO_ADVANCE_BACKOFF_SEC = 60.0

# Number of groups the target stock list is split into. Groups are processed
# SEQUENTIALLY (one after another, single thread) — not concurrently.
DEFAULT_GROUPS = 10

# Per-source anti-bot cooldowns between fetches (jittered by
# async_random_sleep). Sources hit different hosts so cadences are tuned per
# host aggressiveness: Sina (A) gets LONG_SLEEP, both East Money hosts (C/D)
# get VERY_LONG_SLEEP. B (szse fallback) keeps --interval (DEFAULT_SLEEP_SEC).
COOLDOWN_A_SEC: float = LONG_SLEEP_INTERVAL        # akshare  (Sina 1-min)
COOLDOWN_C_SEC: float = VERY_LONG_SLEEP_INTERVAL   # em_push2his
COOLDOWN_D_SEC: float = VERY_LONG_SLEEP_INTERVAL   # em_push2

# Default per-fetch cooldown for B (szse fallback) and the index round
# (same ssjjhq endpoint as B). Overridable via --interval in __main__.py.
DEFAULT_POLL_INTERVAL_SEC: float = DEFAULT_SLEEP_SEC

# Hard timeout for worker shutdown on termination signal. Workers stuck inside
# asyncio.to_thread (e.g. a DB upsert) can't be cancelled — this prevents the
# gather from hanging forever waiting for them.
SHUTDOWN_TIMEOUT_SEC = 15.0
