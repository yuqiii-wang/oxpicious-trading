"""Download Shanghai Stock Exchange (SSE) daily price snapshot.

Studies ``https://www.sse.com.cn/market/price/report/`` and paginates through
every stock listed on the Shanghai exchange, appending each page's parsed
rows into a single CSV that mirrors the ``szse_trend_stock`` schema.

The page is JS-rendered; the underlying data is served as JSONP by
``https://yunhq.sse.com.cn:32042/v1/sh1/list/exchange/equity`` using
offset-based pagination (``begin`` / ``end``) and a ``total`` record count.

CRITICAL: The filename and 交易日期 column are derived strictly from the
API's ``date`` + ``time`` fields (the "更新时间" displayed on the webpage),
never from ``datetime.now()``. The endpoint only returns the latest snapshot,
so this script fetches and caches it as ``temps/sse_trend/sse_trend_stock_YYYYMMDD.csv``.

Output columns (empty when the field is not published by SSE):
    交易日期,证券代码,证券简称,前收,开盘,最高,最低,今收,
    涨跌幅（%）,成交量(万股),成交金额(万元),市盈率
"""

from __future__ import annotations

import csv
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from _download_commons import (
    DEFAULT_TIMEOUT,
    MIN_VALID_BYTES,
    build_headers_with_referer,
    is_valid_file,
    random_sleep,
    resolve_out_dir,
    setup_logger,
    safe_get,
    HostStatusTracker,
)


SSE_LIST_URL = "https://yunhq.sse.com.cn:32042/v1/sh1/list/exchange/equity"
SSE_REFERER = "https://www.sse.com.cn/market/price/report/"

# Field tokens supported by the SSE list endpoint. The row values come back in
# this exact order. ``change_percent`` / ``pe`` are not published here, so
# 涨跌幅 is derived from change/prev_close and 市盈率 is left blank.
SELECT_FIELDS = "code,name,open,high,low,last,prev_close,change,volume,amount"

# Output column schema (mirrors szse_trend_stock CSV).
COLUMNS: List[str] = [
    "交易日期",
    "证券代码",
    "证券简称",
    "前收",
    "开盘",
    "最高",
    "最低",
    "今收",
    "涨跌幅（%）",
    "成交量(万股)",
    "成交金额(万元)",
    "市盈率",
]

JSONP_CALLBACK = "jQuery1"
PAGE_SIZE = 1000
CSV_ENCODING = "utf-8-sig"

logger = setup_logger("sse_download")

SSE_HEADERS = build_headers_with_referer(SSE_REFERER, extra={"Accept": "*/*"})


_RE_JSONP = re.compile(rf"^{re.escape(JSONP_CALLBACK)}\((.*)\);?\s*$", re.S)


def _parse_jsonp(text: str) -> Dict[str, Any]:
    """Strip the JSONP callback wrapper and parse the inner JSON."""
    m = _RE_JSONP.match(text)
    if m:
        return json.loads(m.group(1))
    # Fallback: grab the first balanced (...) group.
    start = text.find("(")
    end = text.rfind(")")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start + 1 : end])
    raise ValueError("Cannot parse JSONP response from SSE endpoint")


def _fetch_page(
    session: requests.Session,
    begin: int,
    end: int,
    timeout: Tuple[int, int] = DEFAULT_TIMEOUT,
    host_tracker: Optional[HostStatusTracker] = None,
) -> Optional[Dict[str, Any]]:
    params = {
        "callback": JSONP_CALLBACK,
        "begin": str(begin),
        "end": str(end),
        "select": SELECT_FIELDS,
    }
    resp = safe_get(
        session,
        SSE_LIST_URL,
        params=params,
        headers=SSE_HEADERS,
        timeout=timeout,
        host_tracker=host_tracker,
        anti_bot=True,
        logger=logger,
        log_tag=f"[fetch-page {begin}-{end}]",
    )
    if resp is None:
        return None
    return _parse_jsonp(resp.text)


