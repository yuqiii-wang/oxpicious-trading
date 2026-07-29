"""Download Beijing Stock Exchange (BSE) daily price snapshot.

Studies ``https://www.bse.cn/nq/quotation.html`` and paginates through every
stock listed on the Beijing exchange, appending each page's parsed rows into a
single CSV that mirrors the ``szse_trend_stock`` / ``sse_trend_stock`` schema.

The page is JS-rendered; the underlying data is served as JSONP (callback name
``null``) by ``https://www.bse.cn/nqhqController/nqhq_en.do`` via POST form
data with offset-based pagination (``page``, 0-indexed) and a
``totalElements`` / ``totalPages`` record count. ``pageSize`` is capped at 20
by the server regardless of the requested value, so the snapshot requires
``ceil(totalElements / 20)`` requests (~293 pages for ~5,848 stocks).

CRITICAL: The filename and 交易日期 column are derived strictly from the
API's per-record ``hqjsrq`` field (the trade date in YYYYMMDD, e.g.
``20260723``), never from ``datetime.now()``. The endpoint only returns the
latest snapshot, so this script fetches and caches it as
``temps/bse_trend/bse_trend_stock_YYYYMMDD.csv``.

Output columns (empty when the field is not published by BSE):
    交易日期,证券代码,证券简称,前收,开盘,最高,最低,今收,
    涨跌幅（%）,成交量(万股),成交金额(万元),市盈率

Field mapping (BSE ``hq*`` → unified schema):
    hqzqdm  -> 证券代码 (zero-padded 6 digits + ".BJ" suffix)
    hqzqjc  -> 证券简称
    hqzrsp  -> 前收 (previous close)
    hqjrkp  -> 开盘 (today's open)
    hqzgcj  -> 最高 (highest)
    hqzdcj  -> 最低 (lowest)
    hqzjcj  -> 今收 (latest / last)
    hqzdf   -> 涨跌幅（%）(already in percent, e.g. -2.54)
    hqcjsl  -> 成交量(万股) (shares → 万股: /10000)
    hqcjje  -> 成交金额(万元) (yuan → 万元: /10000)
    hqsyl1  -> 市盈率 (P/E ratio)
    hqjsrq  -> 交易日期 (YYYYMMDD → YYYY-MM-DD)
    hqgxsj  -> update time HHMMSS (used for logging only)
"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from _download_commons import (
    DEFAULT_TIMEOUT,
    MIN_VALID_BYTES,
    AntiBotProxy,
    AntiBotConfig,
    build_headers_with_referer,
    is_valid_file,
    resolve_out_dir,
    setup_logger,
)


BSE_LIST_URL = "https://www.bse.cn/nqhqController/nqhq_en.do"
BSE_REFERER = "https://www.bse.cn/nq/quotation.html"

# BSE's server caps pageSize at 20 regardless of the requested value, so this
# is the effective maximum and the page count is ceil(totalElements / 20).
PAGE_SIZE = 20

CSV_ENCODING = "utf-8-sig"

logger = setup_logger("bse_download")

BSE_HEADERS = build_headers_with_referer(
    BSE_REFERER,
    extra={
        "Accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Requested-With": "XMLHttpRequest",
    },
)

# Output column schema (mirrors sse_trend_stock / szse_trend_stock CSV).
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

# JSONP wrapper patterns. The BSE endpoint returns ``null([...])`` when no
# callback is supplied, or ``jQuery<digits>_<digits>([...])`` when the page
# sends a ``callback`` form field. We handle both.
_RE_JSONP = re.compile(r"^[\w$.]+\((.*)\);?\s*$", re.S)


def _parse_jsonp(text: str) -> Any:
    """Strip the JSONP callback wrapper and parse the inner JSON.

    Handles ``null([...])`` (no callback) and ``jQuery123_456([...])`` (with
    callback). Falls back to parsing the raw text if no wrapper is detected.
    """
    text = text.strip()
    m = _RE_JSONP.match(text)
    if m:
        return json.loads(m.group(1))
    return json.loads(text)


def _fetch_page(
    session: requests.Session,
    page: int,
    timeout: Tuple[int, int] = DEFAULT_TIMEOUT,
    proxy: Optional[AntiBotProxy] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch one page of BSE quotation data.

    POSTs form-encoded ``page=<n>&pageSize=<PAGE_SIZE>&sortColumn=hqzqdm``
    to the BSE list endpoint and returns the parsed JSON object (the first
    element of the response array), or None on failure.
    """
    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(rotate_browser_profile=False, base_sleep_sec=5.0))
    
    data = {
        "page": str(page),
        "pageSize": str(PAGE_SIZE),
        "sortColumn": "hqzqdm",
        "sortType": "asc",
    }
    # We disable browser profile rotation because BSE needs specific headers
    # (Content-Type / X-Requested-With) that would be clobbered by fingerprint overlay.
    resp = proxy.post(
        session,
        BSE_LIST_URL,
        data=data,
        headers=BSE_HEADERS,
        timeout=timeout,
        logger=logger,
        log_tag=f"[fetch-page {page}] ",
    )
    if resp is None:
        return None
    try:
        payload = _parse_jsonp(resp.text)
    except (ValueError, json.JSONDecodeError) as e:
        logger.error("[fetch-page %d] failed to parse JSONP: %s", page, e)
        return None
    # The endpoint returns a JSON array with a single object whose ``content``
    # key holds the record list. An error response looks like
    # ``[{"msg": "请求参数异常"}]`` (no ``content`` key).
    if not isinstance(payload, list) or not payload:
        logger.error("[fetch-page %d] unexpected payload shape: %s",
                     page, str(payload)[:200])
        return None
    obj = payload[0]
    if not isinstance(obj, dict) or "content" not in obj:
        logger.error("[fetch-page %d] response missing 'content' key: %s",
                     page, str(obj)[:200])
        return None
    return obj


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


