"""Download Shanghai Stock Exchange (SSE) TODAY's price snapshot.

This module is the TODAY/latest-biz-date half of the former ``download_sse_price``.
It fetches the SSE list endpoint — the same JSONP data source used by
``stream_sse_price.py`` (yunhq.sse.com.cn:32042/v1/sh1/list/exchange/equity)
— and writes a single date-grouped CSV under ``temps/sse_trend/``:

    sse_trend_stock_{YYYYMMDD}.csv

This is effectively the end-of-day snapshot: one row per stock with OHLCV +
last price. The trade date is taken from the API response's ``date`` field
(never from ``datetime.now()``), so running on a non-trading day yields the
last trading day's snapshot.

For HISTORICAL data (per-stock ``{code}_trend.csv`` + ``{code}_pe.csv``
archives), use ``download_sse_archive.py`` — it fetches the dayk endpoint.

Output columns (same schema as the archive, empty when not published by SSE):
    交易日期,证券代码,证券简称,前收,开盘,最高,最低,今收,
    涨跌幅（%）,成交量(万股),成交金额(万元),市盈率

Usage:
  python download_sse_trend.py                  # all SSE equities (default)
  python download_sse_trend.py --force          # overwrite existing file
"""
from __future__ import annotations

import time as _time
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from _download_commons import (
    DEFAULT_TIMEOUT,
    MIN_VALID_BYTES,
    HostStatusTracker,
    is_valid_file,
    last_business_day,
    resolve_out_dir,
    setup_logger,
)

# Reuse shared SSE list-endpoint symbols + helpers from the archive module.
# These are the same constants/functions that stream_sse_price historically
# imported from download_sse_price.
from download_sse_archive import (
    COLUMNS,
    CSV_ENCODING,
    JSONP_CALLBACK,
    PAGE_SIZE,
    SSE_HEADERS,
    SSE_LIST_URL,
    _fmt_num,
    _num,
    _parse_jsonp,
    _write_rows,
)


logger = setup_logger("sse_trend")

# Full real-time field set for the SSE list endpoint. The streaming endpoint
# needs last/volume/open/... — unlike the archive's list fetcher which only
# selects code+name (the OHLCV comes from the dayk endpoint there).
STREAM_SELECT_FIELDS = "code,name,open,high,low,last,prev_close,change,volume,amount"

# Inter-page sleep for the list endpoint pagination (lightweight, no anti-bot
# cadence needed — same as stream_sse_price).
INTER_PAGE_SLEEP_SEC = 0.3


# ---------------------------------------------------------------------------
# Output path helper
# ---------------------------------------------------------------------------
def _get_out_file(out_dir: Path, trade_date_str: str) -> Path:
    """Get date-grouped trend CSV path under temps/sse_trend/."""
    ymd = trade_date_str.replace("-", "")
    return out_dir / f"sse_trend_stock_{ymd}.csv"


# ---------------------------------------------------------------------------
# SSE list-endpoint snapshot fetcher (full field set)
# ---------------------------------------------------------------------------
def _extract_trade_date_str(payload: Dict[str, Any]) -> Optional[str]:
    """Extract the trade date from the SSE list endpoint response.

    The yunhq list endpoint returns a top-level ``date`` field in YYYYMMDD
    format — the "更新时间" date shown on the webpage, not the local clock.
    Returns "YYYY-MM-DD" or None if the field is missing/unparseable.
    """
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


