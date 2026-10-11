"""Shared constants and logger for csindex.com.cn index composition download."""
from __future__ import annotations

from typing import List, Tuple

from downloads._common import setup_logger, sleep_sec_for

# Service name for the external_request_config JSON row (pacing source).
SERVICE = "downloads.index.csindex.composition"

# ---------------------------------------------------------------------------
# HTTP endpoint
# ---------------------------------------------------------------------------

CLOSEWEIGHT_URL_TEMPLATE = (
    "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/"
    "file/autofile/closeweight/{index_code}closeweight.xls"
)

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

# SZSE indices that must NOT be downloaded from csindex.com.cn (they are
# covered by SZSE-specific downloaders).
CSINDEX_SKIP_CODES = {"399001", "399006", "399348", "399346"}

# Bond-market indices track bonds (not stocks), so they don't have meaningful
# composition (closeweight) data. They are tracked via daily index OHLCV
# data instead (downloaded by download_csindex.py).
DEBT_SECTOR_INDUSTRY_IDS = frozenset({"DEBT_TREASURY", "DEBT_CORP"})
DEBT_SECTOR_ID = "DEBT"

# ---------------------------------------------------------------------------
# Shared logger
# ---------------------------------------------------------------------------
logger = setup_logger("csi_index_composition")
