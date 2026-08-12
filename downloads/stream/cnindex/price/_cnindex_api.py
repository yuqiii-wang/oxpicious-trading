"""CNINDEX (国证指数) intraday API constants and fetch function.

Inlined from the removed ``downloads.index.cnindex.archive`` module.
Used by ``downloads.stream.cnindex.price`` for intraday 1-min bar fetching.

API: https://hq.cnindex.com.cn/market/market/getIndexRealTimeData
  GET params: indexCode
  Response: {
    "code": 200,
    "data": {
      "indexCode": "399303",
      "indexName": "国证2000",
      "item": ["timestamp", "current", "high", "open", "low", "close",
               "chg", "percent", "amount", "volume", "avg"],
      "data": [  # array of 1-min bars (12 elements each; 12th = preClose)
        [timestamp, current, high, open, low, close, chg, percent,
         amount, volume, avg, preClose],
        ...
      ]
    }
  }
  Null rows = non-trading minutes (lunch break, after close).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Optional

from downloads._common.core import (
    COMMON_BASE_HEADERS,
    DEFAULT_SLEEP_SEC,
    AntiBotConfig,
    AntiBotProxy,
)

# CNINDEX-published indices not covered by SSE/SZSE/CSIndex streamers.
#   399303 = 国证2000, 399311 = 国证1000, 399310 = 国证A50
CNINDEX_CODES = ["399303", "399311", "399310"]

# API endpoint
CNINDEX_HQ_BASE = "https://hq.cnindex.com.cn"
CNINDEX_INTRADAY_API = CNINDEX_HQ_BASE + "/market/market/getIndexRealTimeData"

# HTTP headers
CNINDEX_HEADERS: Dict[str, str] = dict(COMMON_BASE_HEADERS)
CNINDEX_HEADERS["Accept"] = "application/json, text/plain, */*"
CNINDEX_HEADERS["Referer"] = "https://www.cnindex.com.cn/"

# Request timeout (connect, read)
CNINDEX_TIMEOUT = (15, 60)

# Bar array column indices:
#   [timestamp, current, high, open, low, close, chg, percent,
#    amount, volume, avg, preClose]
COL_TIMESTAMP = 0
COL_CURRENT = 1
COL_HIGH = 2
COL_OPEN = 3
COL_LOW = 4
COL_CLOSE = 5
COL_PRECLOSE = 11


def _ms_to_date(ms: Any) -> Optional[date]:
    """Convert epoch milliseconds to date."""
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000).date()
    except (ValueError, TypeError, OSError):
        return None


def _to_float(val: Any) -> Optional[float]:
    """Safely convert a value to float, returning None on failure."""
    if val is None:
        return None
    try:
        f = float(val)
        return f if f == f else None  # NaN check (NaN != NaN)
    except (ValueError, TypeError):
        return None


def fetch_intraday_data(
    session,
    code: str,
    proxy: Optional[AntiBotProxy] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch intraday 1-min bars for one index code from cnindex.com.cn.

    Returns the inner ``data`` dict (containing ``data`` = bars array and
    ``indexName``), or None on failure.
    """
    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=DEFAULT_SLEEP_SEC))

    resp = proxy.get(
        session,
        CNINDEX_INTRADAY_API,
        params={"indexCode": code},
        headers=CNINDEX_HEADERS,
        timeout=CNINDEX_TIMEOUT,
    )

    if resp is None:
        return None

    try:
        result = resp.json()
    except (ValueError, TypeError):
        return None

    if not result or result.get("code") != 200:
        return None

    return result.get("data")
