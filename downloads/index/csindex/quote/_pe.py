"""PE (peg) historical series: fetch, cache load/save, and date indexing."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from downloads._common import (
    MIN_VALID_BYTES,
    DEFAULT_SLEEP_SEC,
    AntiBotProxy,
    AntiBotConfig,
    is_valid_file,
    is_error_html,
)

from ._config import (
    API_INDEX_CSI_DS_PE,
    CSINDEX_HEADERS,
    CSINDEX_TIMEOUT,
    logger,
    ymd,
    detail_referer,
)


def fetch_pe_series(
    session: requests.Session,
    index_code: str,
    start_date: date,
    end_date: date,
    proxy: Optional[AntiBotProxy] = None,
) -> List[Dict[str, Any]]:
    """Fetch historical PE (peg) series via the indexCsiDsPe endpoint.

    The ``peg`` field in csindex's API is a PE ratio variant (not the standard
    PEG ratio), with values in the 10-30 range typical of P/E. We treat it as PE.
    """
    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=DEFAULT_SLEEP_SEC))

    params = {
        "indexCode": index_code,
        "startDate": ymd(start_date),
        "endDate": ymd(end_date),
    }
    base_headers = dict(CSINDEX_HEADERS)
    base_headers["Referer"] = detail_referer(index_code)

    resp = proxy.get(
        session,
        API_INDEX_CSI_DS_PE,
        params=params,
        headers=base_headers,
        timeout=CSINDEX_TIMEOUT,
        logger=logger,
        log_tag=f"  [pe-fetch {index_code}]",
    )
    if resp is None:
        logger.warning("  [pe-fetch] %s: request failed", index_code)
        return []

    ctype = resp.headers.get("Content-Type", "")
    if is_error_html(ctype, resp.content):
        logger.warning("  [pe-fetch] %s: got error HTML response (blocked?)", index_code)
        proxy.record_error(API_INDEX_CSI_DS_PE, 403, "error_html_detected")
        return []

    try:
        payload = resp.json()
    except ValueError as e:
        logger.warning("  [pe-fetch] %s: json parse error: %s", index_code, e)
        return []

    if payload.get("code") != "200":
        logger.warning("  [pe-fetch] %s: code=%s msg=%s", index_code, payload.get("code"), payload.get("msg"))
        return []

    data = payload.get("data")
    if not isinstance(data, list):
        return []
    return data


def load_pe_cache(pe_cache_file: Path) -> Optional[List[Dict[str, Any]]]:
    """Load cached PE records from JSON file. Returns None if invalid."""
    if not is_valid_file(pe_cache_file, min_bytes=MIN_VALID_BYTES):
        return None
    try:
        with pe_cache_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        logger.warning("  [pe-cache] %s: unexpected JSON structure, ignoring cache", pe_cache_file.name)
        return None
    except (ValueError, OSError) as e:
        logger.warning("  [pe-cache] %s: load error: %s", pe_cache_file.name, e)
        return None


def save_pe_cache(pe_cache_file: Path, pe_records: List[Dict[str, Any]]) -> bool:
    """Persist PE records to JSON file for future skip."""
    try:
        with pe_cache_file.open("w", encoding="utf-8") as f:
            json.dump(pe_records, f, ensure_ascii=False)
        return True
    except OSError as e:
        logger.warning("  [pe-cache] %s: save error: %s", pe_cache_file.name, e)
        return False


def normalize_pe_date(val: Any) -> str:
    """Normalize a PE tradeDate value to YYYYMMDD string (no hyphens/slashes)."""
    s = str(val).strip()
    if not s or s == "nan":
        return ""
    return s.replace("-", "").replace("/", "")


def index_pe_by_date(pe_records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Index PE records by normalized tradeDate (YYYYMMDD). Later entries win on dup."""
    out: Dict[str, Dict[str, Any]] = {}
    for r in pe_records:
        d = normalize_pe_date(r.get("tradeDate"))
        if d:
            out[d] = r
    return out
