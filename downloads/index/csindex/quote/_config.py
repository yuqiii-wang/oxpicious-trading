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

from database.sec_statics.exclusion_rules_store import excluded_codes

from downloads._common import (
    COMMON_BASE_HEADERS,
    AntiBotProxy,
    setup_logger,
    build_default_session,
    merge_browser_profile,
    service_urls,
    sleep_sec_for,
)

# Codes csindex.com.cn cannot serve (exclusion store: scope "csindex" or
# "all") — the SZSE-published 399xxx family the SZSE downloader owns, the
# cnindex-owned 399303/399310/399311 trio, and the 0008xx/000170/000974
# indices whose daily data SSE bulk trend carries. Curated in
# database/sec_statics/exclusion_rules/index_excluded.json — edit that
# list, not this module. The exchange-map routing (SZSE marker via
# _common.importance) stays the PRIMARY SZSE routing; this set also
# covers codes with no rank row (fresh install before the first monthly
# build) because the JSON ships with the repo.
CSINDEX_SKIP_CODES = excluded_codes("index", scopes=("csindex",))

# The service name this package stamps on its external_resource_audit rows
# (record_http via AntiBotProxy.audit_service). Passed explicitly because
# the runner is LONG-LIVED (spawned by the API's yday-ref chain and the
# nightly scheduler, running far past deploys) — an explicit name keeps
# its audit rows attributed even when the process outlives its code.
# Also the key of this package's external_request_config JSON rows
# (request_type close/export/intraday/pe — the pacing source).
SERVICE = "downloads.index.csindex.quote"

# Endpoint URLs — the service's external_request_config JSON row (urls
# object) is the single source; a missing role raises at the point of use.
URLS = service_urls(SERVICE)

CSINDEX_BASE = URLS["csindex_base"]

# POST export: daily OHLCV + amount as Excel (body must be a JSON array)
API_EXPORT_PERF = URLS["api_export_perf"]
API_EXPORT_PERF_TESHU = URLS["api_export_perf_teshu"]

# GET historical PE (peg) series — supports long date ranges
API_INDEX_CSI_DS_PE = URLS["api_index_csi_ds_pe"]

# GET latest-day intraday granular ticks (~15s intervals throughout the trading day)
API_INDEX_PERF_ONEDAY = URLS["api_index_perf_oneday"]

# Static indicator xls (PE1/PE2/dividend yield, ~1 month recent data) — supplemental
INDICATOR_XLS_TEMPLATE = URLS["indicator_xls_template"]

DETAIL_REFERER = URLS["detail_referer"]

UPDATE_WINDOW_DAYS = 35  # ~1 month plus weekend/holiday buffer

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

# Local hour from which a CN trading day's session counts as publishable
# by CSIndex (market closes 15:00; the close-quote exports start carrying
# the day's rows ~17:00-17:30). Runs at/after this hour expect TODAY's
# session — earlier runs (mornings) still expect only the previous
# session, since overseas/HK indices land their T-1 rows mid-day. The
# gate is content-based either way: a code missing the expected session
# is simply re-fetched (2026-10-17 study: the 18:27-evening sweep gated
# itself to the PREVIOUS session until midnight, so 186 CSI-sourced
# codes' Friday rows only landed on the Monday run).
EOD_EXPECT_HOUR = 17

# Mop-up budget (minutes) for the end-of-sweep retry pass: codes still
# missing the expected session after the single main pass get ONE extra
# 1m fetch (freshness backoff bypassed — it guards cross-RUN retries,
# not a second in-process attempt). Bounded so a total CSIndex outage
# cannot extend the nightly chain unboundedly.
MOPUP_BUDGET_MIN = 45.0

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
