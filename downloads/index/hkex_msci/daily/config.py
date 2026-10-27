"""downloads.index.hkex_msci.daily.config — endpoints and constants for the
MSCI China A 50 Connect Index (HKEX marquee RIC .MICN0A5C0PCY) daily close
fetch. This index is the underlying of the HKEX MCA futures family
(stats.futures_identity, codes MCA%) and the spot series the Futures UI
overlays on that family's contract curves.

All URLs live in the service's external_request_config JSON row
(database/sql/monitoring/static/external_request_config/downloads/index/
hkex_msci/daily.json) — resolved once at import; a missing role raises at
the point of use.
"""

from __future__ import annotations

from typing import Dict

from downloads._common import service_urls

# Pacing service key — sleeps, timeouts, retries, host-block cooldown all
# resolve from this service's external_request_config JSON row.
SERVICE = "downloads.index.hkex_msci.daily"

_URLS = service_urls(SERVICE)
PRICES_PAGE_URL: str = _URLS["prices_page"]
WIDGET_DATA_BASE: str = _URLS["widget_data_base"]
REFERER_URL: str = _URLS["referer_url"]

# stats.index_identity / stats.index_basic_stats code — equals the HKEX
# marquee RIC without the leading dot; matches the underlying_code the HKEX
# MCA futures family writes on its identity rows.
INDEX_CODE: str = "MICN0A5C0PCY"
INDEX_NAME: str = "MSCI中国A50互联互通指数"

# LabCI widget RIC of the index (leading dot = index convention there).
CHART_RIC: str = ".MICN0A5C0PCY"

# getchartdata2 granularity is span/int-joint (probed 2026-10-26):
#   span=6&int=4 → ~6 months of DAILY bars (122 rows)
#   span=7&int=6 → ~2 years of WEEKLY bars (~105 rows, the feed's retention)
# Both windows are fetched every run and merged (daily wins per date); the
# weekly tail covers the older half of the MCA futures retention window.
DAILY_SPAN: int = 6
DAILY_INT: int = 4
WEEKLY_SPAN: int = 7
WEEKLY_INT: int = 6

# The per-render LabCI token embedded in every HKEX page's inline
# getToken stub — already URL-encoded as served; used as-is in the query.
RE_TOKEN = r'getToken\s*=\s*function\s*\(\)\s*\{[^}]*?return\s+"([A-Za-z0-9%+/=]+)"'

# Chart-data rows render as [ts_ms, open, high, low, close, volume, extra];
# rows with a null close pad non-trading days and are dropped.
CLOSE_COLUMN: int = 4

# CSV audit directory (under temps/ unless --out-root overrides).
OUT_DIRNAME: str = "hkex_msci_index_daily"

# DB write scope — every query/purge filters this exact code.
TARGET_CODE_LIKE: str = INDEX_CODE
