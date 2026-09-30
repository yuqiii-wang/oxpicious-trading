"""BJS (Beijing Stock Exchange) downloader + BSE CSV format conventions.

The BSE quotation endpoint (``https://www.bse.cn/nqhqController/nqhq_en.do``)
is the JSONP table behind https://www.bse.cn/nq/quotation.html (the quotes
grid on the BSE site). It serves offset pagination capped at 20 rows/page.

CRITICAL filters: the endpoint serves the whole BSE-group quotation
universe (~5,800 securities = 新三板/NEEQ) unless restricted exactly the
way the quotation page's own JS does (companies_in_name.min.js):
``type_en='["B"]'`` (BSE tab) + ``xxfcbj_en='[2]'`` (listed flag). With
them it returns only the ~348 BSE-listed stocks (920xxx series). The sort
params are ``sortfield``/``sorttype`` — the server silently IGNORES
``sortColumn``/``sortType`` (different names), which used to leave
pagination on the server's volatile default order and produced duplicate
rows; always sort by ``hqzqdm`` for a stable page walk.

EOD rows are appended into ``temps/bse_trend/bse_trend_stock_YYYYMMDD.csv``
using the unified trend schema (same columns as szse_trend_stock /
sse_trend_stock), with codes zero-padded and suffixed ``.BJ``.
"""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from downloads._common.filescan import (
    MIN_VALID_BYTES,
    is_valid_file,
    resolve_out_dir,
)
from downloads._common.net import (
    DEFAULT_TIMEOUT,
    AntiBotConfig,
    AntiBotProxy,
    build_headers_with_referer,
    setup_logger,
)
from downloads._common.io_csv import ensure_canonical_csv


BSE_LIST_URL = "https://www.bse.cn/nqhqController/nqhq_en.do"
BSE_REFERER = "https://www.bse.cn/nq/quotation.html"

# BSE's server caps pageSize at 20 regardless of the requested value (the
# quotation page's own JS sends no pageSize at all), so this is the
# effective page size and the page count is ceil(totalElements / 20).
PAGE_SIZE = 20

# The quotation page's own request (companies_in_name.min.js): type_en
# restricts to the BSE tab (without it the endpoint returns the whole
# ~5,800-security NEEQ universe), xxfcbj_en to listed securities, and
# sortfield/sorttype (NOT sortColumn/sortType — silently ignored) give the
# stable code order pagination depends on.
BSE_TAB_FORM_DATA = {
    "type_en": '["B"]',
    "xxfcbj_en": "[2]",
    "sortfield": "hqzqdm",
    "sorttype": "asc",
}

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

