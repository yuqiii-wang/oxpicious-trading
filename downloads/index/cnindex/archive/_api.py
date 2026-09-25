"""CNINDEX daily-history API fetch (hq.cnindex.com.cn getIndexDailyData)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Optional, Tuple

from downloads._common import (
    DEFAULT_SLEEP_SEC,
    AntiBotConfig,
    AntiBotProxy,
)

from ._config import (
    CNINDEX_DAILY_API,
    CNINDEX_HEADERS,
    CNINDEX_TIMEOUT,
)

# Daily-bar array column indices:
#   [timestamp, current, high, open, low, close, chg, percent, amount,
#    volume, avg]
COL_TIMESTAMP = 0
COL_CURRENT = 1
COL_HIGH = 2
COL_OPEN = 3
COL_LOW = 4
COL_CLOSE = 5
COL_CHG = 6
COL_PERCENT = 7
COL_AMOUNT = 8
COL_VOLUME = 9


def ms_to_date(ms: Any) -> Optional[date]:
    """Convert epoch milliseconds to a date.

    Daily bars are stamped at the session day (00:00 UTC / 08:00 CST), so
    the naive host-local conversion lands on the session date for both UTC
    and CST hosts.
    """
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000).date()
    except (ValueError, TypeError, OSError):
        return None


def to_float(val: Any) -> Optional[float]:
    """Safely convert a value to float, returning None on failure."""
    if val is None:
        return None
    try:
        f = float(val)
        return f if f == f else None  # NaN check (NaN != NaN)
    except (ValueError, TypeError):
        return None


def fetch_daily_data(
    session,
    code: str,
    proxy: Optional[AntiBotProxy] = None,
) -> Optional[Tuple[str, list]]:
    """Fetch the FULL daily history for one index code from cnindex.com.cn.

    Returns (index_name, bars) where *bars* is the raw daily-bar array
    ([timestamp, current, high, open, low, close, chg, percent, amount,
    volume, avg] rows, oldest first), or None on failure.
    """
    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=DEFAULT_SLEEP_SEC))

    resp = proxy.get(
        session,
        CNINDEX_DAILY_API,
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

    data = result.get("data") or {}
    bars = data.get("data") or []
    index_name = data.get("indexName") or code
    return index_name, bars
