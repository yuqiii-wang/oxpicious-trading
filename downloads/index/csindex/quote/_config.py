"""Constants, API endpoints, HTTP session setup, and shared helpers for the
csindex.com.cn quote downloader.

The website (https://www.csindex.com.cn/zh-CN/indices/index#/indices/family/detail?indexCode=000300)
is a Single Page Application. Its axios client uses baseURL "/csindex-home".
All data (chart, export, intraday, PE) is served as JSON/Excel via these endpoints.
"""
from __future__ import annotations

from datetime import date
from typing import Dict

import requests

from downloads._common import (
    COMMON_BASE_HEADERS,
    AntiBotProxy,
    setup_logger,
    build_default_session,
    merge_browser_profile,
    sleep_sec_for,
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

# The service name this package stamps on its external_resource_audit rows
# (record_http via AntiBotProxy.audit_service). Passed explicitly because
# the runner is LONG-LIVED (spawned by the API's yday-ref chain and the
# nightly scheduler, running far past deploys) — an explicit name keeps
# its audit rows attributed even when the process outlives its code.
# Also the key of this package's external_request_config JSON rows
# (request_type close/export/intraday/pe — the pacing source).
SERVICE = "downloads.index.csindex.quote"

# Anti-bot sleep between requests — resolved from the service's
# external_request_config JSON row ("close" request_type: the runner-level
# daily close-quote pacing; the row also carries sleep_jitter, the
# connect/read timeouts and the anti-bot toggles). The per-flow rows
# ("export"/"pe"/"intraday") resolve inside the sub-fetchers. Overridable
# per-run with --sleep-sec.
SLEEP_SEC = sleep_sec_for(SERVICE, "close")
# Retry backoff for the content-freshness gates (runner._run_1m/_run_pe):
# when a code's local records lack the newest publishable session, the
# export is re-fetched at most once per this many hours. Bounds the
# anti-bot cost of codes that never satisfy the content check (indices
# CSIndex stopped publishing) without delaying a laggard index's
# late-landing row past the next scheduled run.
REFETCH_MIN_INTERVAL_HOURS = 6.0

CSINDEX_HEADERS: Dict[str, str] = dict(COMMON_BASE_HEADERS)
CSINDEX_HEADERS["Accept"] = "application/json, text/plain, */*"

EXPORT_HEADERS: Dict[str, str] = dict(CSINDEX_HEADERS)
EXPORT_HEADERS["Content-Type"] = "application/json"

logger = setup_logger("csindex_download")


def build_session() -> requests.Session:
    return build_default_session(merge_browser_profile(CSINDEX_HEADERS))


def make_proxy(request_type: str = "close") -> AntiBotProxy:
    """Create an AntiBotProxy pacing from the request_type's
    external_request_config JSON row (one of close/export/intraday/pe)."""
    return AntiBotProxy(service=SERVICE, request_type=request_type,
                        audit_service=SERVICE)


def ymd(d: date) -> str:
    """Format date as YYYYMMDD (the API date format, no hyphens)."""
    return d.strftime("%Y%m%d")


def detail_referer(index_code: str) -> str:
    return DETAIL_REFERER.format(indexCode=index_code)
