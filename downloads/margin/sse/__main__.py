"""Download Shanghai Stock Exchange (SSE) margin (融资融券) data.

Studies ``https://www.sse.com.cn/market/othersdata/margin/sum/`` and uses the
underlying JSONP API at ``https://query.sse.com.cn/commonSoaQuery.do`` that
the page's ``search_otherData_2021.js`` calls into.

Two report types are fetched per trade date:

* **summary** (``sqlId=RZRQ_HZ_INFO``): market-wide daily totals — single row
  per day with 融资余额/融资买入额/融资偿还额/融券余量/融券余量金额/融券卖出量/融资融券余额.
* **detail**  (``sqlId=RZRQ_MX_INFO``): per-security margin data — ~1900 rows
  per day paginated via ``pageHelp.pageNo`` / ``pageHelp.pageSize``.

Output:
  ``temps/sse_margin/sse_margin_summary_YYYYMMDD.csv``
  ``temps/sse_margin/sse_margin_detail_YYYYMMDD.csv``

Reuses anti-bot machinery (``safe_get``, ``HostStatusTracker``, browser
profile rotation, random sleep) and the margin summary/detail split pattern
from ``_download_commons`` and ``download_szse_margin``.

CRITICAL: Stock codes in the detail CSV are suffixed with ``.SS`` per the
project convention (Shanghai-listed securities).
"""

from __future__ import annotations


import csv
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from downloads._common import (
    DEFAULT_START_DATE,
    DEFAULT_TIMEOUT,
    EMPTY_MARKER_RETRY_DAYS,
    AntiBotProxy,
    AntiBotConfig,
    build_headers_with_referer,
    business_days,
    last_business_day,
    resolve_out_dir,
    setup_logger,
    single_instance_lock,
)


SSE_QUERY_URL = "https://query.sse.com.cn/commonSoaQuery.do"
SSE_REFERER = "https://www.sse.com.cn/market/othersdata/margin/sum/"
JSONP_CALLBACK = "jsonpCallback"

SUMMARY_SQL_ID = "RZRQ_HZ_INFO"
DETAIL_SQL_ID = "RZRQ_MX_INFO"

# The API accepts large page sizes for detail; use 100 to minimize # of
# paginated requests (~1900 rows → ~19 pages instead of ~77 at pageSize=25).
DETAIL_PAGE_SIZE = 100
SUMMARY_PAGE_SIZE = 25

CSV_ENCODING = "utf-8-sig"

logger = setup_logger("sse_margin_download")

SSE_HEADERS = build_headers_with_referer(SSE_REFERER, extra={"Accept": "*/*"})

# Output column schemas — mirror the SSE webpage table headers, with the
# 融资偿还额 column retained for summary (API returns it even though the
# default summary table view hides it). Detail 融券 columns use the
# "(股/份)" unit suffix to match the SZSE margin detail schema.
SUMMARY_COLUMNS: List[str] = [
    "信用交易日期",
    "融资余额(元)",
    "融资买入额(元)",
    "融资偿还额(元)",
    "融券余量",
    "融券余量金额(元)",
    "融券卖出量",
    "融资融券余额(元)",
]

DETAIL_COLUMNS: List[str] = [
    "信用交易日期",
    "证券代码",
    "证券简称",
    "融资余额(元)",
    "融资买入额(元)",
    "融资偿还额(元)",
    "融券余量(股/份)",
    "融券卖出量(股/份)",
    "融券偿还量(股/份)",
]


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
    sql_id: str,
    trade_date: date,
    page_no: int,
    page_size: int,
    proxy: AntiBotProxy,
) -> Optional[Dict[str, Any]]:
    """Fetch one page from the SSE commonSoaQuery JSONP endpoint.

    ``sql_id`` selects summary (RZRQ_HZ_INFO) vs detail (RZRQ_MX_INFO).
    Single-day queries set ``beginDate`` = ``endDate`` = trade_date (YYYYMMDD).
    """
    ymd = trade_date.strftime("%Y%m%d")
    params: Dict[str, Any] = {
        "isPagination": "true",
        "pageHelp.pageSize": page_size,
        "pageHelp.pageNo": page_no,
        "pageHelp.beginPage": page_no,
        "pageHelp.cacheSize": 1,
        "pageHelp.endPage": page_no,
        "beginDate": ymd,
        "endDate": ymd,
        "sqlId": sql_id,
        "jsonCallBack": JSONP_CALLBACK,
    }
    # Summary uses stockCode; detail uses preStockCode (both empty = all).
    if sql_id == SUMMARY_SQL_ID:
        params["stockCode"] = ""
    else:
        params["preStockCode"] = ""

    resp = proxy.get(
        session,
        SSE_QUERY_URL,
        params=params,
        headers=SSE_HEADERS,
        timeout=DEFAULT_TIMEOUT,
        logger=logger,
        log_tag=f"[{sql_id} {ymd} p{page_no}]",
    )
    if resp is None:
        return None
    return _parse_jsonp(resp.text)