# --- Individual-item historical K-line (公司行情页图表接口) --------------------
# Clicking a stock row on the quotation page opens the company detail page
# ``/products/neeq_listed_companies/company_time_sharing.html?companyCode=...``
# (the URL built from the page's hidden ``#sshq`` input in
# companies_in_name.min.js). That page's chart JS (company_time_share.min.js)
# loads its 日K/周K/月K series from
# ``GET apiHost + "/companyEchartsController/getKLine/list/{code}.do"`` where
# apiHost resolves to "" (main.min.js), with params ``begin``/``end``/``type``/
# ``xxfcbj``. ``begin=0&end=-1&type=dayKline`` returns the stock's FULL daily
# history since listing in one response. Record fields:
#   jsrq 结束日期 YYYYMMDD, jrkp 今开, jrsp 今收, zrsp 昨收,
#   drzd 当日最高, drzx 当日最低, cjl 成交量(股), cjje 成交额(元)
BSE_KLINE_URL = "https://www.bse.cn/companyEchartsController/getKLine/list/{code}.do"
BSE_KLINE_REFERER = (
    "https://www.bse.cn/products/neeq_listed_companies/company_time_sharing.html"
)
BSE_KLINE_HEADERS = build_headers_with_referer(
    BSE_KLINE_REFERER,
    extra={"Accept": "*/*", "X-Requested-With": "XMLHttpRequest"},
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

    POSTs the quotation page's form fields (BSE-tab filters + stable code
    sort, see ``BSE_TAB_FORM_DATA``) plus ``page=<n>`` to the BSE list
    endpoint and returns the parsed JSON object (the first element of the
    response array), or None on failure.
    """
    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(rotate_browser_profile=False, base_sleep_sec=5.0))

    data = {"page": str(page), **BSE_TAB_FORM_DATA}
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

    Stock codes are zero-padded to 6 digits and suffixed with ``.BJ`` (the
    project convention for Beijing Stock Exchange codes, matching
    stock_identity.exchange).
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
        # lineterminator="\n": the csv module defaults to "\r\n", and cudf's
        # buffer-path parser (single-code byte-prefiltered reads) keeps the
        # trailing "\r" in the last column — the other exchanges' CSVs are
        # pandas-written ("\n") and never hit this.
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore",
                                lineterminator="\n")
        if write_header:
            writer.writeheader()
        for r in rows:
            writer.writerow(r)


def download_bse_price(
    out_root: Optional[str] = None,
    *,
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
    out_dir = resolve_out_dir(__file__, "bse_trend", out_root)
    sess = session or requests.Session()

    # Create unified AntiBotProxy with disabled browser rotation (BSE needs specific headers)
    proxy_config = AntiBotConfig(
        rotate_browser_profile=False,
        base_sleep_sec=sleep_sec,
    )
    proxy = AntiBotProxy(proxy_config)

    logger.info("Starting BSE price download (BSE tab, page size %d)", PAGE_SIZE)

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
    # canonicalize 证券代码 -> "NNNNNN.BJ" + exchange/board/sec_type columns
    # so build loaders can filter by plain column equality (no regex)
    if written:
        ensure_canonical_csv(out_file, "BJ", sec_type="stock", logger=logger)
    return {
        "downloaded": 1,
        "update_time": update_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "trade_date": date_str,
        "total": total,
        "rows": written,
        "pages": page_index,
        "out_file": str(out_file),
    }


# --- BSE indices (北证指数 — /market_data/bse_indices/bse_bz50.html) ----------
# The 北证50 page's chart JS (neeq_bz50.min.js, apiHost="" so all on
# www.bse.cn) serves two GET JSONP endpoints:
#   /neeqRealController/getBz50Kline.do?type=dayKline  — full daily history
#     since the index base date (records: jsrq YYYYMMDD, drkp 开盘, drzd 最高,
#     drzx 最低, drsp 收盘, zrsp 昨收, zd 涨跌(点), zdf 涨跌幅(RATIO — ×100
#     for percent), cjl 成交量(股), cjje 成交额(元), zsdm/jc 代码/简称). The
#     LAST record is TODAY's live row during the session (final after close).
#   /neeqRealController/getRealBZ50.do — per-minute intraday series for today
#     (records: GXSJ HHMMSS, JSRQ YYYY-MM-DD, SSZS 实时指数, ZRSP 昨收,
#     ZD/ZDF 涨跌, CJL/CJJE per-MINUTE volume(股)/amount(元) — per-minute, NOT
#     cumulative). One call returns the whole session so far.
BSE_INDEX_KLINE_URL = "https://www.bse.cn/neeqRealController/getBz50Kline.do"
BSE_INDEX_REALTIME_URL = "https://www.bse.cn/neeqRealController/getRealBZ50.do"
BSE_INDEX_REFERER = "https://www.bse.cn/market_data/bse_indices/bse_bz50.html"
BSE_INDEX_HEADERS = build_headers_with_referer(
    BSE_INDEX_REFERER,
    extra={"Accept": "*/*", "X-Requested-With": "XMLHttpRequest"},
)

# BSE-published indices this repo tracks (code -> Chinese name; bare 6-digit
# codes per stats.index_identity CHECK). 899050 北证50 is the flagship BSE
# index; the 三板-series (899001 等) stay on csindex.
BSE_INDEX_CODES: Dict[str, str] = {"899050": "北证50"}


def _fetch_index_payload(
    session: requests.Session,
    url: str,
    params: Optional[Dict[str, str]] = None,
    proxy: Optional[AntiBotProxy] = None,
    timeout: Tuple[int, int] = DEFAULT_TIMEOUT,
) -> Optional[List[Any]]:
    """GET one BSE index endpoint and return its parsed JSONP record list."""
    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(rotate_browser_profile=False, base_sleep_sec=3.0))
    r = proxy.get(session, url, params=params, headers=BSE_INDEX_HEADERS,
                  timeout=timeout, logger=logger, log_tag="[bse-index] ")
    if r is None:
        return None
    try:
        payload = _parse_jsonp(r.text)
    except (ValueError, json.JSONDecodeError) as e:
        logger.error("BSE index payload parse failed (%s): %s", url, e)
        return None
    if not isinstance(payload, list):
        logger.error("BSE index payload not a list (%s): %s", url, str(payload)[:120])
        return None
    return payload


def fetch_bse_index_kline(
    session: requests.Session,
    proxy: Optional[AntiBotProxy] = None,
    kline_type: str = "dayKline",
    timeout: Tuple[int, int] = DEFAULT_TIMEOUT,
) -> Optional[List[Dict[str, Any]]]:
    """Fetch the full daily K-line history of the BSE index (899050).

    Returns the raw record list (oldest → newest; the last record is today's
    live row during the session), or None on failure.
    """
    return _fetch_index_payload(
        session, BSE_INDEX_KLINE_URL, {"type": kline_type},
        proxy=proxy, timeout=timeout,
    )


def fetch_bse_index_realtime(
    session: requests.Session,
    proxy: Optional[AntiBotProxy] = None,
    timeout: Tuple[int, int] = DEFAULT_TIMEOUT,
) -> Optional[List[Dict[str, Any]]]:
    """Fetch today's per-minute intraday series of the BSE index (899050).

    Returns the raw record list (one record per minute since open), or None
    on failure.
    """
    return _fetch_index_payload(
        session, BSE_INDEX_REALTIME_URL, {}, proxy=proxy, timeout=timeout,
    )


def bse_index_kline_rows(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Map getBz50Kline records to the CSIndex history-CSV row schema.

    Units pass through as published — trading_shares in SHARES, trading_amount
    in YUAN (the cnindex archive loader's convention); ``changePct`` stores
    percent (the API's zdf is a ratio, ×100).
    """
    rows: List[Dict[str, Any]] = []
    for rec in records or []:
        jsrq = str(rec.get("jsrq") or "").strip()
        if len(jsrq) != 8 or not jsrq.isdigit():
            continue
        date_iso = f"{jsrq[:4]}-{jsrq[4:6]}-{jsrq[6:8]}"
        zdf = _num(rec.get("zdf"))
        rows.append({
            "date": date_iso,
            "indexCode": str(rec.get("zsdm") or "").strip() or "899050",
            "indexName": str(rec.get("jc") or "").strip() or "北证50",
            "open": _num(rec.get("drkp")),
            "high": _num(rec.get("drzd")),
            "low": _num(rec.get("drzx")),
            "close": _num(rec.get("drsp")),
            "trading_shares": _num(rec.get("cjl")),
            "trading_amount": _num(rec.get("cjje")),
            "change": _num(rec.get("zd")),
            "changePct": round(zdf * 100, 4) if zdf is not None else None,
            "pe": None,
            "consNumber": None,
        })
    return rows


