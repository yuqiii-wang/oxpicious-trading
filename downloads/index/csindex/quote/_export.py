"""Export Excel download (daily OHLCV + amount history)."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Optional

import requests

from downloads._common.core import (
    MIN_VALID_BYTES,
    DEFAULT_SLEEP_SEC,
    AntiBotProxy,
    AntiBotConfig,
    is_error_html,
    safe_write_bytes,
)

from ._config import (
    API_EXPORT_PERF,
    API_EXPORT_PERF_TESHU,
    CSINDEX_TIMEOUT,
    EXPORT_HEADERS,
    logger,
    make_proxy,
    ymd,
    detail_referer,
)


def download_export_excel(
    session: requests.Session,
    index_code: str,
    start_date: date,
    end_date: date,
    out_file: Path,
    proxy: Optional[AntiBotProxy] = None,
    auto_convert: bool = True,
) -> bool:
    """Download daily OHLCV+amount history via the POST export Excel endpoint.

    The body must be a JSON **array** (a single object returns HTTP 500).
    Some "special" indices (e.g. 000010) require the ``-teshu`` variant.

    When *auto_convert* is True (default), the downloaded xlsx is also
    converted to a companion CSV via :func:`safe_write_bytes` (overwriting
    any existing CSV). Set to False when the caller wants to merge the new
    data into an existing CSV incrementally (see
    :func:`downloads.index.csindex.quote._history.append_missing_dates_to_csv`).
    """
    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=DEFAULT_SLEEP_SEC))

    body = json.dumps([
        {"indexCode": index_code, "startDate": ymd(start_date), "endDate": ymd(end_date)}
    ])
    base_headers = dict(EXPORT_HEADERS)
    base_headers["Referer"] = detail_referer(index_code)

    for url, label in ((API_EXPORT_PERF, "regular"), (API_EXPORT_PERF_TESHU, "teshu")):
        resp = proxy.post(
            session,
            url,
            params={"language": "CH"},
            data=body,
            headers=base_headers,
            timeout=CSINDEX_TIMEOUT,
            logger=logger,
            log_tag=f"  [export {label} {index_code}]",
        )
        if resp is None:
            continue

        ctype = resp.headers.get("Content-Type", "")
        content = resp.content

        # Successful export returns a binary Excel (zip) file
        is_xlsx = content[:4] == b"PK\x03\x04" or "excel" in ctype.lower() or "octet-stream" in ctype.lower()
        if is_xlsx and len(content) >= MIN_VALID_BYTES:
            tag = f"[export {label} {index_code} {ymd(start_date)}~{ymd(end_date)}]"
            if safe_write_bytes(out_file, content, min_bytes=MIN_VALID_BYTES, logger=logger, log_tag=tag, auto_convert=auto_convert):
                return True
            continue

        # Check for error HTML (anti-bot block page)
        if is_error_html(ctype, content):
            logger.warning("  [export-%s] %s got error HTML response (blocked?)", label, index_code)
            proxy.record_error(url, 403, "error_html_detected")
            continue

        # Otherwise it's likely a JSON error — try the teshu variant
        try:
            payload = resp.json()
            msg = payload.get("msg") or payload.get("message") or ""
            logger.debug("  [export-%s] %s returned JSON: code=%s msg=%s", label, index_code, payload.get("code"), msg)
        except (ValueError, AttributeError):
            logger.debug("  [export-%s] %s returned non-Excel, non-JSON (%d bytes)", label, index_code, len(content))
        continue

    logger.error("  [export-failed] %s %s~%s (both regular and teshu exhausted)", index_code, start_date, end_date)
    return False
