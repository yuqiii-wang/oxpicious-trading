"""downloads.futures.cffex.trend.config — Configuration for CFFEX trend downloader.

The trend downloader fetches daily futures/options data from the CFFEX
"日统计" (Daily Statistics) page at http://www.cffex.com.cn/cn/rtj.html.

Unlike the archive downloader (which downloads monthly ZIPs), the trend
downloader fetches one day at a time using Playwright browser automation,
filling the gap between the last completed month (archive) and today.
"""

from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta
from typing import List, Optional

from _common._holidays_and_weekdays import is_trading_day, last_business_day
from _common.request_config import request_config_for, service_urls

# ---------------------------------------------------------------------------
# Service row — the runtime source for the URLs, the page timeouts and the
# post-navigation settle sleep (external_request_config JSON, request_type
# "playwright": timeout_connect_sec = page-nav timeout, timeout_read_sec =
# data-load timeout, settle_sleep_sec = post-navigation settle wait).
# ---------------------------------------------------------------------------
SERVICE = "downloads.futures.cffex.trend"
_PLAYWRIGHT_ROW = request_config_for(SERVICE, "playwright")
_URLS = service_urls(SERVICE, "playwright")

# ---------------------------------------------------------------------------
# URLs and paths
# ---------------------------------------------------------------------------
CFFEX_TREND_URL = _URLS["trend_page"]
CFFEX_TREND_REFERER = _URLS["trend_referer"]
CFFEX_BASE_ORIGIN = _URLS["base_origin"]

TREND_DIRNAME = "cffex_trend"
OUTPUT_CSV_ENCODING = "utf-8-sig"

# ---------------------------------------------------------------------------
# Browser settings
# ---------------------------------------------------------------------------
# Playwright browser type and launch options
BROWSER_TYPE = "chromium"
# Headless mode for production; set to False for debugging
HEADLESS = True
# Viewport size matching a typical desktop browser
VIEWPORT_WIDTH = 1920
VIEWPORT_HEIGHT = 1080

# Timeout for page navigation (ms) — the row's timeout_connect_sec.
PAGE_NAVIGATION_TIMEOUT = int(_PLAYWRIGHT_ROW.timeout_connect_sec * 1000)
# Timeout for waiting for data to load (ms) — the row's timeout_read_sec.
DATA_LOAD_TIMEOUT = int(_PLAYWRIGHT_ROW.timeout_read_sec * 1000)

# Post-navigation settle wait before interacting with the page — the row's
# settle_sleep_sec, jittered by the row's sleep_jitter fraction.
SETTLE_SLEEP_SEC: float = _PLAYWRIGHT_ROW.settle_sleep_sec
SETTLE_JITTER: float = _PLAYWRIGHT_ROW.sleep_jitter

# ---------------------------------------------------------------------------
# Product selection
# ---------------------------------------------------------------------------
# Products to download (全部 = all)
PRODUCT_ALL = "全部"
PRODUCTS = ["IF", "IC", "IM", "IH", "TS", "TF", "T", "TL"]

# ---------------------------------------------------------------------------
# CSV format (matches archive CSV columns)
# ---------------------------------------------------------------------------
CSV_HEADERS: list[str] = [
    "合约代码",
    "今开盘",
    "最高价",
    "最低价",
    "成交量",
    "成交金额",
    "持仓量",
    "持仓变化",
    "今收盘",
    "今结算",
    "前结算",
    "涨跌1",
    "涨跌2",
    "Delta",
]

# ---------------------------------------------------------------------------
# Date logic
# ---------------------------------------------------------------------------

def _last_completed_archive_month(today: Optional[date] = None) -> date:
    """Return the first day of the last month that should have archive data.

    CFFEX publishes archive data for completed months. We use the 5th as a
    safe cutoff (same as the archive downloader):
    - On or before the 5th: go back 2 months
    - After the 5th: go back 1 month
    """
    if today is None:
        today = date.today()
    if today.day <= 5:
        last_of_prev = today.replace(day=1) - timedelta(days=1)
        return last_of_prev.replace(day=1) - timedelta(days=32)
    last_of_prev = today.replace(day=1) - timedelta(days=1)
    return last_of_prev.replace(day=1)


def _format_date_for_input(d: date) -> str:
    """Format a date for the trend page date input (YYYY-MM-DD)."""
    return d.strftime("%Y-%m-%d")


def _format_date_for_filename(d: date) -> str:
    """Format a date for the CSV filename (YYYYMMDD)."""
    return d.strftime("%Y%m%d")


def _trading_days_backward(from_date: date, count: int) -> List[date]:
    """Get N trading days before (and including) from_date, going backward."""
    days: List[date] = []
    d = from_date
    while len(days) < count:
        if is_trading_day(d):
            days.append(d)
        d -= timedelta(days=1)
        if d < date(2000, 1, 1):
            break
    return days


def _next_trading_day(d: date) -> date:
    """Get the next trading day on or after d."""
    candidate = d
    while not is_trading_day(candidate):
        candidate += timedelta(days=1)
        if candidate > date.today() + timedelta(days=30):
            break
    return candidate