def fetch_bse_snapshot(
    session: requests.Session,
    proxy: Optional[AntiBotProxy] = None,
    timeout: Tuple[int, int] = DEFAULT_TIMEOUT,
) -> Tuple[Optional[datetime], Dict[str, Dict[str, Any]]]:
    """Fetch one full BSE-listed snapshot for streaming (all pages).

    Returns ``(update_dt, {bare_6digit_code: record})`` or ``(None, {})``.
    ``update_dt`` comes from the first record's ``hqjsrq``/``hqgxsj`` (the
    exchange's own update stamp, not the local clock). Records mirror the
    SSE stream schema — ``code``/``name``/``open``/``high``/``low``/
    ``last``/``prev_close``/``change``/``volume``/``amount``/``pe`` — with
    volume in SHARES and amount in YUAN as returned by the endpoint (raw
    units, same convention as the SSE intraday CSV archive). ``change`` is
    derived as ``last - prev_close`` (the endpoint publishes only the pct
    change, ``hqzdf``).
    """
    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(rotate_browser_profile=False, base_sleep_sec=1.5))

    first = _fetch_page(session, 0, timeout=timeout, proxy=proxy)
    if first is None:
        return None, {}
    update_dt = _extract_update_datetime(first)
    if update_dt is None:
        return None, {}

    def _rec(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        code = str(row.get("hqzqdm") or "").strip()
        if not code:
            return None
        last = _num(row.get("hqzjcj"))
        prev_close = _num(row.get("hqzrsp"))
        change = round(last - prev_close, 4) if last and prev_close else None
        return {
            "code": code.zfill(6),
            "name": str(row.get("hqzqjc") or "").strip(),
            "open": _num(row.get("hqjrkp")),
            "high": _num(row.get("hqzgcj")),
            "low": _num(row.get("hqzdcj")),
            "last": last,
            "prev_close": prev_close,
            "change": change,
            "volume": _num(row.get("hqcjsl")),
            "amount": _num(row.get("hqcjje")),
            "pe": _num(row.get("hqsyl1")),
        }

    snapshot: Dict[str, Dict[str, Any]] = {}
    for row in first.get("content", []) or []:
        rec = _rec(row)
        if rec:
            snapshot[rec["code"]] = rec

    total = int(first.get("totalElements", 0))
    total_pages = int(first.get("totalPages", 0))
    page_index = 1
    while len(snapshot) < total and (total_pages == 0 or page_index < total_pages):
        if proxy.is_blocked(BSE_LIST_URL):
            logger.warning("host blocked mid-sweep, using partial snapshot (%d codes)",
                           len(snapshot))
            break
        payload = _fetch_page(session, page_index, timeout=timeout, proxy=proxy)
        if payload is None:
            logger.warning("page %d failed; using partial snapshot (%d codes)",
                           page_index, len(snapshot))
            break
        page_rows = payload.get("content", []) or []
        if not page_rows:
            break
        for row in page_rows:
            rec = _rec(row)
            if rec:
                snapshot[rec["code"]] = rec
        page_index += 1

    return update_dt, snapshot


def fetch_bse_stock_list(
    session: requests.Session,
    *,
    proxy: Optional[AntiBotProxy] = None,
    timeout: Tuple[int, int] = DEFAULT_TIMEOUT,
) -> List[Tuple[str, str]]:
    """Walk the BSE quotation pagination and return ``[(bare_code, name), ...]``.

    Same endpoint/filters as ``download_bse_price`` (BSE tab + listed flag,
    stable ``hqzqdm`` sort), so the returned universe matches the daily
    ``bse_trend_stock_<date>.csv`` snapshots. ~348 codes / ~18 pages.
    """
    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(rotate_browser_profile=False, base_sleep_sec=5.0))

    stocks: List[Tuple[str, str]] = []
    first = _fetch_page(session, 0, timeout=timeout, proxy=proxy)
    if first is None:
        logger.error("Failed to fetch first BSE list page")
        return stocks

    def _extend(content: List[Dict[str, Any]]) -> int:
        added = 0
        for row in content:
            code_raw = str(row.get("hqzqdm") or "").strip()
            if not code_raw:
                continue
            stocks.append((code_raw.zfill(6), str(row.get("hqzqjc") or "").strip()))
            added += 1
        return added

    total = int(first.get("totalElements", 0))
    total_pages = int(first.get("totalPages", 0))
    written = _extend(first.get("content", []) or [])
    logger.info("BSE stock list: total=%d page0 rows=%d", total, written)

    page_index = 1
    while written < total and (total_pages == 0 or page_index < total_pages):
        if proxy.is_blocked(BSE_LIST_URL):
            logger.warning("[host-blocked] bse.cn blocked, stopping list pagination")
            break
        payload = _fetch_page(session, page_index, timeout=timeout, proxy=proxy)
        if payload is None:
            logger.error("list page %d failed", page_index)
            break
        page_rows = payload.get("content", []) or []
        if not page_rows:
            logger.info("list page %d returned no rows, stopping", page_index)
            break
        written += _extend(page_rows)
        page_index += 1

    logger.info("Done BSE stock list: collected %d codes", len(stocks))
    return stocks