def _num(val: Any) -> Any:
    """Render a numeric cell: blank for None/NaN, otherwise the raw number."""
    if val is None:
        return ""
    try:
        f = float(val)
        return f if f == f else ""  # NaN check
    except (TypeError, ValueError):
        return ""


def _parse_summary_row(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "信用交易日期": item.get("opDate", ""),
        "融资余额(元)": _num(item.get("rzye")),
        "融资买入额(元)": _num(item.get("rzmre")),
        "融资偿还额(元)": _num(item.get("rzche")),
        "融券余量": _num(item.get("rqyl")),
        "融券余量金额(元)": _num(item.get("rqylje")),
        "融券卖出量": _num(item.get("rqmcl")),
        "融资融券余额(元)": _num(item.get("rzrqjyzl")),
    }


def _parse_detail_row(item: Dict[str, Any]) -> Dict[str, Any]:
    code = str(item.get("stockCode", "")).strip()
    # SSE-listed securities — add .SS suffix per project convention.
    if code and "." not in code:
        code = f"{code}.SS"
    return {
        "信用交易日期": item.get("opDate", ""),
        "证券代码": code,
        "证券简称": str(item.get("securityAbbr", "")).strip(),
        "融资余额(元)": _num(item.get("rzye")),
        "融资买入额(元)": _num(item.get("rzmre")),
        "融资偿还额(元)": _num(item.get("rzche")),
        "融券余量(股/份)": _num(item.get("rqyl")),
        "融券卖出量(股/份)": _num(item.get("rqmcl")),
        "融券偿还量(股/份)": _num(item.get("rqchl")),
    }


def _write_csv(out_file: Path, rows: List[Dict[str, Any]], columns: List[str]) -> None:
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding=CSV_ENCODING, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def _download_summary(
    session: requests.Session,
    trade_date: date,
    out_dir: Path,
    proxy: AntiBotProxy,
    sleep_sec: float,
) -> Tuple[Optional[Path], int]:
    """Fetch the single-row market-wide summary for one trade date.

    Returns (path_or_None, n_rows). n_rows == -1 indicates a cache hit.
    """
    ymd = trade_date.strftime("%Y%m%d")
    out_file = out_dir / f"sse_margin_summary_{ymd}.csv"
    if _csv_cache_hit(out_file, trade_date, "summary"):
        logger.debug("[summary %s] already exists, skipping", ymd)
        return out_file, -1

    payload = _fetch_page(
        session, SUMMARY_SQL_ID, trade_date, 1, SUMMARY_PAGE_SIZE, proxy,
    )
    if payload is None:
        logger.error("[summary %s] request failed", ymd)
        return None, 0

    rows_raw = payload.get("result") or []
    if not rows_raw:
        # API confirmed no data for this date — write a header-only CSV so
        # the next run's cache check skips it. Recent markers are retried
        # by _csv_cache_hit in case the export simply wasn't published yet.
        logger.info("[summary %s] no data, writing empty CSV", ymd)
        _write_csv(out_file, [], SUMMARY_COLUMNS)
        return out_file, 0

    parsed = [_parse_summary_row(r) for r in rows_raw]
    _write_csv(out_file, parsed, SUMMARY_COLUMNS)
    logger.info("[summary %s] saved %d row(s) -> %s", ymd, len(parsed), out_file.name)
    # Auto-sleep handled by proxy.get()/post()
    return out_file, len(parsed)