def _fetch_snapshot_page(
    session: requests.Session,
    begin: int,
    end: int,
    host_tracker: Optional[HostStatusTracker] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch one page from the SSE list endpoint with full real-time fields.

    Uses a plain session (no AntiBotProxy) so the snapshot is fast — the list
    endpoint is lightweight and does not require the 20s anti-bot cadence
    used for the dayk endpoint. ``host_tracker`` records 4xx errors for
    blocking detection (same pattern as stream_sse_price).
    """
    params = {
        "callback": JSONP_CALLBACK,
        "begin": str(begin),
        "end": str(end),
        "select": STREAM_SELECT_FIELDS,
    }
    try:
        resp = session.get(
            SSE_LIST_URL,
            params=params,
            headers=SSE_HEADERS,
            timeout=DEFAULT_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.warning("SSE list page %d-%d request failed: %s", begin, end, e)
        return None

    if resp.status_code != 200 and host_tracker is not None:
        host_tracker.record_error(SSE_LIST_URL, resp.status_code, resp.reason)

    try:
        return _parse_jsonp(resp.text)
    except ValueError as e:
        logger.warning("SSE list page %d-%d JSONP parse failed: %s", begin, end, e)
        return None


def _parse_snapshot_to_trend_row(
    row: List[Any], trade_date_str: str
) -> Optional[Dict[str, Any]]:
    """Map one SSE list-endpoint row to the trend CSV column schema.

    Row order matches STREAM_SELECT_FIELDS:
    code, name, open, high, low, last, prev_close, change, volume, amount

    The ``last`` field is the latest (closing) price — used as 今收.
    涨跌幅（%）is computed from (last - prev_close) / prev_close * 100,
    consistent with the archive's ``_parse_dayk_row``. The ``change`` field
    from the API is the absolute change amount (not used directly).
    Volume/amount are converted to 万股 / 万元 to match the archive schema.
    """
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
    # row[7] = change (absolute) — not used; we compute pct from last/prev_close
    volume = _num(row[8]) if len(row) > 8 else None
    amount = _num(row[9]) if len(row) > 9 else None

    # 涨跌幅（%）= (last - prev_close) / prev_close * 100
    pct: Any = ""
    if prev_close and last is not None:
        pct = round((last - prev_close) / prev_close * 100, 2)

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


def _fetch_today_snapshot(
    session: requests.Session,
) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """Fetch the SSE list endpoint snapshot (all equities, full field set).

    Paginates through the list endpoint exactly like the webpage's "刷新"
    button (and stream_sse_price). Returns
    ``(trade_date_str, [trend_row, ...])``.
    """
    host_tracker = HostStatusTracker()

    # First page: discover total record count + trade date.
    first = _fetch_snapshot_page(session, 0, PAGE_SIZE, host_tracker)
    if first is None:
        logger.error("Failed to fetch first SSE list page")
        return None, []

    trade_date_str = _extract_trade_date_str(first)
    if trade_date_str is None:
        logger.error("Failed to extract trade date from SSE list response")
        return None, []

    total = int(first.get("total", 0))
    logger.info("SSE snapshot: trade_date=%s total=%d", trade_date_str, total)

    rows: List[Dict[str, Any]] = []
    for row in first.get("list", []) or []:
        parsed = _parse_snapshot_to_trend_row(row, trade_date_str)
        if parsed:
            rows.append(parsed)

    written = len(first.get("list", []) or [])
    page_index = 1
    while written < total:
        if host_tracker.is_blocked(SSE_LIST_URL):
            logger.warning(
                "SSE host blocked mid-pagination, using partial snapshot (%d stocks)",
                len(rows),
            )
            break
        begin = page_index * PAGE_SIZE
        end = begin + PAGE_SIZE
        _time.sleep(INTER_PAGE_SLEEP_SEC)
        payload = _fetch_snapshot_page(session, begin, end, host_tracker)
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
        "Fetched %d rows (total=%d, pages=%d) for trade_date=%s",
        len(rows), total, page_index, trade_date_str,
    )
    return trade_date_str, rows


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def download_sse_trend(
    out_root: Optional[str] = None,
    *,
    force: bool = False,
    session: Optional[requests.Session] = None,
) -> dict:
    """Download SSE TODAY's price snapshot via the list endpoint.

    Fetches the same JSONP endpoint used by ``stream_sse_price.py``
    (yunhq.sse.com.cn:32042/v1/sh1/list/exchange/equity) with the full
    real-time field set (open/high/low/last/prev_close/volume/amount) and
    writes a single date-grouped CSV:

        temps/sse_trend/sse_trend_stock_{YYYYMMDD}.csv

    The trade date (``{YYYYMMDD}`` and the 交易日期 column) is derived
    strictly from the API response's ``date`` field, never from
    ``datetime.now()`` — so running on a non-trading day yields the last
    trading day's snapshot.

    For HISTORICAL data, run ``download_sse_archive.py`` instead (it fetches
    the dayk endpoint and writes per-stock ``{code}_trend.csv`` /
    ``{code}_pe.csv`` archives).

    Loads ALL SSE equities (no ETF filter). Skips when the file for the
    snapshot date already exists unless ``force=True``. The CSV file is
    checked FIRST before any API request to avoid unnecessary network traffic.
    """
    out_dir = resolve_out_dir(str(Path(__file__).resolve()), "sse_trend", out_root)
    sess = session or requests.Session()

    today = date.today()
    expected_latest_biz_date = last_business_day(today)
    expected_latest_str = expected_latest_biz_date.isoformat()
    logger.info(
        "Today: %s, expected latest trading day: %s", today, expected_latest_biz_date,
    )

    # --- Check CSV file FIRST before any DB scan or API request ---
    # This is critical for idempotency and avoiding unnecessary network calls.
    out_file = _get_out_file(out_dir, expected_latest_str)
    if not force and is_valid_file(out_file, min_bytes=MIN_VALID_BYTES):
        # Verify the existing file actually contains data for the expected date
        try:
            with open(out_file, encoding=CSV_ENCODING, newline="") as f:
                reader = __import__("csv").DictReader(f)
                rows = [dict(r) for r in reader]
            if rows:
                # Check if the file contains the expected date
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
                        "out_dir": str(out_dir),
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

    # Fetch the snapshot (paginated, full field set) — ALL SSE equities.
    trade_date_str, rows = _fetch_today_snapshot(sess)
    if trade_date_str is None or not rows:
        logger.error("No snapshot data fetched")
        return {"downloaded": 0, "failed": 1, "out_dir": str(out_dir)}

    # Write the date-grouped CSV.
    _write_rows(out_file, rows, write_header=True)
    logger.info("Wrote %d rows -> %s", len(rows), out_file.name)

    return {
        "downloaded": len(rows),
        "failed": 0,
        "out_dir": str(out_dir),
        "file": str(out_file),
        "trade_date": trade_date_str,
    }


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description="Download SSE TODAY's price snapshot via the list endpoint "
                    "(same data source as stream_sse_price). Writes "
                    "sse_trend_stock_{YYYYMMDD}.csv under temps/sse_trend/. "
                    "Loads ALL SSE equities. For HISTORICAL data, use "
                    "download_sse_archive.py instead."
    )
    ap.add_argument("--force", action="store_true",
                    help="Overwrite existing date CSV file.")
    args = ap.parse_args()
    print(download_sse_trend(force=args.force))
