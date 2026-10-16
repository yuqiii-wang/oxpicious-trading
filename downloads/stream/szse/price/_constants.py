"""SZSE streamer constants — modes, sampling, pacing and per-source cooldowns.

Split out of ``__main__.py`` so the loop/entry files stay small.
"""
from __future__ import annotations

from datetime import time

from downloads._common import (
    DEFAULT_SLEEP_SEC,
    poll_interval_sec_for,
    sleep_sec_for,
)

# Dotted service name — keys the external_request_config JSON rows (one row
# per source request_type) and the resource audit.
SERVICE = "downloads.stream.szse.price"

# Hourly-mode cutoff: before 15:30 on a trading day the streamer runs in
# "hourly" mode (3 always-on indices + yesterday's top-500 traded SZSE stocks,
# one round per hour). At/after 15:30 it switches to "full" mode (all ETF-member
# stocks, rounds until every stock reaches CLOSE_TIME — the original behavior).
HOURLY_MODE_CUTOFF = time(15, 30)

# Afternoon-only start: the streamer waits until this time on trading days
# before entering the main loop. Morning/intraday data is already captured
# by other streamers; we focus on post-close data.
AFTERNOON_START = time(16, 0)

# SZSE indices always streamed every hour during hourly mode — the
# flagship SZSE-published indices (399001 深证成指, 399006 创业板指).
# Derived from the importance-rank exchange map (codes whose
# download-service exchange carries the SZSE published-index marker —
# stats.sec_importance_ranks, written by the monthly build from the
# curated importance_rules JSONs; cache-file read only, no DB at import).
# The literal list is the fresh-install fallback before the first
# monthly build writes the cache.


def _resolve_index_always_codes() -> list:
    try:
        from _common.importance import (
            load_exchange_map_cache,
            szse_downloader_codes,
        )
        codes = szse_downloader_codes(load_exchange_map_cache())
        if codes:
            return codes
    except Exception:  # noqa: BLE001 — routing must never fail startup
        pass
    return ["399001", "399006"]


INDEX_ALWAYS_CODES: list = _resolve_index_always_codes()

# Number of top-traded SZSE stocks (by yesterday's trading_amount) to sample
# each hour during hourly mode.
TOP_TRADED_N = 500

# Number of HIGHEST-RANK stocks assigned to source B (szse) each round, taken
# from the head of the active stock list (rank = list order: hourly mode is
# yesterday's trading_amount DESC; full mode is code order). Those codes are
# seeded onto B's queue so the official SZSE host — the shortest cadence
# (poll_interval_sec) of the four sources — fetches them first and
# exclusively; A/C/D never see them. B still helps A/C/D on the rest of the
# list afterwards, and circuit-breaker remainders queue behind the priority
# slice (FIFO). 0 disables the priority split (B reverts to pure
# fallback/overflow duty). Overridable via --b-top-n.
B_PRIORITY_TOP_N = 100

# When a round completes but no stock advanced (e.g. pre-open or lunch break),
# back off this long before the next round instead of hammering the API.
NO_ADVANCE_BACKOFF_SEC = 60.0

# Number of groups the target stock list is split into. Groups are processed
# SEQUENTIALLY (one after another, single thread) — not concurrently.
DEFAULT_GROUPS = 10

# Per-source anti-bot cooldowns between fetches (jittered by
# async_random_sleep). Sources hit different hosts so cadences are tuned per
# host aggressiveness; each resolves once at import from the service's
# external_request_config JSON row for that source (request_type):
# Sina (A) sleep=90, both East Money hosts (C/D) sleep=300. B (szse
# fallback) keeps its poll cadence below.
COOLDOWN_A_SEC: float = sleep_sec_for(SERVICE, "source_a_sina")      # akshare  (Sina 1-min)
COOLDOWN_C_SEC: float = sleep_sec_for(SERVICE, "source_c_em_hist")   # em_push2his
COOLDOWN_D_SEC: float = sleep_sec_for(SERVICE, "source_d_em_quote")  # em_push2

# Default per-fetch cooldown for B (szse fallback) and the index round
# (same ssjjhq endpoint as B): the source_b_szse row's poll_interval_sec,
# with the old DEFAULT_SLEEP_SEC (20s) literal as the fallback. Overridable
# via --interval in __main__.py.
DEFAULT_POLL_INTERVAL_SEC: float = (
    poll_interval_sec_for(SERVICE, "source_b_szse") or DEFAULT_SLEEP_SEC
)

# Hard timeout for worker shutdown on termination signal. Workers stuck inside
# asyncio.to_thread (e.g. a DB upsert) can't be cancelled — this prevents the
# gather from hanging forever waiting for them.
SHUTDOWN_TIMEOUT_SEC = 15.0