def _download_detail(
    session: requests.Session,
    trade_date: date,
    out_dir: Path,
    proxy: AntiBotProxy,
    sleep_sec: float,
) -> Tuple[Optional[Path], int]:
    """Fetch the per-security detail for one trade date, paginating to the end.

    Returns (path_or_None, n_rows). n_rows == -1 indicates a cache hit.
    """
    ymd = trade_date.strftime("%Y%m%d")
    out_file = out_dir / f"sse_margin_detail_{ymd}.csv"
    if _csv_cache_hit(out_file, trade_date, "detail"):
        logger.debug("[detail %s] already exists, skipping", ymd)
        return out_file, -1

    all_rows: List[Dict[str, Any]] = []
    page_no = 1
    total: Optional[int] = None
    # True when page 1 returned a valid payload with zero rows — i.e. the API
    # confirmed there is no data for this date (as opposed to a transient
    # request failure that left all_rows empty).
    api_confirmed_empty = False

    while True:
        if proxy.is_blocked(SSE_QUERY_URL):
            logger.warning("[detail %s] host blocked, stopping pagination", ymd)
            break

        payload = _fetch_page(
            session, DETAIL_SQL_ID, trade_date, page_no, DETAIL_PAGE_SIZE, proxy,
        )
        if payload is None:
            logger.error("[detail %s] page %d failed", ymd, page_no)
            break

        rows_raw = payload.get("result") or []
        if not rows_raw:
            logger.info("[detail %s] page %d returned no rows, stopping", ymd, page_no)
            if page_no == 1:
                api_confirmed_empty = True
            break

        all_rows.extend(_parse_detail_row(r) for r in rows_raw)

        if total is None:
            page_help = payload.get("pageHelp") or {}
            total = int(page_help.get("total", 0))
            logger.info("[detail %s] total=%d, paginating", ymd, total)

        if total and len(all_rows) >= total:
            break

        page_no += 1
        # Auto-sleep handled by proxy.get()/post()

    if not all_rows:
        if api_confirmed_empty:
            # API confirmed no data for this date — write a header-only CSV
            # so the next run's cache check skips it. Recent markers are
            # retried by _csv_cache_hit in case the export simply wasn't
            # published yet.
            logger.info("[detail %s] no data, writing empty CSV", ymd)
            _write_csv(out_file, [], DETAIL_COLUMNS)
            return out_file, 0
        logger.info("[detail %s] no data (transient failure)", ymd)
        return None, 0

    _write_csv(out_file, all_rows, DETAIL_COLUMNS)
    # canonicalize 证券代码 -> "NNNNNN.SS" + exchange/board/sec_type columns
    # (margin detail mixes stocks and ETFs -> per-row "auto" inference)
    from downloads._common import ensure_canonical_csv
    ensure_canonical_csv(
        out_file, "SS", sec_type="auto",
        logger=logger, log_tag="[detail %s] " % ymd,
    )
    logger.info(
        "[detail %s] saved %d row(s) across %d page(s) -> %s",
        ymd, len(all_rows), page_no, out_file.name,
    )
    return out_file, len(all_rows)


def _csv_has_data_rows(path: Path) -> bool:
    """Check whether a written CSV has at least one data row (i.e. is not a
    header-only/0-byte "no data" marker)."""
    try:
        with open(path, encoding=CSV_ENCODING, newline="") as f:
            reader = csv.reader(f)
            next(reader, None)  # header
            for row in reader:
                if any(cell.strip() for cell in row):
                    return True
    except (OSError, UnicodeDecodeError, csv.Error):
        return False
    return False


def _csv_cache_hit(out_file: Path, trade_date: date, log_tag: str) -> bool:
    """Return True when ``out_file`` already settles this trade date.

    A file with data rows is a plain cache hit. A header-only/0-byte file
    is an empty marker: recent markers (written within the last
    ``EMPTY_MARKER_RETRY_DAYS`` calendar days) are deleted and retried —
    a "no data" response on a recent trading day usually just means the
    exchange had not published that day's margin export yet when we first
    asked — while stale markers are treated as confirmed no-data dates.
    """
    if not out_file.exists():
        return False
    if _csv_has_data_rows(out_file):
        return True
    marker_age = (date.today() - trade_date).days
    if marker_age > EMPTY_MARKER_RETRY_DAYS:
        return True
    logger.info(
        "[%s %s] empty file is recent (%dd old), retrying (data may not "
        "have been published yet)", log_tag,
        trade_date.strftime("%Y%m%d"), marker_age,
    )
    out_file.unlink(missing_ok=True)
    return False


