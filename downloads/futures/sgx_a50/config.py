"""downloads.futures.sgx_a50.config — endpoints and field mapping for the
Eastmoney global-futures kline API (SGX A50, market 104).

All URLs live in the service's external_request_config JSON row
(downloads/futures/sgx_a50/daily.json) — resolved once at import; a missing
role raises at the point of use.
"""

from __future__ import annotations

from typing import Dict, List

from downloads._common import service_urls

# Pacing service key — sleeps, timeouts, retries, host-block cooldown all
# resolve from this service's external_request_config JSON row.
SERVICE = "downloads.futures.sgx_a50.daily"

_URLS = service_urls(SERVICE)
KLINE_URL: str = _URLS["kline_url"]
CLIST_URL: str = _URLS["clist_url"]
REFERER_URL: str = _URLS["referer_url"]

# Eastmoney secid market prefix for SGX global futures (104) — the A50
# index itself lives under 100 (100.XIN9), the futures chain under 104.
MARKET_FUTURES: int = 104

# Front-month continuous series (当月连续). Eastmoney re-splices it on every
# roll WITHOUT back-adjustment, so its history mutates at each roll — the
# downloader re-fetches it in full on every run.
CONTINUOUS_CODE: str = "CN00Y"

# Standard futures month letters (alphabetical = chronological), used to
# map CN<YY><letter> codes onto contract months for stats.futures_identity.
MONTH_LETTERS: str = "FGHJKMNQUVXZ"

# DB write scope of this family inside the shared futures tables — every
# query/purge filters codes LIKE 'CN%' (SGX A50) so CFFEX/HKEX rows are
# never touched.
FAMILY_CODE_LIKE: str = "CN%"

# Kline fields, canonical ascending order (the response joins them in this
# order regardless of the requested subset, so request the contiguous run
# and map by position):
#   f51 date, f52 open, f53 close, f54 high, f55 low, f56 volume,
#   f57 amount (always 0 for global futures — parsed, then dropped),
#   f58 amplitude %, f59 change_pct, f60 change.
KLINE_FIELDS2: str = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60"

# kline query params shared by every fetch (beg is added per request).
KLINE_COMMON_PARAMS: Dict[str, str] = {
    "fields1": "f1,f3",
    "fields2": KLINE_FIELDS2,
    "klt": "101",      # daily bars
    "fqt": "0",        # no adjustment
    "lmt": "1000000",  # full history in one shot
    "end": "20500101", # open-ended right bound
}

# clist query params: enumerate the whole SGX global-futures board once per
# run; the A50 chain is filtered by name prefix client-side.
CLIST_PARAMS: Dict[str, str] = {
    "pn": "1",
    "pz": "1000",
    "fs": f"m:{MARKET_FUTURES}",
    "fields": "f12,f14",  # code, name
}

# A50 instruments are named "A50期指当月连续" / "A50期指2610" / ...
A50_NAME_PREFIX: str = "A50"

# CSV audit directory (under temps/ unless --out-root overrides).
OUT_DIRNAME: str = "sgx_a50_daily"

# CSV columns for the per-code audit files (same names as the DB columns).
CSV_HEADERS: List[str] = [
    "date", "code", "open", "high", "low", "close",
    "volume", "amplitude", "change", "change_pct",
]
