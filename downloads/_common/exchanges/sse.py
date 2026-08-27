"""SSE list-endpoint downloaders + SSE snapshot CSV format conventions.

The SSE ``list/exchange/{equity|fund|index}`` JSONP endpoint (yunhq.sse.com.cn)
powers several downloaders:

  * ``downloads/stock/sse/archive`` — historical per-stock dayk + PE backfill
    (uses the list endpoint only to enumerate target codes via LIST_SELECT_FIELDS)
  * ``downloads/stock/sse/trend``, ``downloads/etf/sse/trend``,
    ``downloads/index/sse/trend`` — today's end-of-day snapshot via
    :func:`run_snapshot_download` / :func:`download_one_type`
  * ``downloads/stream/sse/price`` — intraday 5-minute streaming

Consolidated here because the SSE snapshot CSV schema (COLUMNS below,
written utf-8-sig under ``temps/sse_trend/``) is this exchange's canonical
on-disk format.
"""
from __future__ import annotations

import csv
import json
import re
import time as _time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from _common._holidays_and_weekdays import last_business_day
from downloads._common.filescan import (
    MIN_VALID_BYTES,
    resolve_out_dir,
    is_valid_file,
)
from downloads._common.net import (
    DEFAULT_TIMEOUT,
    HostStatusTracker,
    build_headers_with_referer,
    setup_logger,
)


# --- Endpoint URLs ---------------------------------------------------------
SSE_LIST_URL = "https://yunhq.sse.com.cn:32042/v1/sh1/list/exchange/equity"
# Fund (ETF/LOF) tab — same JSONP schema as the equity endpoint, only the
# path suffix differs (/exchange/fund vs /exchange/equity).
SSE_FUND_LIST_URL = SSE_LIST_URL.replace("/equity", "/fund")
# Index (指数) tab — same JSONP schema, path suffix /exchange/index.
SSE_INDEX_LIST_URL = SSE_LIST_URL.replace("/equity", "/index")
SSE_REFERER = "https://www.sse.com.cn/market/price/trends/"

# --- JSONP / pagination ----------------------------------------------------
JSONP_CALLBACK = "jQuery1"
PAGE_SIZE = 1000

# Minimal field set used by the archive enumerator (code+name only).
LIST_SELECT_FIELDS = "code,name"
# Full real-time field set for the list endpoint. The streaming/today-snapshot
# paths need last/volume/open/... — unlike the archive's list fetcher which
# only selects code+name (the OHLCV comes from the dayk endpoint there).
STREAM_SELECT_FIELDS = "code,name,open,high,low,last,prev_close,change,volume,amount"

# Inter-page sleep for list-endpoint pagination (lightweight, no anti-bot
# cadence needed — same as stream_sse_price).
INTER_PAGE_SLEEP_SEC = 3.0

SSE_HEADERS = build_headers_with_referer(SSE_REFERER, extra={"Accept": "*/*"})

# --- Output schema (mirrors szse_trend_stock CSV) --------------------------
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
CSV_ENCODING = "utf-8-sig"


_RE_JSONP = re.compile(rf"^{re.escape(JSONP_CALLBACK)}\((.*)\);?\s*$", re.S)


logger = setup_logger("sse_trend")


# ---------------------------------------------------------------------------
# Shared helpers (JSONP, number coercion)
# ---------------------------------------------------------------------------
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


def _write_rows(
    out_file: Path,
    rows: List[Dict[str, Any]],
    *,
    write_header: bool,
) -> None:
    """Write rows to CSV file."""
    mode = "a" if out_file.exists() and not write_header else "w"
    with open(out_file, mode, encoding=CSV_ENCODING, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        for r in rows:
            writer.writerow(r)


# ---------------------------------------------------------------------------
# Snapshot helpers (today's EOD snapshot)
# ---------------------------------------------------------------------------

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
    sec_type: str = "auto",
) -> dict:
    """Download one snapshot type (equity / fund / index) and write its CSV."""
    out_file = _get_out_file(out_dir, expected_latest_str, prefix=prefix)

    if not force and is_valid_file(out_file, min_bytes=MIN_VALID_BYTES):
        try:
            with open(out_file, encoding=CSV_ENCODING, newline="") as f:
                reader = csv.DictReader(f)
                file_rows = [dict(r) for r in reader]
            if file_rows:
                dates_in_file = {r.get("交易日期", "") for r in file_rows}
                if expected_latest_str in dates_in_file:
                    logger.info(
                        "  %s already exists with %d rows for %s, skipping "
                        "(use --force to overwrite)",
                        out_file.name, len(file_rows), expected_latest_str,
                    )
                    return {
                        "downloaded": 0,
                        "failed": 0,
                        "file": str(out_file),
                        "trade_date": expected_latest_str,
                        "skipped": True,
                        "cached_rows": len(file_rows),
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
    # canonicalize 证券代码 -> "NNNNNN.SS" + exchange/board/sec_type columns
    from downloads._common.io_csv import ensure_canonical_csv
    ensure_canonical_csv(out_file, "SS", sec_type=sec_type)
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
    """Shared entry point for the equity / fund / index snapshot leaves."""
    out_dir = resolve_out_dir(__file__, "sse_trend", out_root)
    sess = session or requests.Session()

    today = date.today()
    expected_latest_biz_date = last_business_day(today)
    expected_latest_str = expected_latest_biz_date.isoformat()
    logger.info(
        "Today: %s, expected latest trading day: %s", today, expected_latest_biz_date,
    )

    # single-type exports: sse_trend_stock -> stock, sse_trend_etf -> etf,
    # sse_trend_index -> index
    prefix_sec_type = prefix.rsplit("_", 1)[-1] if prefix.rsplit("_", 1)[-1] in (
        "stock", "etf", "index") else "auto"
    result = download_one_type(
        sess, out_dir, expected_latest_str,
        list_url, prefix, force=force, sec_type=prefix_sec_type,
    )
    return {"out_dir": str(out_dir), "result": result}


__all__ = [
    "SSE_LIST_URL",
    "SSE_FUND_LIST_URL",
    "SSE_INDEX_LIST_URL",
    "SSE_REFERER",
    "JSONP_CALLBACK",
    "PAGE_SIZE",
    "LIST_SELECT_FIELDS",
    "STREAM_SELECT_FIELDS",
    "INTER_PAGE_SLEEP_SEC",
    "SSE_HEADERS",
    "COLUMNS",
    "CSV_ENCODING",
    "_RE_JSONP",
    "_parse_jsonp",
    "_num",
    "_fmt_num",
    "_write_rows",
    "DEFAULT_TIMEOUT",
    "download_one_type",
    "run_snapshot_download",
]
