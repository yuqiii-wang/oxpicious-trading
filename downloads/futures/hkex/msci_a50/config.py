"""downloads.futures.hkex.msci_a50.config — endpoints and field mapping for
the HKEX MSCI China A 50 Connect (USD) Index Futures daily market reports.

All URLs live in the service's external_request_config JSON row
(downloads/futures/hkex/msci_a50/daily.json) — resolved once at import; a
missing role raises at the point of use.
"""

from __future__ import annotations

from typing import Dict

from downloads._common import service_urls

# Pacing service key — sleeps, timeouts, retries, host-block cooldown all
# resolve from this service's external_request_config JSON row.
SERVICE = "downloads.futures.hkex.msci_a50.daily"

_URLS = service_urls(SERVICE)
REPORT_BASE: str = _URLS["report_base"]
REFERER_URL: str = _URLS["referer_url"]

# Report zips under REPORT_BASE — {yymmdd} is the HKEX trading date:
#   mcaf{YYMMDD}.zip  Daily Market Report (FINAL): the PREVIOUS trading
#       day's after-hours session + this date's day session + settlement
#       and open interest per contract month.
#   mcafa{YYMMDD}.zip After-hours activity summary: the T+1 session that
#       ran the EVENING of {date} (published ~03:15 HKT the next morning).
DAY_REPORT_ZIP: str = "mcaf{yymmdd}.zip"
AH_REPORT_ZIP: str = "mcafa{yymmdd}.zip"

# Contract months report as "NOV-26"; the normalized code is
# MCA<YY><month letter> (MCA = the HKATS product code; letters are the
# standard futures month codes, alphabetically chronological).
MONTH_LETTERS: Dict[int, str] = {
    1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
    7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z",
}

# DB write scope of this family inside the shared futures tables — every
# query/purge filters codes LIKE 'MCA%' so CFFEX/SGX rows are untouched.
FAMILY_CODE_LIKE: str = "MCA%"

# Archive retention on the exchange side — daily report zips older than
# roughly one year return 404 (probed 2026-10-26: 2025-10-27 exists,
# 2025-09-26 does not). Bounds the initial backfill; incremental runs
# never need dates this old.
RETENTION_DAYS: int = 400

# CSV audit directory (under temps/ unless --out-root overrides).
OUT_DIRNAME: str = "hkex_mca_daily"
