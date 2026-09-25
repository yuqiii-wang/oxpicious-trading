"""Constants and shared setup for the CNINDEX (国证指数) daily archive downloader.

Restores the removed ``downloads.index.cnindex.archive`` module: refreshes
``{code}_history.csv`` under ``temps/cnindex_archive`` (the CSIndex-schema
daily records ``builds.index.baseline.loaders.load_cnindex_history`` reads)
for CNINDEX-published indices that NO other source covers — csindex.com.cn
returns empty exports for them (verified 2026-09-25: header-only 1m and
from2020 files) and the SZSE trend export does not carry them.

API: https://hq.cnindex.com.cn/market/market/getIndexDailyData
  GET params: indexCode
  Response: {"code": 200, "data": {"indexCode", "indexName", "item",
             "data": [ [timestamp, current, high, open, low, close, chg,
                        percent, amount, volume, avg], ... ]}}
  Full history per call (~5KB/decade); amount in YUAN, volume in SHARES
  (unit-verified 2026-09-25 against the 2026-08-07 archive row:
  amount 747812282826.23 ↔ 成交额 7478.12亿; volume 42117380635 ↔
  42117.38万手 × 1e6).
"""
from __future__ import annotations

from typing import Dict

from downloads._common import (
    COMMON_BASE_HEADERS,
    DEFAULT_SLEEP_SEC,
    setup_logger,
)

# CNINDEX-published indices not covered by csindex/SZSE/SSE sources.
#   399303 = 国证2000, 399311 = 国证1000, 399310 = 国证A50
CNINDEX_ARCHIVE_CODES = ["399303", "399310", "399311"]

CNINDEX_HQ_BASE = "https://hq.cnindex.com.cn"
CNINDEX_DAILY_API = CNINDEX_HQ_BASE + "/market/market/getIndexDailyData"

CNINDEX_HEADERS: Dict[str, str] = dict(COMMON_BASE_HEADERS)
CNINDEX_HEADERS["Accept"] = "application/json, text/plain, */*"
CNINDEX_HEADERS["Referer"] = "https://www.cnindex.com.cn/"

# Request timeout (connect, read)
CNINDEX_TIMEOUT = (15, 60)

SLEEP_SEC = DEFAULT_SLEEP_SEC

# Retry backoff for the content-freshness gate (runner.py): when a code's
# history CSV lacks the newest publishable session, the daily history is
# re-fetched at most once per this many hours.
REFETCH_MIN_INTERVAL_HOURS = 6.0

logger = setup_logger("cnindex_archive")
