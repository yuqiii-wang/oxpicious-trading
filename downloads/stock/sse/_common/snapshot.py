"""Shared SSE list-endpoint snapshot helpers (today's EOD snapshot).

Used by ``downloads/stock/sse/trend/__main__.py`` (equity tab) and
``downloads/etf/sse/trend/__main__.py`` (fund tab). Both leaves call
``_download_one_type`` with a different ``list_url`` / ``prefix``.
"""
from __future__ import annotations

import time as _time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from downloads._common.core import (
    DEFAULT_TIMEOUT,
    HostStatusTracker,
    MIN_VALID_BYTES,
    is_valid_file,
    last_business_day,
    resolve_out_dir,
    setup_logger,
)
from downloads.stock.sse._common.list_endpoint import (
    CSV_ENCODING,
    INTER_PAGE_SLEEP_SEC,
    JSONP_CALLBACK,
    PAGE_SIZE,
    SSE_HEADERS,
    STREAM_SELECT_FIELDS,
    _fmt_num,
    _num,
    _parse_jsonp,
    _write_rows,
)

logger = setup_logger("sse_trend")


def _get_out_file(
    out_dir: Path, trade_date_str: str, prefix: str = "sse_trend_stock"
) -> Path:
    """Get date-grouped trend CSV path under temps/sse_trend/."""
    ymd = trade_date_str.replace("-", "")
    return out_dir / f"{prefix}_{ymd}.csv"


def _extract_trade_date_str(payload: Dict[str, Any]) -> Optional[str]:
    """Extract the trade date from the SSE list endpoint response."""
    date_raw = payload.get("date")
    if not date_raw:
        return None
    try:
        date_str = str(date_raw)
        if len(date_str) < 8:
            return None
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    except (ValueError, IndexError):
        return None


def _extract_update_datetime(payload: Dict[str, Any]) -> Optional[datetime]:
    """Extract the 更新时间 (update datetime) from the SSE list endpoint response."""
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
    except (ValueError, IndexError):
        return None


