"""Constants for the CSIndex intraday streamer.

All tunable parameters and hard-coded code sets live here so they can be
imported by any sub-module without creating circular dependencies.
"""
from __future__ import annotations

from datetime import time

from database.sec_statics.exclusion_rules_store import excluded_codes

# Loop cadence: 30 minutes between full sweeps.
LOOP_INTERVAL_SEC = 30 * 60  # 1800s

# A code is "stale" if its latest intraday bar is older than this many minutes
# behind the current time (during trading hours). Triggers a re-fetch.
STALE_THRESHOLD_MIN = 30

# Trading hours for stale-checking (don't re-fetch outside trading hours
# unless completely missing).
TRADING_START = time(9, 25)
TRADING_END = time(15, 5)

# CSIndex post-close start: the streamer waits until 16:00 on trading days
# before entering the main loop, so API calls focus on post-close data.
# Intraday data is already captured by SSE/SZSE.
CSINDEX_START_TIME = time(16, 0)

# Bond indices (name contains '债') are skipped in CSIndex streaming — they
# typically lack meaningful intraday tick data on csindex.com.cn.
BOND_NAME_KEYWORD = "债"

# Index codes the streamer must never fetch — curated in
# database/sec_statics/exclusion_rules/index_excluded.json (edit that
# list, not this module), scopes "stream"/"csindex" (+"all" is implicit):
#   - "stream": codes empirically observed to return "no ticks
#     available" from the csindex.com.cn intraday API on every sweep —
#     each fetch otherwise costs ~15-30s of anti-bot sleep budget;
#   - "csindex": codes csindex.com.cn serves nothing for at all (SZSE-
#     published family, cnindex-owned trio, SSE-covered 0008xx).
CSINDEX_NO_TICK_CODES = excluded_codes("index", scopes=("stream", "csindex"))

# CSV backfill interval: how often the main loop calls backfill_csvs.
BACKFILL_INTERVAL_SEC = 5 * 60  # 5 minutes

# CSV columns for archived intraday bar rows.
CSV_COLUMNS = [
    "update_time", "date", "code", "name", "time",
    "open", "high", "low", "close", "change", "change_pct",
]