def _num(val: Any) -> Optional[float]:
    """Coerce a JSON value to float, returning None when not numeric."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _fmt_num(val: Any) -> Any:
    """Render a numeric cell: blank for None, otherwise the raw number."""
    if val is None:
        return ""
    return val


def _parse_row(row: List[Any], trade_date_str: str) -> Dict[str, Any]:
    """Map one SSE list row to the output column schema.

    Row order matches SELECT_FIELDS:
    code, name, open, high, low, last, prev_close, change, volume, amount
    """
    code = str(row[0]).strip() if row[0] is not None else ""
    name = str(row[1]).strip() if row[1] is not None else ""

    open_ = _num(row[2]) if len(row) > 2 else None
    high = _num(row[3]) if len(row) > 3 else None
    low = _num(row[4]) if len(row) > 4 else None
    last = _num(row[5]) if len(row) > 5 else None
    prev_close = _num(row[6]) if len(row) > 6 else None
    change = _num(row[7]) if len(row) > 7 else None
    volume = _num(row[8]) if len(row) > 8 else None
    amount = _num(row[9]) if len(row) > 9 else None

    # 涨跌幅（%）= change / prev_close * 100 (computed; endpoint returns null).
    pct: Any = ""
    if prev_close and change is not None:
        pct = round(change / prev_close * 100, 2)

    # SSE returns volume in shares and amount in yuan; convert to 万股 / 万元.
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
        "市盈率": "",  # not published by the SSE list endpoint
    }


def _extract_update_datetime(payload: Dict[str, Any]) -> Optional[datetime]:
    """Extract the update timestamp from the API response's 'date' and 'time' fields.

    The SSE endpoint returns both 'date' (YYYYMMDD) and 'time' (HHMMSS) fields
    which together form the "更新时间" displayed on the webpage (e.g., 2026-07-16 16:29:00).
    This function combines them into a single datetime object.

    Returns None if either field is missing or cannot be parsed.
    """
    date_raw = payload.get("date")
    time_raw = payload.get("time")
    if date_raw is None or time_raw is None:
        logger.warning(
            "SSE response missing update time fields: date=%r time=%r",
            date_raw, time_raw,
        )
        return None
    try:
        date_str = str(date_raw)
        time_str = str(time_raw).zfill(6)
        dt_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]} {time_str[:2]}:{time_str[2:4]}:{time_str[4:6]}"
        return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
    except (ValueError, IndexError) as e:
        logger.warning(
            "Failed to parse SSE update time: date=%r time=%r error=%s",
            date_raw, time_raw, e,
        )
        return None


def _count_csv_rows(path: Path) -> int:
    """Return the number of data rows (excluding header) in a CSV file."""
    if not path.exists():
        return 0
    with open(path, "r", encoding=CSV_ENCODING, newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)
    return max(0, len(rows) - 1)


def _write_page(
    out_file: Path,
    rows: List[Dict[str, Any]],
    *,
    write_header: bool,
) -> None:
    mode = "a" if out_file.exists() and not write_header else "w"
    with open(out_file, mode, encoding=CSV_ENCODING, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        for r in rows:
            writer.writerow(r)


def download_sse_price(
    out_root: Optional[str] = None,
    *,
    page_size: int = PAGE_SIZE,
    sleep_sec: float = 0.8,
    force: bool = False,
    session: Optional[requests.Session] = None,
) -> dict:
    """Download the SSE daily price snapshot using the API's "更新时间".

    Paginates the SSE list endpoint with ``begin``/``end`` offsets until all
    ``total`` records are collected, appending each page's parsed rows to
    ``temps/sse_trend/sse_trend_stock_YYYYMMDD.csv``.

    The filename and 交易日期 column are derived strictly from the API's
    ``date`` + ``time`` fields (the "更新时间" shown on the webpage),
    never from ``datetime.now()``.

    Skips when the file for the returned trade date already contains the
    expected row count unless ``force=True``.
    """
    out_dir = resolve_out_dir(str(Path(__file__).resolve()), "sse_trend", out_root)
    sess = session or requests.Session()
    host_tracker = HostStatusTracker()

    logger.info("Starting SSE price download (page_size=%d)", page_size)

    # First page: discover the update time and total record count.
    first = _fetch_page(sess, 0, page_size, host_tracker=host_tracker)
    if first is None:
        logger.error("Failed to fetch first SSE page")
        return {"downloaded": 0, "failed": 1, "out_dir": str(out_dir)}

    update_dt = _extract_update_datetime(first)
    if update_dt is None:
        logger.error(
            "SSE response missing or invalid update time fields: date=%r time=%r",
            first.get("date"), first.get("time"),
        )
        return {"downloaded": 0, "failed": 1, "out_dir": str(out_dir)}

    trade_date = update_dt.date()
    total = int(first.get("total", 0))
    ymd = trade_date.strftime("%Y%m%d")
    date_str = trade_date.strftime("%Y-%m-%d")
    out_file = out_dir / f"sse_trend_stock_{ymd}.csv"

    logger.info(
        "SSE 更新时间=%s 交易日期=%s total=%d begin=%s end=%s rows=%d",
        update_dt.strftime("%Y-%m-%d %H:%M:%S"),
        date_str,
        total,
        first.get("begin"),
        first.get("end"),
        len(first.get("list", [])),
    )

    if not force and is_valid_file(out_file, min_bytes=MIN_VALID_BYTES):
        existing = _count_csv_rows(out_file)
        if existing >= total:
            logger.info(
                "%s already has %d rows (>= total %d), skipping",
                out_file.name, existing, total,
            )
            return {
                "downloaded": 0,
                "skipped_cached": 1,
                "trade_date": date_str,
                "total": total,
                "rows": existing,
                "out_file": str(out_file),
            }
        logger.info(
            "%s has %d rows (< total %d), re-downloading",
            out_file.name, existing, total,
        )

    # Parse and write the first page (with header).
    rows = [_parse_row(r, date_str) for r in first.get("list", [])]
    _write_page(out_file, rows, write_header=True)
    written = len(rows)
    logger.info("page 1: wrote %d rows -> %s", written, out_file.name)

    # Subsequent pages: append until we've collected `total` records.
    page_index = 1
    while written < total:
        if host_tracker.is_blocked(SSE_LIST_URL):
            logger.warning("  [host-blocked] sse.com.cn is blocked, stopping pagination")
            break

        begin = page_index * page_size
        end = begin + page_size
        random_sleep(sleep_sec)

        payload = _fetch_page(sess, begin, end, host_tracker=host_tracker)
        if payload is None:
            logger.error("page %d (begin=%d) failed: request returned None", page_index + 1, begin)
            break

        page_rows = payload.get("list", []) or []
        if not page_rows:
            logger.info("page %d returned no rows, stopping", page_index + 1)
            break

        parsed = [_parse_row(r, date_str) for r in page_rows]
        _write_page(out_file, parsed, write_header=False)
        written += len(parsed)
        logger.info(
            "page %d: wrote %d rows (cumulative %d / %d)",
            page_index + 1, len(parsed), written, total,
        )
        page_index += 1

    logger.info(
        "Done SSE price. 更新时间=%s 交易日期=%s total=%d written=%d out=%s",
        update_dt.strftime("%Y-%m-%d %H:%M:%S"),
        date_str,
        total,
        written,
        out_file,
    )
    return {
        "downloaded": 1,
        "update_time": update_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "trade_date": date_str,
        "total": total,
        "rows": written,
        "pages": page_index,
        "out_file": str(out_file),
    }


if __name__ == "__main__":
    print(download_sse_price())