def _parse_trade_date(hqjsrq: Any) -> Optional[str]:
    """Parse the per-record ``hqjsrq`` field (YYYYMMDD) into 'YYYY-MM-DD'."""
    if hqjsrq is None:
        return None
    s = str(hqjsrq).strip()
    try:
        dt = datetime.strptime(s, "%Y%m%d")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return None


def _parse_row(row: Dict[str, Any], trade_date_str: str) -> Dict[str, Any]:
    """Map one BSE quotation record to the output column schema.

    Field mapping is documented in the module docstring. Stock codes are
    zero-padded to 6 digits and suffixed with ``.BJ`` (the project convention
    for Beijing Stock Exchange codes, matching stock_identity.code_suffix).
    """
    code_raw = str(row.get("hqzqdm") or "").strip()
    code = f"{code_raw.zfill(6)}.BJ" if code_raw else ""
    name = str(row.get("hqzqjc") or "").strip()

    prev_close = _num(row.get("hqzrsp"))
    open_ = _num(row.get("hqjrkp"))
    high = _num(row.get("hqzgcj"))
    low = _num(row.get("hqzdcj"))
    last = _num(row.get("hqzjcj"))
    pct = _num(row.get("hqzdf"))
    volume = _num(row.get("hqcjsl"))
    amount = _num(row.get("hqcjje"))
    pe = _num(row.get("hqsyl1"))

    # BSE returns volume in shares and amount in yuan; convert to 万股 / 万元
    # to match the sse_trend_stock / szse_trend_stock schema.
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
        "涨跌幅（%）": _fmt_num(pct),
        "成交量(万股)": vol_wan,
        "成交金额(万元)": amt_wan,
        "市盈率": _fmt_num(pe),
    }


