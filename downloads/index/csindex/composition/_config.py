"""Shared constants and logger for csindex.com.cn index composition download."""
from __future__ import annotations

from typing import List, Tuple

from database.sec_statics.exclusion_rules_store import excluded_codes

from downloads._common import service_urls, setup_logger, sleep_sec_for

# Service name for the external_request_config JSON row (pacing source).
SERVICE = "downloads.index.csindex.composition"

# Endpoint URLs — the service's external_request_config JSON row (urls
# object) is the single source; a missing role raises at the point of use.
URLS = service_urls(SERVICE)

# ---------------------------------------------------------------------------
# HTTP endpoint
# ---------------------------------------------------------------------------

CLOSEWEIGHT_URL_TEMPLATE = URLS["closeweight_url_template"]

# ---------------------------------------------------------------------------
# Column mapping (bilingual xls headers → normalized names)
# ---------------------------------------------------------------------------
# The xls header row is bilingual (Chinese + English concatenated, no separator).
# We match by substring to be robust against minor header variations.
COLUMN_MATCHERS: List[Tuple[str, str]] = [
    ("日期",              "snapshot_date_raw"),
    ("指数代码",          "index_code"),
    ("指数名称",          "index_name"),
    ("成份券代码",        "stock_code_raw"),
    ("成份券名称",        "stock_name"),
    ("交易所",            "exchange_raw"),
    ("权重",              "weight_pct"),
]

# ---------------------------------------------------------------------------
# Anti-bot cadence
# ---------------------------------------------------------------------------
# Sleep between HTTP requests — resolved from the service's
# external_request_config JSON row (sleep_sec=300 with jitter 0.3 keeps the
# full ~500-index sweep safe from csindex's anti-bot defenses; the same row
# also carries the connect/read timeouts and anti-bot toggles). Overridable
# per-run with --sleep-sec.
SLEEP_SEC: float = sleep_sec_for(SERVICE)

# ---------------------------------------------------------------------------
# Skip-sets
# ---------------------------------------------------------------------------

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

# Bond-market indices track bonds (not stocks), so they don't have meaningful
# composition (closeweight) data. They are tracked via daily index OHLCV
# data instead (downloaded by download_csindex.py).
DEBT_SECTOR_INDUSTRY_IDS = frozenset({"DEBT_TREASURY", "DEBT_CORP"})
DEBT_SECTOR_ID = "DEBT"

# ---------------------------------------------------------------------------
# Shared logger
# ---------------------------------------------------------------------------
logger = setup_logger("csi_index_composition")
