"""Constants, API endpoints, HTTP session setup, and shared helpers for the
csindex.com.cn quote downloader.

The website (https://www.csindex.com.cn/zh-CN/indices/index#/indices/family/detail?indexCode=000300)
is a Single Page Application. Its axios client uses baseURL "/csindex-home".
All data (chart, export, intraday, PE) is served as JSON/Excel via these endpoints.
"""
from __future__ import annotations

from datetime import date
from typing import Dict, Tuple

import requests

from downloads._common import (
    DEFAULT_SLEEP_SEC,
    COMMON_BASE_HEADERS,
    AntiBotProxy,
    AntiBotConfig,
    setup_logger,
    build_default_session,
    merge_browser_profile,
)

# SZSE indices that must NOT be downloaded from csindex.com.cn
# (they are covered by download_szse_trend.py via East Money API)
CSINDEX_SKIP_CODES = {"399001", "399006", "399348", "399346"}

CSINDEX_BASE = "https://www.csindex.com.cn"

# POST export: daily OHLCV + amount as Excel (body must be a JSON array)
API_EXPORT_PERF = CSINDEX_BASE + "/csindex-home/exportExcel/downloadindex-perf"
API_EXPORT_PERF_TESHU = CSINDEX_BASE + "/csindex-home/exportExcel/downloadindex-perf-teshu"

# GET historical PE (peg) series — supports long date ranges
API_INDEX_CSI_DS_PE = CSINDEX_BASE + "/csindex-home/perf/indexCsiDsPe"

# GET latest-day intraday granular ticks (~15s intervals throughout the trading day)
API_INDEX_PERF_ONEDAY = CSINDEX_BASE + "/csindex-home/perf/index-perf-oneday"

# Static indicator xls (PE1/PE2/dividend yield, ~1 month recent data) — supplemental
INDICATOR_XLS_TEMPLATE = (
    "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/indicator/{indexCode}indicator.xls"
)

DETAIL_REFERER = CSINDEX_BASE + "/zh-CN/indices/index#/indices/family/detail?indexCode={indexCode}"

UPDATE_WINDOW_DAYS = 35  # ~1 month plus weekend/holiday buffer
SLEEP_SEC = DEFAULT_SLEEP_SEC
CSINDEX_TIMEOUT: Tuple[int, int] = (15, 120)

CSINDEX_HEADERS: Dict[str, str] = dict(COMMON_BASE_HEADERS)
CSINDEX_HEADERS["Accept"] = "application/json, text/plain, */*"

EXPORT_HEADERS: Dict[str, str] = dict(CSINDEX_HEADERS)
EXPORT_HEADERS["Content-Type"] = "application/json"

logger = setup_logger("csindex_download")


def build_session() -> requests.Session:
    return build_default_session(merge_browser_profile(CSINDEX_HEADERS))


def make_proxy(sleep_sec: float = SLEEP_SEC) -> AntiBotProxy:
    """Create an AntiBotProxy with the given base sleep interval."""
    return AntiBotProxy(AntiBotConfig(base_sleep_sec=sleep_sec))


def ymd(d: date) -> str:
    """Format date as YYYYMMDD (the API date format, no hyphens)."""
    return d.strftime("%Y%m%d")


def detail_referer(index_code: str) -> str:
    return DETAIL_REFERER.format(indexCode=index_code)