def fetch_bse_kline(
    session: requests.Session,
    code: str,
    *,
    proxy: Optional[AntiBotProxy] = None,
    timeout: Tuple[int, int] = DEFAULT_TIMEOUT,
) -> Optional[List[Dict[str, Any]]]:
    """Fetch one stock's FULL daily K-line history (the 日K series behind the
    company detail page's chart).

    ``begin=0&end=-1`` makes the endpoint return every daily bar since
    listing in a single response. Returns the raw ``data`` record list, or
    None on request/parse failure or when the endpoint reports an error
    (missing ``data`` key / non-zero ``status``). An empty list means no
    published history for the code.

    bse.cn fronts the endpoint with a WAF cookie challenge (302 + Set-Cookie
    ``C3VK``, ``Ws-Action: cc``); the shared ``requests.Session`` passes it
    transparently by following the redirect.
    """
    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(rotate_browser_profile=False))

    resp = proxy.get(
        session,
        BSE_KLINE_URL.format(code=code),
        params={"begin": 0, "end": -1, "type": "dayKline", "xxfcbj": 2},
        headers=BSE_KLINE_HEADERS,
        timeout=timeout,
        logger=logger,
        log_tag=f"[kline {code}] ",
    )
    if resp is None:
        return None
    try:
        payload = _parse_jsonp(resp.text)
    except (ValueError, json.JSONDecodeError) as e:
        logger.error("[kline %s] failed to parse response: %s", code, e)
        return None
    if not isinstance(payload, dict) or "data" not in payload:
        logger.error("[kline %s] unexpected payload shape: %s",
                     code, str(payload)[:200])
        return None
    if payload.get("status") not in (0, "0"):
        logger.error("[kline %s] endpoint error status=%r msg=%r",
                     code, payload.get("status"), payload.get("msg"))
        return None
    return payload.get("data") or []