def _fetch_snapshot_page(
    session: requests.Session,
    begin: int,
    end: int,
    list_url: str,
    host_tracker: Optional[HostStatusTracker] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch one page from an SSE list endpoint with full real-time fields."""
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


def _parse_snapshot_to_trend_row(
    row: List[Any], trade_date_str: str
) -> Optional[Dict[str, Any]]:
    """Map one SSE list-endpoint row to the trend CSV column schema."""
    if not row:
        return None
    code = str(row[0]).strip() if row[0] is not None else ""
    if not code:
        return None
    name = str(row[1]).strip() if len(row) > 1 and row[1] is not None else ""
    open_ = _num(row[2]) if len(row) > 2 else None
    high = _num(row[3]) if len(row) > 3 else None
    low = _num(row[4]) if len(row) > 4 else None
    last = _num(row[5]) if len(row) > 5 else None
    prev_close = _num(row[6]) if len(row) > 6 else None
    volume = _num(row[8]) if len(row) > 8 else None
    amount = _num(row[9]) if len(row) > 9 else None

    pct: Any = ""
    if prev_close and last is not None:
        pct = round((last - prev_close) / prev_close * 100, 2)

    vol_wan: Any = ""
    if volume is not None:
        vol_wan = round(volume / 10000, 2)
    amt_wan: Any = ""
    if amount is not None:
        amt_wan = round(amount / 10000, 2)

    return {
        "交易日期": trade_date_str,
        "证券代码": code,
        "证券简称": name,
        "前收": _fmt_num(prev_close),
        "开盘": _fmt_num(open_),
        "最高": _fmt_num(high),
        "最低": _fmt_num(low),
        "今收": _fmt_num(last),
        "涨跌幅（%）": pct,
        "成交量(万股)": vol_wan,
        "成交金额(万元)": amt_wan,
        "市盈率": "",
    }


def _fetch_today_snapshot(
    session: requests.Session,
    list_url: str,
) -> Tuple[Optional[datetime], Optional[str], List[Dict[str, Any]]]:
    """Fetch the SSE list endpoint snapshot (all securities, full field set)."""
    host_tracker = HostStatusTracker()
    asset_tag = list_url.rsplit("/", 1)[-1]

    first = _fetch_snapshot_page(
        session, 0, PAGE_SIZE, list_url=list_url, host_tracker=host_tracker
    )
    if first is None:
        logger.error("Failed to fetch first SSE list page (%s)", asset_tag)
        return None, None, []

    update_dt = _extract_update_datetime(first)
    trade_date_str = _extract_trade_date_str(first)
    if trade_date_str is None:
        logger.error("Failed to extract trade date from SSE list response (%s)", asset_tag)
        return update_dt, None, []

    total = int(first.get("total", 0))
    logger.info(
        "SSE snapshot [%s]: trade_date=%s 更新时间=%s total=%d",
        asset_tag, trade_date_str,
        update_dt.strftime("%Y-%m-%d %H:%M:%S") if update_dt else "N/A",
        total,
    )

    rows: List[Dict[str, Any]] = []
    for row in first.get("list", []) or []:
        parsed = _parse_snapshot_to_trend_row(row, trade_date_str)
        if parsed:
            rows.append(parsed)

    written = len(first.get("list", []) or [])
    page_index = 1
    while written < total:
        if host_tracker.is_blocked(list_url):
            logger.warning(
                "SSE host blocked mid-pagination, using partial snapshot (%d items)",
                len(rows),
            )
            break
        begin = page_index * PAGE_SIZE
        end = begin + PAGE_SIZE
        _time.sleep(INTER_PAGE_SLEEP_SEC)
        payload = _fetch_snapshot_page(
            session, begin, end, list_url=list_url, host_tracker=host_tracker
        )
        if payload is None:
            logger.warning(
                "page %d (begin=%d) failed; using partial snapshot",
                page_index + 1, begin,
            )
            break
        page_rows = payload.get("list", []) or []
        if not page_rows:
            break
        for row in page_rows:
            parsed = _parse_snapshot_to_trend_row(row, trade_date_str)
            if parsed:
                rows.append(parsed)
        written += len(page_rows)
        page_index += 1

    logger.info(
        "Fetched %d rows (total=%d, pages=%d) for trade_date=%s [%s]",
        len(rows), total, page_index, trade_date_str, asset_tag,
    )
    return update_dt, trade_date_str, rows


def download_one_type(
    sess: requests.Session,
    out_dir: Path,
    expected_latest_str: str,
    list_url: str,
    prefix: str,
    *,
    force: bool = False,
) -> dict:
    """Download one snapshot type (equity or fund) and write its CSV."""
    out_file = _get_out_file(out_dir, expected_latest_str, prefix=prefix)

    if not force and is_valid_file(out_file, min_bytes=MIN_VALID_BYTES):
        try:
            with open(out_file, encoding=CSV_ENCODING, newline="") as f:
                reader = __import__("csv").DictReader(f)
                rows = [dict(r) for r in reader]
            if rows:
                dates_in_file = {r.get("交易日期", "") for r in rows}
                if expected_latest_str in dates_in_file:
                    logger.info(
                        "  %s already exists with %d rows for %s, skipping "
                        "(use --force to overwrite)",
                        out_file.name, len(rows), expected_latest_str,
                    )
                    return {
                        "downloaded": 0,
                        "failed": 0,
                        "file": str(out_file),
                        "trade_date": expected_latest_str,
                        "skipped": True,
                        "cached_rows": len(rows),
                    }
                else:
                    logger.info(
                        "  %s exists but does not contain %s, proceeding with fetch",
                        out_file.name, expected_latest_str,
                    )
        except Exception:
            logger.info(
                "  %s exists but is unreadable, proceeding with fetch",
                out_file.name,
            )

    update_dt, trade_date_str, rows = _fetch_today_snapshot(sess, list_url=list_url)
    if trade_date_str is None or not rows:
        logger.error("No snapshot data fetched for %s", prefix)
        return {"downloaded": 0, "failed": 1, "file": str(out_file)}

    _write_rows(out_file, rows, write_header=True)
    logger.info("Wrote %d rows -> %s", len(rows), out_file.name)

    return {
        "downloaded": len(rows),
        "failed": 0,
        "file": str(out_file),
        "trade_date": trade_date_str,
        "update_time": update_dt.strftime("%Y-%m-%d %H:%M:%S") if update_dt else None,
    }


def run_snapshot_download(
    list_url: str,
    prefix: str,
    *,
    out_root: Optional[str] = None,
    force: bool = False,
    session: Optional[requests.Session] = None,
) -> dict:
    """Shared entry point for both equity and fund snapshot leaves."""
    out_dir = resolve_out_dir(__file__, "sse_trend", out_root)
    sess = session or requests.Session()

    today = date.today()
    expected_latest_biz_date = last_business_day(today)
    expected_latest_str = expected_latest_biz_date.isoformat()
    logger.info(
        "Today: %s, expected latest trading day: %s", today, expected_latest_biz_date,
    )

    result = download_one_type(
        sess, out_dir, expected_latest_str,
        list_url, prefix, force=force,
    )
    return {"out_dir": str(out_dir), "result": result}


__all__ = [
    "download_one_type",
    "run_snapshot_download",
]
