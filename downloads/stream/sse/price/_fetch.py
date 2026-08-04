"""SSE list endpoint fetch layer — paginated JSONP snapshot retrieval.

Extracted from the former ``stream_sse_price.py`` monolith. Provides the
real-time snapshot fetcher that polls the SSE ``yunhq.sse.com.cn`` list
endpoint (the JSONP backing the "刷新" button on the report page) for both
the equity (股票) and index (指数) tabs.

The fetcher is generic over the ``list_url`` parameter so it can serve both
asset flows:
  * equity endpoint → stock snapshot (full OHLCV + last/volume/amount)
  * index  endpoint → index snapshot (same schema, filtered to tracked codes)
"""
from __future__ import annotations

import time as _time
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import requests

from downloads._common.core import (
    DEFAULT_TIMEOUT,
    HostStatusTracker,
    setup_logger,
)
from downloads.stock.sse._common.list_endpoint import (
    INTER_PAGE_SLEEP_SEC,
    JSONP_CALLBACK,
    PAGE_SIZE,
    SSE_HEADERS,
    SSE_LIST_URL,
    STREAM_SELECT_FIELDS,
    _num,
    _parse_jsonp,
)

logger = setup_logger("stream_sse")


def _extract_update_datetime(payload: Dict[str, Any]) -> Optional[datetime]:
    """Extract the snapshot update datetime from the SSE list endpoint response.

    The yunhq list endpoint returns top-level ``date`` (YYYYMMDD) and ``time``
    (HHMMSS) fields — the "更新时间" shown on the webpage, not the local clock.
    Returns None if the fields are missing or unparseable.
    """
    date_raw = payload.get("date")
    time_raw = payload.get("time")
    if not date_raw:
        return None
    try:
        date_str = str(date_raw)
        time_str = str(time_raw).zfill(6) if time_raw is not None else "000000"
        dt_str = (
            f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]} "
            f"{time_str[:2]}:{time_str[2:4]}:{time_str[4:6]}"
        )
        return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
    except (ValueError, IndexError) as e:
        logger.warning(
            "Failed to parse SSE update time: date=%r time=%r error=%s",
            date_raw, time_raw, e,
        )
        return None


def _fetch_page(
    session: requests.Session,
    begin: int,
    end: int,
    list_url: str = SSE_LIST_URL,
    host_tracker: Optional[HostStatusTracker] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch one page from an SSE list endpoint with full real-time fields.

    Returns the raw parsed JSONP payload (caller drives pagination +
    update-datetime extraction) so the polling cadence is not slowed by the
    anti-bot sleep. ``list_url`` selects the asset type (equity/fund/index);
    ``host_tracker`` records 4xx errors for blocking detection.
    """
    params = {
        "callback": JSONP_CALLBACK,
        "begin": str(begin),
        "end": str(end),
        "select": STREAM_SELECT_FIELDS,
    }
    try:
        resp = session.get(
            list_url,
            params=params,
            headers=SSE_HEADERS,
            timeout=DEFAULT_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.warning("SSE list page %d-%d request failed: %s", begin, end, e)
        return None

    if resp.status_code != 200 and host_tracker is not None:
        host_tracker.record_error(list_url, resp.status_code, resp.reason)

    try:
        return _parse_jsonp(resp.text)
    except ValueError as e:
        logger.warning("SSE list page %d-%d JSONP parse failed: %s", begin, end, e)
        return None


def _parse_snapshot_row(row: list) -> Optional[Dict[str, object]]:
    """Map one SSE list row to a full record dict.

    Row order matches STREAM_SELECT_FIELDS:
    code, name, open, high, low, last, prev_close, change, volume, amount
    """
    if not row:
        return None
    code = str(row[0]).strip() if row[0] is not None else ""
    if not code:
        return None
    return {
        "code": code,
        "name": str(row[1]).strip() if len(row) > 1 and row[1] is not None else "",
        "open": _num(row[2]) if len(row) > 2 else None,
        "high": _num(row[3]) if len(row) > 3 else None,
        "low": _num(row[4]) if len(row) > 4 else None,
        "last": _num(row[5]) if len(row) > 5 else None,
        "prev_close": _num(row[6]) if len(row) > 6 else None,
        "change": _num(row[7]) if len(row) > 7 else None,
        "volume": _num(row[8]) if len(row) > 8 else None,
        "amount": _num(row[9]) if len(row) > 9 else None,
    }


def fetch_snapshot(
    session: requests.Session,
    list_url: str = SSE_LIST_URL,
    page_size: int = PAGE_SIZE,
    host_tracker: Optional[HostStatusTracker] = None,
    allowed_codes: Optional[set] = None,
) -> Tuple[Optional[datetime], dict]:
    """Fetch all SSE securities of one type in a single snapshot.

    Returns (update_dt, {bare_code: full_record}) or (None, {}).
    The ``update_dt`` comes from the API's date+time fields (the "更新时间" on
    the webpage), not from local clock.

    ``list_url`` selects the asset type (equity/fund/index). When
    ``allowed_codes`` is supplied, rows whose bare code is NOT in the set are
    dropped before entering the snapshot — used for indices to implement
    "only load to existing index" (skip the ~200 SSE indices we don't track).
    """
    first = _fetch_page(session, 0, page_size, list_url=list_url, host_tracker=host_tracker)
    if first is None:
        return None, {}
    update_dt = _extract_update_datetime(first)
    if update_dt is None:
        return None, {}

    snapshot: dict = {}
    for row in first.get("list", []) or []:
        rec = _parse_snapshot_row(row)
        if rec and (allowed_codes is None or rec["code"] in allowed_codes):
            snapshot[rec["code"]] = rec

    total = int(first.get("total", 0))
    written = len(first.get("list", []) or [])
    page_index = 1
    while written < total:
        if host_tracker and host_tracker.is_blocked(list_url):
            logger.warning(
                "SSE host blocked mid-pagination (%s), using partial snapshot (%d codes)",
                list_url, len(snapshot),
            )
            break
        begin = page_index * page_size
        end = begin + page_size
        _time.sleep(INTER_PAGE_SLEEP_SEC)
        payload = _fetch_page(session, begin, end, list_url=list_url, host_tracker=host_tracker)
        if payload is None:
            logger.warning("page %d (begin=%d) failed; using partial snapshot", page_index + 1, begin)
            break
        page_rows = payload.get("list", []) or []
        if not page_rows:
            break
        for row in page_rows:
            rec = _parse_snapshot_row(row)
            if rec and (allowed_codes is None or rec["code"] in allowed_codes):
                snapshot[rec["code"]] = rec
        written += len(page_rows)
        page_index += 1

    return update_dt, snapshot