def parse_bse_kline_row(
    rec: Dict[str, Any], code: str, name: str,
) -> Optional[Dict[str, Any]]:
    """Map one kline record (jsrq/jrkp/jrsp/zrsp/drzd/drzx/cjl/cjje) to the
    unified trend schema (``COLUMNS``). Returns None when ``jsrq`` is not a
    parseable YYYYMMDD date. ``市盈率`` is left blank — the kline endpoint
    does not publish it (the daily quotation snapshot carries it instead).
    """
    trade_date = _parse_trade_date(rec.get("jsrq"))
    if trade_date is None:
        return None

    prev_close = _num(rec.get("zrsp"))
    close = _num(rec.get("jrsp"))
    # 涨跌幅（%）= (今收 - 昨收) / 昨收 * 100, blank when 昨收 is missing/0
    pct: Any = ""
    if prev_close and close is not None:
        pct = round((close - prev_close) / prev_close * 100, 2)

    # cjl is in shares and cjje in yuan; convert to 万股 / 万元 to match the
    # unified schema (same conversion as _parse_row).
    vol_wan: Any = ""
    volume = _num(rec.get("cjl"))
    if volume is not None:
        vol_wan = round(volume / 10000, 2)
    amt_wan: Any = ""
    amount = _num(rec.get("cjje"))
    if amount is not None:
        amt_wan = round(amount / 10000, 2)

    return {
        "交易日期": trade_date,
        "证券代码": code,
        "证券简称": name,
        "前收": _fmt_num(prev_close),
        "开盘": _fmt_num(_num(rec.get("jrkp"))),
        "最高": _fmt_num(_num(rec.get("drzd"))),
        "最低": _fmt_num(_num(rec.get("drzx"))),
        "今收": _fmt_num(close),
        "涨跌幅（%）": pct,
        "成交量(万股)": vol_wan,
        "成交金额(万元)": amt_wan,
        "市盈率": "",
    }
