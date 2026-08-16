"""Constants for the CSIndex intraday streamer.

All tunable parameters and hard-coded code sets live here so they can be
imported by any sub-module without creating circular dependencies.
"""
from __future__ import annotations

from datetime import time

# Loop cadence: 30 minutes between full sweeps.
LOOP_INTERVAL_SEC = 30 * 60  # 1800s

# A code is "stale" if its latest intraday bar is older than this many minutes
# behind the current time (during trading hours). Triggers a re-fetch.
STALE_THRESHOLD_MIN = 30

# Trading hours for stale-checking (don't re-fetch outside trading hours
# unless completely missing).
TRADING_START = time(9, 25)
TRADING_END = time(15, 5)

# CSIndex starts 10 minutes after SSE so SSE has already produced its first
# 2 bars (09:35, 09:40) by the time CSIndex queries which codes SSE is
# streaming. CSIndex then excludes those codes from its download list,
# downloading only indices that SSE does NOT cover.
SSE_HEAD_START_MIN = 10
CSINDEX_START_TIME = time(9, 30 + SSE_HEAD_START_MIN)  # 09:40

# Bond indices (name contains '债') are skipped in CSIndex streaming — they
# typically lack meaningful intraday tick data on csindex.com.cn.
BOND_NAME_KEYWORD = "债"

# Index codes empirically observed to return "no ticks available" from the
# csindex.com.cn intraday API. Hard-skipped to avoid wasting anti-bot sleep
# budget (each fetch otherwise costs ~15-30s).
CSINDEX_NO_TICK_CODES = {
    "931265", "931407", "931528", "931688",
    "931786", "931800", "H11014",
    # SZSE-published 399xxx indices that csindex.com.cn intraday API
    # returns "no ticks available" for.
    "399303", "399310", "399311",
}

# CSV backfill interval: how often the main loop calls backfill_csvs.
BACKFILL_INTERVAL_SEC = 5 * 60  # 5 minutes

# CSV columns for archived intraday bar rows.
CSV_COLUMNS = [
    "update_time", "date", "code", "name", "time",
    "open", "high", "low", "close", "change", "change_pct",
]