def _extract_update_datetime(first_page: Dict[str, Any]) -> Optional[datetime]:
    """Extract the trade date + update time from the first record on page 0.

    BSE does not return a global update timestamp; each record carries
    ``hqjsrq`` (trade date YYYYMMDD) and ``hqgxsj`` (update time HHMMSS).
    All records on a given snapshot share the same values, so we read them
    from the first content record. Returns None if no records or unparseable.
    """
    content = first_page.get("content") or []
    if not content:
        return None
    rec = content[0]
    hqjsrq = rec.get("hqjsrq")
    hqgxsj = rec.get("hqgxsj")
    if hqjsrq is None:
        return None
    try:
        date_str = str(hqjsrq)
        time_str = str(hqgxsj).zfill(6) if hqgxsj is not None else "000000"
        dt_str = (
            f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]} "
            f"{time_str[:2]}:{time_str[2:4]}:{time_str[4:6]}"
        )
        return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
    except (ValueError, IndexError) as e:
        logger.warning(
            "Failed to parse BSE update time: hqjsrq=%r hqgxsj=%r error=%s",
            hqjsrq, hqgxsj, e,
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


def download_bse_price(
    out_root: Optional[str] = None,
    *,
    page_size: int = PAGE_SIZE,
    sleep_sec: float = 5.0,
    force: bool = False,
    session: Optional[requests.Session] = None,
) -> dict:
    """Download the BSE daily price snapshot using the API's trade date.

    Paginates the BSE list endpoint with ``page`` (0-indexed) until all
    ``totalElements`` records are collected, appending each page's parsed
    rows to ``temps/bse_trend/bse_trend_stock_YYYYMMDD.csv``.

    The filename and 交易日期 column are derived strictly from the API's
    per-record ``hqjsrq`` field (the trade date), never from
    ``datetime.now()``.

    Skips when the file for the returned trade date already contains the
    expected row count unless ``force=True``.
    """
    out_dir = resolve_out_dir(str(Path(__file__).resolve()), "bse_trend", out_root)
    sess = session or requests.Session()
    
    # Create unified AntiBotProxy with disabled browser rotation (BSE needs specific headers)
    proxy_config = AntiBotConfig(
        rotate_browser_profile=False,
        base_sleep_sec=sleep_sec,
    )
    proxy = AntiBotProxy(proxy_config)

    logger.info("Starting BSE price download (page_size=%d)", page_size)

    # First page: discover the trade date and total record count.
    first = _fetch_page(sess, 0, proxy=proxy)
    if first is None:
        logger.error("Failed to fetch first BSE page")
        return {"downloaded": 0, "failed": 1, "out_dir": str(out_dir)}

    total = int(first.get("totalElements", 0))
    total_pages = int(first.get("totalPages", 0))

    update_dt = _extract_update_datetime(first)
    if update_dt is None:
        logger.error(
            "BSE response missing or invalid trade date (hqjsrq); first record: %s",
            str((first.get("content") or [{}])[0])[:200],
        )
        return {"downloaded": 0, "failed": 1, "out_dir": str(out_dir)}

    trade_date = update_dt.date()
    ymd = trade_date.strftime("%Y%m%d")
    date_str = trade_date.strftime("%Y-%m-%d")
    out_file = out_dir / f"bse_trend_stock_{ymd}.csv"

    logger.info(
        "BSE 更新时间=%s 交易日期=%s totalElements=%d totalPages=%d page0 rows=%d",
        update_dt.strftime("%Y-%m-%d %H:%M:%S"),
        date_str,
        total,
        total_pages,
        len(first.get("content", [])),
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
    rows = [_parse_row(r, date_str) for r in first.get("content", [])]
    _write_page(out_file, rows, write_header=True)
    written = len(rows)
    logger.info("page 0: wrote %d rows -> %s", written, out_file.name)

    # Subsequent pages: append until we've collected `total` records or
    # reach totalPages. Pages past the last one return empty content arrays.
    page_index = 1
    while written < total and (total_pages == 0 or page_index < total_pages):
        if proxy.is_blocked(BSE_LIST_URL):
            logger.warning("  [host-blocked] bse.cn is blocked, stopping pagination")
            break

        # Auto-sleep is handled by proxy.post() inside _fetch_page
        payload = _fetch_page(sess, page_index, proxy=proxy)
        if payload is None:
            logger.error("page %d failed: request returned None", page_index)
            break

        page_rows = payload.get("content", []) or []
        if not page_rows:
            logger.info("page %d returned no rows, stopping", page_index)
            break

        parsed = [_parse_row(r, date_str) for r in page_rows]
        _write_page(out_file, parsed, write_header=False)
        written += len(parsed)
        logger.info(
            "page %d: wrote %d rows (cumulative %d / %d)",
            page_index, len(parsed), written, total,
        )
        page_index += 1

    logger.info(
        "Done BSE price. 更新时间=%s 交易日期=%s total=%d written=%d out=%s",
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
    print(download_bse_price())