def download_sse_margin(
    out_root: Optional[str] = None,
    end_date: Optional[str] = None,
    start_date: str = DEFAULT_START_DATE,
    report_types: Optional[List[str]] = None,
    sleep_sec: float = 5.0,
    session: Optional[requests.Session] = None,
) -> dict:
    """Download SSE margin (融资融券) data day by day.

    For each trading day in ``[start_date, end_date]`` fetches both the
    market-wide summary (1 row) and the per-security detail (~1900 rows,
    paginated). Skips dates where the output CSV already exists.

    Margin exports for trade date T are published by the exchange on T+1
    (daytime), so the newest date or two may legitimately have no data yet
    when this runs late at night or early morning. Those fetches write
    empty CSVs that ``_csv_cache_hit`` retries on subsequent runs, so any
    run after publication picks the dates up automatically (weekends
    included).

    Args:
        out_root: Override output root dir (default: ``temps/sse_margin``).
        end_date: ``YYYY-MM-DD`` inclusive (default: the most recent
            trading day; not-yet-published dates are retried on later runs).
        start_date: ``YYYY-MM-DD`` inclusive (default: ``DEFAULT_START_DATE``).
        report_types: Subset of ``["summary", "detail"]`` (default: both).
        sleep_sec: Sleep between requests for anti-bot throttling.
        session: Optional ``requests.Session`` to reuse.

    Returns:
        Dict with per-report download/skip/fail counts and the output dir.
    """
    out_dir = resolve_out_dir(str(Path(__file__).resolve()), "sse_margin", out_root)
    sess = session or requests.Session()
    
    # Create unified AntiBotProxy
    proxy_config = AntiBotConfig(base_sleep_sec=sleep_sec)
    proxy = AntiBotProxy(proxy_config)

    if end_date is None:
        # Walk all the way to the most recent trading day; dates whose
        # export is not published yet produce empty CSVs that later runs
        # retry (see _csv_cache_hit).
        effective_end_date = last_business_day(date.today())
    else:
        effective_end_date = datetime.strptime(end_date, "%Y-%m-%d").date()

    _start = datetime.strptime(start_date, "%Y-%m-%d").date()

    if report_types is None:
        report_types = ["summary", "detail"]

    days = business_days(_start, effective_end_date, reverse=True)
    total_days = len(days)

    logger.info(
        "Starting SSE margin download: %s -> %s (%d trading days). types=%s",
        _start, effective_end_date, total_days, report_types,
    )

    stats: Dict[str, Any] = {
        "downloaded_summary": 0, "downloaded_detail": 0,
        "skipped_summary": 0, "skipped_detail": 0,
        "failed_summary": 0, "failed_detail": 0,
        "empty_summary": 0, "empty_detail": 0,
        "out_dir": str(out_dir),
        "start_date": str(_start),
        "end_date": str(effective_end_date),
    }

    # Track consecutive cache-skip dates per report type. Instead of logging
    # each skipped file individually, accumulate a streak and emit a single
    # consolidated line when an actual download (or failure) interrupts the
    # run, or once at loop end.
    skip_streak: Dict[str, List[date]] = {rt: [] for rt in report_types}

    def _flush_skip_streak() -> None:
        parts: List[str] = []
        total_files = 0
        for rt in report_types:
            dates = skip_streak[rt]
            if not dates:
                continue
            # days are iterated newest-first, so dates[0] is the newest
            newest = dates[0].strftime("%Y%m%d")
            oldest = dates[-1].strftime("%Y%m%d")
            n = len(dates)
            total_files += n
            rng = newest if newest == oldest else f"{newest}->{oldest}"
            parts.append(f"{rt} {rng} ({n})")
            dates.clear()
        if parts:
            logger.info(
                "[cache] skipped %d file(s): %s",
                total_files, ", ".join(parts),
            )

    try:
        for d in days:
            if proxy.is_blocked(SSE_QUERY_URL):
                logger.warning("[host-blocked] query.sse.com.cn is blocked, stopping")
                break

            if "summary" in report_types:
                path, n = _download_summary(sess, d, out_dir, proxy, sleep_sec)
                if n == -1:
                    skip_streak["summary"].append(d)
                    stats["skipped_summary"] += 1
                else:
                    _flush_skip_streak()
                    if path is None:
                        stats["failed_summary"] += 1
                    elif n == 0:
                        stats["empty_summary"] += 1
                    else:
                        stats["downloaded_summary"] += 1

            if "detail" in report_types:
                path, n = _download_detail(sess, d, out_dir, proxy, sleep_sec)
                if n == -1:
                    skip_streak["detail"].append(d)
                    stats["skipped_detail"] += 1
                else:
                    _flush_skip_streak()
                    if path is None:
                        stats["failed_detail"] += 1
                    elif n == 0:
                        stats["empty_detail"] += 1
                    else:
                        stats["downloaded_detail"] += 1
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")

    _flush_skip_streak()

    logger.info(
        "Done SSE margin. summary: dl=%d skip=%d fail=%d empty=%d | detail: dl=%d skip=%d fail=%d empty=%d out=%s",
        stats["downloaded_summary"], stats["skipped_summary"], stats["failed_summary"], stats["empty_summary"],
        stats["downloaded_detail"], stats["skipped_detail"], stats["failed_detail"], stats["empty_detail"],
        out_dir,
    )
    return stats


if __name__ == "__main__":
    try:
        with single_instance_lock("downloads.margin.sse"):
            logger.info(download_sse_margin())
    except RuntimeError as exc:
        logger.warning("%s — exiting", exc)
