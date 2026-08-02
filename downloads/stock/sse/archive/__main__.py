"""Download Shanghai Stock Exchange (SSE) HISTORICAL daily price + PE data.

This module is the HISTORY/archive half of the former ``download_sse_price``.
It downloads full OHLCV history via the dayk endpoint and PE (静态市盈率 +
总换手率) via the 成交概况 endpoint. For TODAY's snapshot (latest biz date),
use ``download_sse_trend.py`` instead — it fetches the SSE list endpoint
(the same data source as ``stream_sse_price.py``).

Studies ``https://www.sse.com.cn/market/price/trends/`` and downloads ALL
historical daily data for every stock listed on the Shanghai exchange.

The page is JS-rendered; the underlying data is served as JSONP by:
1. ``https://yunhq.sse.com.cn:32042/v1/sh1/list/exchange/equity`` — stock list
   (used only when ``--no-etf-filter`` is passed; default loads targets from DB)
2. ``https://yunhq.sse.com.cn:32042/v1/sh1/dayk/{code}`` — historical daily K-line

Uses offset-based pagination (``begin`` / ``end``) and a ``total`` record count
for the list endpoint. For historical K-line data, uses ``begin=-10000`` to fetch
the last 10000 trading days per stock.

CRITICAL: The filename and 交易日期 column are derived strictly from the
API's date field in the K-line data, never from ``datetime.now()``.

Two outputs are produced per stock under ``temps/sse_archive/``:
  1. ``{code}_trend.csv`` — full OHLCV daily price history fetched from the
     dayk endpoint (yunhq.sse.com.cn), one row per trading day. Always
     (re)written when the stock's dayk data is fetched, so the archive
     reflects the latest API response.
  2. ``{code}_pe.csv`` — 静态市盈率(倍) (PE_RATE) + 总换手率(%) (TO_RATE)
     snapshots fetched from the SSE 成交概况 endpoint.

Output columns (empty when the field is not published by SSE):
    交易日期,证券代码,证券简称,前收,开盘,最高,最低,今收,
    涨跌幅（%）,成交量(万股),成交金额(万元),市盈率

Trading statistics (成交统计) + OHLCV trend
--------------------------------------------
Per-stock workflow (trend → PE → next stock):
  For each stock, the trend is fetched first (cached or API), then PE is
  requested immediately for that stock before moving on to the next.

  Trend: fetch the full OHLCV daily history from the dayk endpoint
    (yunhq.sse.com.cn) and write ``{code}_trend.csv``.

  PE: compute PE request dates and fetch 静态市盈率(倍) (PE_RATE) +
    总换手率(%) (TO_RATE) from the SSE 成交概况 endpoint, writing
    ``{code}_pe.csv``.

    PE request dates are the union of:
    (a) Quarterly snapshots — first trading day of Feb, Apr, May, Aug,
        Sep, Nov each year, spanning the stock's full history.
    (b) Big-jump dates — days where the close-to-close return exceeds
        ±7% (both big drops AND big rises); for each jump, PE is
        requested for both the previous day and the jump day. After each
        jump-triggered request, a 7-day cooldown suppresses subsequent
        jumps.

    The latest trading day is NOT automatically fetched — PE for today is
    only requested when today happens to be a quarterly snapshot date or a
    big-jump date (same criteria as any historical date). The daily price
    (trend) is always downloaded regardless.

PE APIs use a fallback/alternation strategy (per user request):
  - Primary: ``queryNewAllQuatAbel.do`` (FUNDID/inMonth/inYear/searchDate) —
    has long historical coverage (back to 2020 and earlier).
  - Alternative: ``commonQuery.do`` with
    ``sqlId=COMMON_SSE_CP_GPJCTPZ_GPLB_CJGK_MRGK_C`` (SEC_CODE/TX_DATE),
    studied from the "成交统计" section of the SSE company page
    ``.../company/index.shtml?COMPANY_CODE=600030`` — covers only recent
    dates, so it complements the primary API.
  The primary is tried first; on failure the alternative is tried. Whichever
  API last succeeded becomes the preferred API for subsequent dates — once
  the alternative starts working it is kept until it fails, at which point
  the primary is retried. Only when both APIs fail for a date is it marked
  as "no data".

Resumable: existing ``{code}_trend.csv`` and ``{code}_pe.csv`` are read
to skip already-fetched data. ``--force`` re-fetches everything.
"""

from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from downloads._common.core import (
    DEFAULT_TIMEOUT,
    DEFAULT_SLEEP_SEC,
    DEFAULT_START_DATE,
    MIN_VALID_BYTES,
    DEFAULT_SHORT_SLEEP_SEC,
    AntiBotProxy,
    AntiBotConfig,
    build_headers_with_referer,
    date_range_backward,
    date_range_forward,
    is_trading_day,
    is_valid_file,
    last_business_day,
    next_business_day,
    resolve_out_dir,
    setup_logger,
)
from downloads.stock.sse._common.list_endpoint import (
    COLUMNS,
    CSV_ENCODING,
    JSONP_CALLBACK,
    LIST_SELECT_FIELDS,
    PAGE_SIZE,
    SSE_HEADERS,
    SSE_LIST_URL,
    SSE_REFERER,
    _RE_JSONP,
    _fmt_num,
    _num,
    _parse_jsonp,
    _write_rows,
)
from utils.db_commons import get_db_connection


# --- History (dayk) constants ----------------------------------------------
SSE_DAYK_URL = "https://yunhq.sse.com.cn:32042/v1/sh1/dayk/{code}"
# Default start date for trend data (imported from _download_commons).
# The dayk endpoint returns the full listing history (some SSE stocks date
# back to 1999); by default we only keep data from 2020-01-01 onwards.
# Field tokens for dayk endpoint
DAYK_SELECT_FIELDS = "date,open,high,low,close,volume,amount,prev_close"
DAYK_MAX_DAYS = 10000  # Fetch up to 10000 trading days per stock

# Per-stock archive directory: one CSV per stock holding its full fetched
# history. Lives under temps/sse_archive/, sibling of the date-grouped
# temps/sse_trend/ output.
ARCHIVE_DIRNAME = "sse_archive"

# --- Trading statistics (成交统计) constants --------------------------------
# Primary endpoint: queryNewAllQuatAbel.do (FUNDID/inMonth/inYear/searchDate
# params). Serves the "成交统计" section of the SSE turnover page and has
# long historical coverage (back to 2020 and earlier).
SSE_TRADE_STATS_URL = "http://query.sse.com.cn/security/fund/queryNewAllQuatAbel.do"
# Alternative (fallback) endpoint: commonQuery.do with
# sqlId=COMMON_SSE_CP_GPJCTPZ_GPLB_CJGK_MRGK_C, studied from the "成交统计"
# section of the SSE company page
# ``.../company/index.shtml?COMPANY_CODE=600000`` (loaded by
# ``search_stocksDepositoryReceipts_2021.js``). Uses SEC_CODE/TX_DATE params
# and tends to cover only recent dates, so it complements the primary API
# which has older data.
SSE_TRADE_STATS_ALT_URL = "http://query.sse.com.cn/commonQuery.do"
SSE_TRADE_STATS_ALT_SQL_ID = "COMMON_SSE_CP_GPJCTPZ_GPLB_CJGK_MRGK_C"
SSE_COMPANY_REFERER = (
    "http://www.sse.com.cn/assortment/stock/list/info/turnover/index.shtml"
)
SSE_TRADE_STATS_HEADERS = build_headers_with_referer(
    SSE_COMPANY_REFERER, extra={"Accept": "*/*"}
)
# Trade-stats output lives alongside the per-stock price archive under
# temps/sse_archive/. Each stock gets two files:
#   {code}_trend.csv    — full OHLCV price history (dayk)
#   {code}_pe.csv       — 静态市盈率(倍) + 总换手率(%) snapshots
TRADE_STATS_DIRNAME = ARCHIVE_DIRNAME  # "sse_archive"
# First trading day of these months is the quarterly snapshot date.
# User requested: Feb, May, Aug, Nov (quarterly) + Apr, Sep (extra).
QUARTERLY_MONTHS: Tuple[int, ...] = (2, 4, 5, 8, 9, 11)
# Smaller sleep than the dayk/list endpoint — query.sse.com.cn is a lightweight
# query API and does not require the 20s cadence used for yunhq.sse.com.cn.
TRADE_STATS_SLEEP_SEC = DEFAULT_SHORT_SLEEP_SEC
# PE file holds both metrics per the user's request.
PE_COLUMNS: List[str] = [
    "日期", "证券代码", "证券简称", "静态市盈率(倍)", "总换手率(%)",
]
# Jump detection: request PE for prev day + jump day when daily rise exceeds
# this threshold. After each jump-triggered PE request, skip subsequent jumps
# within JUMP_COOLDOWN_DAYS calendar days.
JUMP_THRESHOLD_PCT = 7.0
JUMP_COOLDOWN_DAYS = 7

logger = setup_logger("sse_download")


# ---------------------------------------------------------------------------
# SSE list-endpoint fetcher (stock list only; for today's snapshot see
# download_sse_trend.py which uses the full field set)
# ---------------------------------------------------------------------------
def _fetch_page(
    session: requests.Session,
    begin: int,
    end: int,
    proxy: AntiBotProxy,
) -> Optional[Dict[str, Any]]:
    """Fetch one page from the SSE list endpoint (code+name only)."""
    params = {
        "callback": JSONP_CALLBACK,
        "begin": str(begin),
        "end": str(end),
        "select": LIST_SELECT_FIELDS,
    }

    resp = proxy.get(
        session,
        SSE_LIST_URL,
        params=params,
        headers=SSE_HEADERS,
        timeout=DEFAULT_TIMEOUT,
        logger=logger,
        log_tag=f"[list-page {begin}-{end}]",
    )
    if resp is None:
        return None
    return _parse_jsonp(resp.text)


def _fetch_all_stocks(
    session: requests.Session,
    proxy: AntiBotProxy,
    page_size: int = PAGE_SIZE,
) -> List[Tuple[str, str]]:
    """Fetch all stock codes and names from the SSE list endpoint."""
    stocks: List[Tuple[str, str]] = []

    # First page: discover total record count
    first = _fetch_page(session, 0, page_size, proxy)
    if first is None:
        logger.error("Failed to fetch first SSE list page")
        return stocks

    total = int(first.get("total", 0))
    logger.info("SSE stock list: total=%d", total)

    # Parse first page
    for row in first.get("list", []):
        code = str(row[0]).strip() if row[0] is not None else ""
        name = str(row[1]).strip() if row[1] is not None else ""
        if code:
            stocks.append((code, name))

    # Subsequent pages
    page_index = 1
    while len(stocks) < total:
        if proxy.is_blocked(SSE_LIST_URL):
            logger.warning("  [host-blocked] sse.com.cn is blocked, stopping pagination")
            break

        begin = page_index * page_size
        end = begin + page_size

        proxy.sleep()

        payload = _fetch_page(session, begin, end, proxy)
        if payload is None:
            logger.error("list page %d (begin=%d) failed", page_index + 1, begin)
            break

        page_rows = payload.get("list", []) or []
        if not page_rows:
            logger.info("list page %d returned no rows, stopping", page_index + 1)
            break

        for row in page_rows:
            code = str(row[0]).strip() if row[0] is not None else ""
            name = str(row[1]).strip() if row[1] is not None else ""
            if code:
                stocks.append((code, name))

        logger.info(
            "list page %d: collected %d stocks (cumulative %d / %d)",
            page_index + 1, len(page_rows), len(stocks), total,
        )
        page_index += 1

    logger.info("Done fetching stock list: total collected=%d", len(stocks))
    return stocks


# ---------------------------------------------------------------------------
# dayk (historical K-line) fetcher + parser
# ---------------------------------------------------------------------------
def _fetch_dayk(
    session: requests.Session,
    code: str,
    proxy: AntiBotProxy,
) -> Optional[Dict[str, Any]]:
    """Fetch historical daily K-line data for a single stock."""
    url = SSE_DAYK_URL.format(code=code)
    params = {
        "callback": JSONP_CALLBACK,
        "begin": str(-DAYK_MAX_DAYS),
        "end": "-1",
        "period": "day",
        "select": DAYK_SELECT_FIELDS,
    }

    resp = proxy.get(
        session,
        url,
        params=params,
        headers=SSE_HEADERS,
        timeout=DEFAULT_TIMEOUT,
        logger=logger,
        log_tag=f"[dayk {code}]",
    )
    if resp is None:
        return None
    return _parse_jsonp(resp.text)


def _parse_dayk_row(row: List[Any], code: str, name: str) -> Optional[Dict[str, Any]]:
    """Map one SSE dayk row to the output column schema.

    Row order matches DAYK_SELECT_FIELDS:
    date, open, high, low, close, volume, amount, prev_close
    """
    if len(row) < 8:
        logger.warning("Skipping incomplete dayk row for %s: %r", code, row)
        return None

    date_raw = row[0]
    try:
        date_str = str(date_raw)
        trade_date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    except (ValueError, IndexError):
        logger.warning("Skipping row with invalid date for %s: %r", code, date_raw)
        return None

    open_ = _num(row[1])
    high = _num(row[2])
    low = _num(row[3])
    close = _num(row[4])
    volume = _num(row[5])
    amount = _num(row[6])
    prev_close = _num(row[7])

    # 涨跌幅（%）= change / prev_close * 100
    pct: Any = ""
    if prev_close and close is not None:
        change = close - prev_close
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
        "今收": _fmt_num(close),
        "涨跌幅（%）": pct,
        "成交量(万股)": vol_wan,
        "成交金额(万元)": amt_wan,
        "市盈率": "",  # not published by the SSE dayk endpoint
    }


# ---------------------------------------------------------------------------
# DB target stock loader
# ---------------------------------------------------------------------------
def load_target_stocks(conn) -> List[Tuple[str, str]]:
    """Return [(bare_code, name), ...] for SSE stocks held by ETFs.

    Mirrors ``stream_szse_price.load_target_stocks`` but filters to SSE codes
    via ``stock_identity.code_suffix = 'SS'`` (instead of SZSE's ``%.SZ``).
    Joins the latest stock_identity snapshot with the latest ETF composition
    snapshot in stats.sec_composition. ``stock_code`` in sec_composition
    carries the exchange suffix (e.g. "600000.SS"), matching stock_identity.code.

    Performance: the latest identity date and ETF snapshot date are precomputed
    via two tiny index lookups and passed as literal params. This lets Postgres
    use a Merge Join over a single-date slice of stock_identity (~2.3K rows)
    and a single-snapshot slice of sec_composition (~15.5K rows), touching ~1.5K
    buffers total — vs ~13K buffers for the ``DISTINCT ON`` / correlated-subquery
    form. All 1025 target codes are present on the latest identity date.
    """
    with conn.cursor() as cur:
        # Precompute the latest dates via index-friendly lookups (avoids the
        # expensive MAX(date) subquery scanning pk_stock_identity backward).
        cur.execute(
            "SELECT MAX(date) FROM stats.stock_identity WHERE code_suffix = 'SS'"
        )
        latest_id_date = cur.fetchone()[0]
        cur.execute(
            "SELECT MAX(snapshot_date) FROM stats.sec_composition "
            "WHERE source_type = 'etf'"
        )
        latest_snapshot = cur.fetchone()[0]

        if latest_id_date is None or latest_snapshot is None:
            logger.warning(
                "load_target_stocks: missing latest dates (id=%s, snapshot=%s)",
                latest_id_date, latest_snapshot,
            )
            return []

        # Materialize the distinct ETF-held stock_codes first, then merge-join
        # with stock_identity filtered to the latest date + code_suffix='SS'.
        cur.execute(
            """
            WITH etf_targets AS (
                SELECT DISTINCT stock_code
                  FROM stats.sec_composition
                 WHERE source_type = 'etf'
                   AND snapshot_date = %s
            )
            SELECT si.code, si.name
              FROM stats.stock_identity si
              JOIN etf_targets t ON t.stock_code = si.code
             WHERE si.code_suffix = 'SS'
               AND si.date = %s
             ORDER BY si.code
            """,
            (latest_snapshot, latest_id_date),
        )
        rows = cur.fetchall()

    stocks: List[Tuple[str, str]] = []
    for r in rows:
        full_code = r[0]
        name = r[1] or ""
        bare = full_code.split(".")[0]
        stocks.append((bare, name))
    logger.info(
        "Loaded %d target SSE stocks (ETF-held) from DB "
        "(id_date=%s, snapshot=%s).",
        len(stocks), latest_id_date, latest_snapshot,
    )
    return stocks


# ---------------------------------------------------------------------------
# CSV writers
# ---------------------------------------------------------------------------
def _get_archive_file(archive_dir: Path, code: str) -> Path:
    """Get per-stock trend archive CSV path under temps/sse_archive/.

    Files are named ``{code}_trend.csv``.
    """
    return archive_dir / f"{code}_trend.csv"


def _find_latest_biz_date_from_trend_dir(trend_dir: Path) -> Optional[date]:
    """Scan the sse_trend directory for the latest date file.

    Looks for ``sse_trend_stock_{YYYYMMDD}.csv`` files (written by
    ``download_sse_trend``) and returns the max date. Used to build a
    date-grouped PE file (``sse_pe_stock_<date>.csv``) for the latest
    trading day. PE rows for that date are only present when it happens
    to be a quarterly snapshot or big-jump date — today's PE is no longer
    forced on every run.

    Returns None if the directory is empty or doesn't exist.
    """
    if not trend_dir.exists():
        return None
    dates: List[date] = []
    for f in trend_dir.glob("sse_trend_stock_*.csv"):
        ymd = f.stem.rsplit("_", 1)[-1]
        if len(ymd) == 8 and ymd.isdigit():
            try:
                dates.append(date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8])))
            except ValueError:
                continue
    return max(dates) if dates else None


def _find_earliest_biz_date_from_trend_dir(trend_dir: Path) -> Optional[date]:
    """Scan the sse_trend directory for the earliest date file.

    Looks for ``sse_trend_stock_{YYYYMMDD}.csv`` files (written by
    ``download_sse_trend``) and returns the min date. Used to determine the
    earliest date of daily snapshot tracking, so the archive only needs to
    cover the historical gap from DEFAULT_START_DATE (2020-01-01) up to this
    date. Stocks whose archive already covers this range can be skipped.

    Returns None if the directory is empty or doesn't exist.
    """
    if not trend_dir.exists():
        return None
    dates: List[date] = []
    for f in trend_dir.glob("sse_trend_stock_*.csv"):
        ymd = f.stem.rsplit("_", 1)[-1]
        if len(ymd) == 8 and ymd.isdigit():
            try:
                dates.append(date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8])))
            except ValueError:
                continue
    return min(dates) if dates else None


# ---------------------------------------------------------------------------
# Trading statistics (成交统计) helpers
# ---------------------------------------------------------------------------
def _compute_quarterly_dates(
    start_year: int, end_year: int
) -> List[date]:
    """First trading day of Feb, Apr, May, Aug, Sep, Nov for each year in range.

    Returns a sorted (ascending) list of trading dates. Future dates (after
    today) are excluded since the SSE endpoint returns no data for them.
    """
    today = date.today()
    dates: List[date] = []
    for year in range(start_year, end_year + 1):
        for month in QUARTERLY_MONTHS:
            first_trading = next_business_day(date(year, month, 1))
            if first_trading > today:
                continue
            dates.append(first_trading)
    dates.sort()
    return dates


def _fetch_trade_stats_primary(
    session: requests.Session,
    code: str,
    tx_date: date,
    proxy: AntiBotProxy,
) -> Optional[Dict[str, Any]]:
    """Query the primary SSE 成交概况 endpoint (queryNewAllQuatAbel.do).

    Returns the first result dict (daily data containing
    ``closeProfitRate`` / ``totalExchRate``) or None when the endpoint returns
    an empty result (non-trading day, stock not yet listed, or fetch failure).
    """
    params = {
        "jsonCallBack": JSONP_CALLBACK,
        "FUNDID": code,
        "inMonth": tx_date.strftime("%Y%m"),
        "inYear": tx_date.strftime("%Y"),
        "searchDate": tx_date.isoformat(),
    }
    resp = proxy.get(
        session,
        SSE_TRADE_STATS_URL,
        params=params,
        headers=SSE_TRADE_STATS_HEADERS,
        timeout=DEFAULT_TIMEOUT,
        logger=logger,
        log_tag=f"[trade-stats {code} {tx_date.isoformat()}]",
    )
    if resp is None:
        return None
    payload = _parse_jsonp(resp.text)
    result_arr = payload.get("result") or []
    if not result_arr:
        return None
    # result[0] = daily data, result[1] = monthly, result[2] = yearly
    raw = result_arr[0]
    # Validate: closeTxDate must be a non-empty YYYY-MM-DD string matching the
    # requested date. The API sometimes returns a result with null/empty
    # closeTxDate and 0.0 values — skip those.
    close_tx = raw.get("closeTxDate")
    if not close_tx or not isinstance(close_tx, str):
        return None
    expected = tx_date.isoformat()
    if close_tx != expected:
        logger.debug(
            "  [trade-stats %s %s] closeTxDate mismatch: got %s",
            code, expected, close_tx,
        )
        return None
    return raw


def _fetch_trade_stats_alt(
    session: requests.Session,
    code: str,
    tx_date: date,
    proxy: AntiBotProxy,
) -> Optional[Dict[str, Any]]:
    """Query the alternative SSE 成交统计 endpoint (commonQuery.do).

    Uses the ``COMMON_SSE_CP_GPJCTPZ_GPLB_CJGK_MRGK_C`` sqlId from the
    company page's 成交统计 section (``.../company/index.shtml``). Returns a
    dict normalized to the same key names as the primary API
    (``closeTxDate`` / ``closeProfitRate`` / ``totalExchRate`` /
    ``productName``) so ``_parse_trade_stats_row`` works unchanged, or None
    on failure / empty result / date mismatch.
    """
    params = {
        "jsonCallBack": JSONP_CALLBACK,
        "sqlId": SSE_TRADE_STATS_ALT_SQL_ID,
        "SEC_CODE": code,
        "TX_DATE": tx_date.isoformat(),
    }
    resp = proxy.get(
        session,
        SSE_TRADE_STATS_ALT_URL,
        params=params,
        headers=SSE_TRADE_STATS_HEADERS,
        timeout=DEFAULT_TIMEOUT,
        logger=logger,
        log_tag=f"[trade-stats-alt {code} {tx_date.isoformat()}]",
    )
    if resp is None:
        return None
    payload = _parse_jsonp(resp.text)
    result_arr = payload.get("result") or []
    if not result_arr:
        return None
    raw = result_arr[0]
    # TX_DATE comes back as "YYYYMMDD"; convert to "YYYY-MM-DD" and validate.
    tx_raw = raw.get("TX_DATE")
    if not tx_raw or not isinstance(tx_raw, str) or len(tx_raw) != 8:
        return None
    tx_str = f"{tx_raw[:4]}-{tx_raw[4:6]}-{tx_raw[6:8]}"
    expected = tx_date.isoformat()
    if tx_str != expected:
        logger.debug(
            "  [trade-stats-alt %s %s] TX_DATE mismatch: got %s",
            code, expected, tx_str,
        )
        return None
    # Normalize to primary API key names so downstream parsing is shared.
    return {
        "closeTxDate": tx_str,
        "closeProfitRate": raw.get("PE_RATE"),
        "totalExchRate": raw.get("TO_RATE"),
        "productName": raw.get("SEC_NAME"),
    }


def _parse_trade_stats_row(
    raw: Dict[str, Any], code: str, name: str
) -> Dict[str, Any]:
    """Map one SSE 成交概况 result dict to the PE CSV row schema.

    Returns a single dict with both 静态市盈率(倍) and 总换手率(%).
    Field mapping (from queryNewAllQuatAbel.do result[0] = daily data):
      closeTxDate  → 日期 (already "YYYY-MM-DD")
      closeProfitRate → 静态市盈率(倍)
      totalExchRate → 总换手率(%)
    """
    date_str = str(raw.get("closeTxDate", ""))

    def _val(key: str) -> Any:
        v = raw.get(key)
        if v is None or v == "":
            return ""
        try:
            return round(float(v), 4)
        except (TypeError, ValueError):
            return v

    return {
        "日期": date_str,
        "证券代码": code,
        "证券简称": raw.get("productName") or name,
        "静态市盈率(倍)": _val("closeProfitRate"),
        "总换手率(%)": _val("totalExchRate"),
    }


def _detect_jump_dates(
    ohlcv_rows: List[Dict[str, Any]],
    threshold_pct: float = JUMP_THRESHOLD_PCT,
    cooldown_days: int = JUMP_COOLDOWN_DAYS,
) -> List[date]:
    """Find dates where daily move (drop OR rise) > threshold, with cooldown.

    Scans the OHLCV history for days where the absolute close-to-close return
    exceeds ``threshold_pct`` (i.e. ``|pct| > threshold`` — both big drops and
    big rises). The return is computed from consecutive 今收 (close) prices —
    the 涨跌幅（%）field from the dayk endpoint is often empty because the API
    does not always return ``prev_close``.

    For each jump, collects both the previous day and the jump day as PE
    request dates. After a jump-triggered request, subsequent jumps within
    ``cooldown_days`` calendar days are skipped.

    Returns a sorted list of dates (prev day + jump day pairs).
    """
    jump_pe_dates: List[date] = []
    last_jump_anchor: Optional[date] = None

    def _close_val(row: Dict[str, Any]) -> Optional[float]:
        v = row.get("今收")
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    for i in range(1, len(ohlcv_rows)):
        prev_close = _close_val(ohlcv_rows[i - 1])
        curr_close = _close_val(ohlcv_rows[i])
        if prev_close is None or curr_close is None or prev_close == 0:
            continue
        pct = (curr_close - prev_close) / prev_close * 100
        # Detect BOTH big drops AND big rises (abs > threshold).
        if abs(pct) <= threshold_pct:
            continue

        try:
            curr_date = date.fromisoformat(ohlcv_rows[i]["交易日期"])
            prev_date = date.fromisoformat(ohlcv_rows[i - 1]["交易日期"])
        except (KeyError, ValueError):
            continue

        # Cooldown: skip jumps too close to the last requested jump.
        if last_jump_anchor is not None:
            if (curr_date - last_jump_anchor).days <= cooldown_days:
                continue

        jump_pe_dates.append(prev_date)
        jump_pe_dates.append(curr_date)
        last_jump_anchor = curr_date

    jump_pe_dates.sort()
    return jump_pe_dates


def _read_existing_trade_stats(path: Optional[Path]) -> Dict[str, Dict[str, Any]]:
    """Read a trade-stats CSV and return {date_str: row}.

    Used to skip already-fetched dates on resumable runs. Returns an empty
    dict when ``path`` is None or the file does not exist / is invalid.
    """
    existing: Dict[str, Dict[str, Any]] = {}
    if path is None or not path.exists():
        return existing
    try:
        with open(path, "r", encoding=CSV_ENCODING, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                d = (row.get("日期") or "").strip()
                # Skip invalid dates (e.g., "None", empty, or non-YYYY-MM-DD)
                try:
                    date.fromisoformat(d)
                except ValueError:
                    continue
                existing[d] = row
    except (OSError, csv.Error):
        pass
    return existing


def _write_stats_csv(
    path: Path, rows: List[Dict[str, Any]], columns: List[str]
) -> None:
    """(Re)write a CSV, sorted ascending by date."""
    rows_sorted = sorted(rows, key=lambda r: r.get("日期", ""))
    with open(path, "w", encoding=CSV_ENCODING, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for r in rows_sorted:
            writer.writerow(r)


def _build_date_grouped_pe_file(
    trend_dir: Path,
    archive_dir: Path,
    stocks: List[Tuple[str, str]],
    latest_biz_date: date,
) -> int:
    """Build ``sse_pe_stock_<YYYYMMDD>.csv`` under ``trend_dir`` from per-stock PE files.

    Collects all stocks' PE rows for ``latest_biz_date`` from their
    ``{code}_pe.csv`` files and writes them into a single date-grouped CSV —
    mirroring the ``sse_trend_stock_<date>.csv`` layout so the build script
    can read PE for the latest date alongside the trend files.

    PE rows for ``latest_biz_date`` are only present when that date happens
    to be a quarterly snapshot or big-jump date (today's PE is not forced on
    every run); the function logs and writes nothing when no PE rows exist
    for the date.

    The file is rebuilt every run (overwritten), ensuring it reflects the
    latest state of all per-stock PE files. Returns the number of rows
    written.
    """
    pe_group_file = trend_dir / f"sse_pe_stock_{latest_biz_date:%Y%m%d}.csv"
    date_str = latest_biz_date.isoformat()
    pe_rows: List[Dict[str, Any]] = []
    for code, _name in stocks:
        pe_file = archive_dir / f"{code}_pe.csv"
        existing_pe = _read_existing_trade_stats(pe_file)
        if date_str in existing_pe:
            pe_rows.append(existing_pe[date_str])
    if pe_rows:
        # Sort by 证券代码 for stable, deterministic output.
        pe_rows.sort(key=lambda r: r.get("证券代码", ""))
        _write_stats_csv(pe_group_file, pe_rows, PE_COLUMNS)
        logger.info(
            "Wrote %d PE rows for %s -> %s",
            len(pe_rows), latest_biz_date, pe_group_file.name,
        )
    else:
        logger.info(
            "No PE rows found for %s in per-stock PE files",
            latest_biz_date,
        )
    return len(pe_rows)


def _run_pe_for_stock(
    code: str,
    name: str,
    ohlcv_rows: List[Dict[str, Any]],
    archive_dir: Path,
    sess: requests.Session,
    stats_proxy: AntiBotProxy,
    all_quarterly_dates: List[date],
    today: date,
    *,
    force: bool = False,
) -> Tuple[int, int]:
    """Request PE (静态市盈率 + 总换手率) for a single stock.

    Computes PE request dates (quarterly snapshots + big-jump dates with
    cooldown), fetches PE data from the SSE 成交概况 endpoint, and appends
    each result immediately to ``{code}_pe.csv``.

    PE request dates are the union of:
      (a) Quarterly snapshots — first trading day of Feb, Apr, May, Aug,
          Sep, Nov each year (filtered to the stock's trading history).
      (b) Big-jump dates — days where close-to-close return exceeds ±7%
          (both drops and rises); for each jump, PE is requested for both
          the previous day and the jump day. A 7-day cooldown suppresses
          subsequent jumps.

    The latest trading day is NOT automatically included — PE for today is
    only fetched when today happens to be a quarterly snapshot date or a
    big-jump date (same criteria as any historical date). The daily price
    (trend) is always downloaded regardless.

    API fallback strategy (per user request): the primary endpoint
    (queryNewAllQuatAbel.do) is tried first. When it fails for a date, the
    alternative endpoint (commonQuery.do from the company page's 成交统计
    section) is tried. Whichever API last succeeded becomes the "preferred"
    API for subsequent dates — so once the alternative starts working it is
    kept until it fails, at which point the primary is retried. Only when
    both APIs fail for a date is it marked as "no data".

    Returns ``(pe_fetched, pe_skipped)``.
    """
    if stats_proxy.is_blocked(SSE_TRADE_STATS_URL):
        logger.warning("  [host-blocked] query.sse.com.cn blocked, skipping PE for %s", code)
        return (0, 0)

    # Compute PE request dates:
    # (a) quarterly snapshots — filtered to the stock's actual trading
    #     history (from its first trading date to today)
    first_date = date.fromisoformat(ohlcv_rows[0]["交易日期"])
    quarterly_dates = [d for d in all_quarterly_dates if d >= first_date]
    pe_date_set: set = set(quarterly_dates)

    # (b) big-jump dates (drops + rises, abs > 7%) with 7-day cooldown
    jump_dates = _detect_jump_dates(ohlcv_rows)
    pe_date_set.update(jump_dates)

    # The latest trading day is NOT automatically added — PE for today is
    # only fetched when today naturally qualifies as a quarterly snapshot
    # date or a big-jump date (same criteria as any historical date).
    # The daily price (trend) is always downloaded regardless.

    # Filter out future dates (endpoint returns nothing for them).
    pe_dates = sorted(d for d in pe_date_set if d <= today)

    # Prepare {code}_pe.csv for incremental appends.
    # Resumable: if the file already exists (interrupted run), read
    # what's already been fetched. Otherwise, start fresh.
    pe_file = archive_dir / f"{code}_pe.csv"
    if force and pe_file.exists():
        pe_file.unlink()
    if pe_file.exists():
        existing_pe = _read_existing_trade_stats(pe_file)
    else:
        existing_pe = {}
        # Create empty file with header.
        _write_stats_csv(pe_file, [], PE_COLUMNS)

    dates_to_fetch = [
        d for d in pe_dates if d.isoformat() not in existing_pe
    ]

    new_count = 0
    pe_skipped = 0
    # API fallback state: start with the primary (old) API. When the
    # alternative API succeeds it becomes preferred; when it fails we
    # switch back to primary. Both must fail for a date to be marked no-data.
    prefer_alt = False
    for tx_date in dates_to_fetch:
        if stats_proxy.is_blocked(SSE_TRADE_STATS_URL):
            logger.warning("  [host-blocked] stopping mid-stock %s", code)
            break

        # Try the preferred API first.
        # Auto-sleep handled by proxy.get()/post()
        if prefer_alt:
            raw = _fetch_trade_stats_alt(sess, code, tx_date, stats_proxy)
            used_alt = True
        else:
            raw = _fetch_trade_stats_primary(sess, code, tx_date, stats_proxy)
            used_alt = False

        # Preferred API failed -> try the other API.
        if raw is None:
            # Auto-sleep handled by proxy.get()/post()
            if prefer_alt:
                raw = _fetch_trade_stats_primary(sess, code, tx_date, stats_proxy)
                used_alt = False
            else:
                raw = _fetch_trade_stats_alt(sess, code, tx_date, stats_proxy)
                used_alt = True

        if raw is None:
            # Both APIs failed for this date.
            logger.info(
                "  -> %s pe %s: FAILED (no data, both APIs)",
                code, tx_date.isoformat(),
            )
            pe_skipped += 1
            continue

        # Success: stick with the API that just worked for subsequent dates.
        prefer_alt = used_alt

        row = _parse_trade_stats_row(raw, code, name)
        new_count += 1

        # Append immediately to CSV (resumable — each fetch is saved).
        with open(pe_file, "a", encoding=CSV_ENCODING, newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=PE_COLUMNS, extrasaction="ignore"
            )
            writer.writerow(row)

        api_tag = "alt" if used_alt else "primary"
        logger.info(
            "  -> %s pe %s [%s]: PE=%s, turnover=%s",
            code, tx_date.isoformat(), api_tag,
            row.get("静态市盈率(倍)", ""),
            row.get("总换手率(%)", ""),
        )

    total_pe = len(existing_pe) + new_count
    if new_count:
        logger.info(
            "  -> %s pe: +%d rows (total %d, jumps=%d) -> %s",
            code, new_count, total_pe,
            len(jump_dates), pe_file.name,
        )
    elif dates_to_fetch:
        logger.info("  -> %s pe: 0 new (all %d cached)", code, len(pe_dates))

    return (new_count, pe_skipped)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def download_sse_archive(
    out_root: Optional[str] = None,
    *,
    sleep_sec: float = TRADE_STATS_SLEEP_SEC,
    force: bool = False,
    session: Optional[requests.Session] = None,
    etf_filter: bool = True,
) -> dict:
    """Download SSE HISTORICAL daily price + PE data (archive mode).

    Studies ``https://www.sse.com.cn/market/price/trends/`` 行情走势 and
    downloads the full date range available from the dayk slider — the entire
    trading history of each stock, not a user-specified year range.

    Per-stock workflow (trend → PE → next stock):
      For each stock, the trend is fetched first (cached or API), then PE
      is requested immediately for that stock before moving on to the next.

      Trend: fetch the full OHLCV daily history from the dayk endpoint
        (yunhq.sse.com.cn) and write ``{code}_trend.csv``.

      PE: compute PE request dates and fetch 静态市盈率(倍) (PE_RATE) +
        总换手率(%) (TO_RATE) from the SSE 成交概况 endpoint, writing
        ``{code}_pe.csv``.

        PE request dates are the union of:
        (a) Quarterly snapshots — first trading day of Feb, Apr, May, Aug,
            Sep, Nov each year, spanning the stock's full history.
        (b) Big-jump dates — days where the close-to-close return exceeds
            ±7% (both big drops AND big rises); for each jump, PE is
            requested for both the previous day and the jump day. After each
            jump-triggered request, a 7-day cooldown suppresses subsequent
            jumps.

        The latest trading day is NOT automatically fetched — PE for today
        is only requested when today happens to be a quarterly snapshot
        date or a big-jump date (same criteria as any historical date). The
        daily price (trend) is always downloaded regardless.

    Both files live under ``temps/sse_archive/``. Resumable: existing
    ``{code}_pe.csv`` is read to skip already-fetched PE dates unless
    ``force=True``.

    Archive skip logic: the archive only needs to cover historical data from
    DEFAULT_START_DATE (2020-01-01) up to the earliest date found in the
    ``sse_trend`` directory (where daily snapshots are written by
    ``download_sse_trend.py``). Stocks whose ``{code}_trend.csv`` already
    contains data up to this threshold date are skipped to avoid unnecessary
    API calls. If the ``sse_trend`` directory is empty or missing, falls back
    to checking against the expected latest business day.

    For TODAY's snapshot (date-grouped ``sse_trend_stock_<date>.csv``), run
    ``download_sse_trend.py`` — it fetches the SSE list endpoint (same data
    source as ``stream_sse_price.py``).
    """
    archive_dir = resolve_out_dir(
        str(Path(__file__).resolve()), TRADE_STATS_DIRNAME, out_root
    )
    sess = session or requests.Session()

    today = date.today()
    # Calculate the expected latest trading day: today if it's a trading day,
    # otherwise the most recent previous trading day. Used for building the
    # date-grouped PE file (sse_pe_stock_<date>.csv) at the end.
    expected_latest_biz_date = last_business_day(today)

    # Determine the latest biz date from existing sse_trend files (written by
    # download_sse_trend). Used to build a date-grouped PE file for the latest
    # date — PE for that date is only present when it happens to be a
    # quarterly snapshot or big-jump date (today's PE is not forced).
    trend_dir = resolve_out_dir(
        str(Path(__file__).resolve()), "sse_trend", out_root
    )
    existing_latest_biz_date = _find_latest_biz_date_from_trend_dir(trend_dir)

    # Find the earliest date from sse_trend directory to determine the start of
    # daily snapshot tracking. The archive only needs to cover from DEFAULT_START_DATE
    # (2020-01-01) up to this earliest date — stocks with archive data already
    # covering this range can be skipped.
    earliest_trend_date = _find_earliest_biz_date_from_trend_dir(trend_dir)
    # Fall back to expected_latest_biz_date if sse_trend dir is empty/missing
    skip_check_date = earliest_trend_date or expected_latest_biz_date
    skip_check_str = skip_check_date.isoformat()

    logger.info("Today: %s, expected latest trading day: %s", today, expected_latest_biz_date)
    if existing_latest_biz_date:
        logger.info("Latest biz date from sse_trend dir: %s", existing_latest_biz_date)
    if earliest_trend_date:
        logger.info("Earliest biz date from sse_trend dir: %s", earliest_trend_date)
        logger.info("Archive skip threshold: %s (covers %s to %s)", skip_check_str, DEFAULT_START_DATE, skip_check_str)

    # Precompute quarterly snapshot dates from DEFAULT_START_DATE to today.
    # Per-stock filtering by first trading date is applied inside the loop.
    # Computed BEFORE the pre-scan so the latest quarterly date can be used
    # to decide whether PE files are up-to-date.
    all_quarterly_dates = _compute_quarterly_dates(int(DEFAULT_START_DATE[:4]), today.year)
    if not all_quarterly_dates:
        logger.error("No quarterly dates computed")
        return {"downloaded": 0, "failed": 1, "archive_dir": str(archive_dir)}
    logger.info(
        "Quarterly snapshot dates (%d): %s .. %s (full SSE range %s-%d)",
        len(all_quarterly_dates),
        all_quarterly_dates[0].isoformat(),
        all_quarterly_dates[-1].isoformat(),
        DEFAULT_START_DATE[:4],
        today.year,
    )

    # Latest quarterly snapshot date that should be present in every PE file.
    # Used by the pre-scan to decide whether PE data is current — a stock is
    # only skipped when BOTH its trend file AND its PE file are up-to-date.
    latest_quarterly_date = max(
        (d for d in all_quarterly_dates if d <= today), default=None
    )
    latest_quarterly_str = (
        latest_quarterly_date.isoformat() if latest_quarterly_date else None
    )

    # --- Pre-scan existing archive files FIRST before DB/API ---
    # This is critical for idempotency: only fetch for stocks that actually
    # need updating. A stock is skipped only when BOTH:
    #   (a) {code}_trend.csv covers up to skip_check_str (earliest sse_trend date)
    #   (b) {code}_pe.csv contains the latest quarterly snapshot date
    # Otherwise the stock is processed — the PE workflow skips any dates
    # already cached and fetches only the missing ones.
    up_to_date_stocks: set = set()
    pe_missing_stocks: int = 0
    if not force and archive_dir.exists():
        for csv_file in archive_dir.glob("*_trend.csv"):
            if not is_valid_file(csv_file, min_bytes=MIN_VALID_BYTES):
                continue
            try:
                code = csv_file.stem.replace("_trend", "")
                with open(csv_file, encoding=CSV_ENCODING, newline="") as f:
                    reader = csv.DictReader(f)
                    ohlcv_rows = [dict(r) for r in reader]
                if ohlcv_rows:
                    latest_date_in_archive = max(
                        r["交易日期"] for r in ohlcv_rows if r.get("交易日期")
                    )
                    # Skip if archive already covers up to the earliest sse_trend date
                    # (or expected_latest_biz_date if sse_trend is empty)
                    if latest_date_in_archive < skip_check_str:
                        continue  # trend not up-to-date
                    # Trend is up-to-date; also check PE file coverage.
                    if latest_quarterly_str:
                        pe_file = archive_dir / f"{code}_pe.csv"
                        existing_pe = _read_existing_trade_stats(pe_file)
                        if latest_quarterly_str not in existing_pe:
                            pe_missing_stocks += 1
                            continue  # PE missing latest quarterly — needs processing
                    up_to_date_stocks.add(code)
            except Exception:
                continue
        logger.info(
            "Pre-scanned archive: %d stocks already up-to-date (skip them), "
            "%d stocks need PE updates (latest quarterly=%s)",
            len(up_to_date_stocks), pe_missing_stocks, latest_quarterly_str,
        )

    # dayk endpoint (yunhq.sse.com.cn) needs the full 20s cadence.
    # trade-stats endpoint (query.sse.com.cn) uses TRADE_STATS_SLEEP_SEC
    # (stats_proxy created below, used per-stock right after trend).
    dayk_proxy = AntiBotProxy(AntiBotConfig(
        base_sleep_sec=DEFAULT_SLEEP_SEC,
        sleep_jitter=0.3,
        rotate_browser_profile=True,
        add_random_param=True,
        enable_host_tracking=True,
        timeout=DEFAULT_TIMEOUT,
    ))

    # Build the target stock list. When etf_filter=False, _fetch_all_stocks
    # hits yunhq.sse.com.cn which needs the full DEFAULT_SLEEP_SEC cadence.
    if etf_filter:
        db_conn = get_db_connection()
        try:
            stocks = load_target_stocks(db_conn)
        finally:
            db_conn.close()
    else:
        stocks = _fetch_all_stocks(sess, dayk_proxy)
    if not stocks:
        logger.error("No stocks found")
        return {"downloaded": 0, "failed": 1, "archive_dir": str(archive_dir)}

    # Filter out already-up-to-date stocks to avoid unnecessary fetching
    if up_to_date_stocks:
        stocks = [(code, name) for code, name in stocks if code not in up_to_date_stocks]
        logger.info(
            "Filtered to %d stocks needing update (removed %d up-to-date)",
            len(stocks), len(up_to_date_stocks),
        )

    logger.info(
        "Starting SSE archive download: %d stocks. Per-stock workflow: "
        "trend (cached or API) -> PE -> next stock",
        len(stocks),
    )

    stocks_done = 0
    stocks_failed = 0
    pe_rows_fetched = 0
    pe_rows_skipped = 0
    trend_rows_written = 0

    # PE stats proxy (query.sse.com.cn) — created once, used per-stock
    # right after each trend fetch. The dayk endpoint (yunhq.sse.com.cn)
    # uses dayk_proxy with the full 20s cadence; the trade-stats endpoint
    # uses this lighter proxy (TRADE_STATS_SLEEP_SEC).
    stats_proxy = AntiBotProxy(AntiBotConfig(
        base_sleep_sec=sleep_sec,
        sleep_jitter=0.3,
        rotate_browser_profile=True,
        add_random_param=True,
        enable_host_tracking=True,
        timeout=DEFAULT_TIMEOUT,
    ))

    # ----- Per-stock workflow: trend (cached or API) -> PE -> next stock -----
    for idx, (code, name) in enumerate(stocks):
        if dayk_proxy.is_blocked(SSE_DAYK_URL.format(code=code)):
            logger.warning("  [host-blocked] yunhq.sse.com.cn blocked, stopping")
            stocks_failed += len(stocks) - idx
            break

        if (idx + 1) % 25 == 0 or idx == 0:
            logger.info(
                "Stock %d/%d: %s %s", idx + 1, len(stocks), code, name,
            )

        trend_file = archive_dir / f"{code}_trend.csv"

        # Skip if trend file already exists and is valid (resumable), but only
        # if it contains data up to the earliest sse_trend date (or expected
        # latest if sse_trend is empty). The archive only needs to cover the
        # historical gap from DEFAULT_START_DATE (2020-01-01) up to this date.
        ohlcv_rows: Optional[List[Dict[str, Any]]] = None
        if not force and is_valid_file(trend_file, min_bytes=MIN_VALID_BYTES):
            with open(trend_file, encoding=CSV_ENCODING, newline="") as f:
                cached_rows = [dict(r) for r in csv.DictReader(f)]
            if cached_rows:
                latest_date_in_archive = max(
                    r["交易日期"] for r in cached_rows if r.get("交易日期")
                )
                if latest_date_in_archive >= skip_check_str:
                    ohlcv_rows = cached_rows
                    logger.info(
                        "  -> %s trend: %d rows (cached, up-to-date)",
                        code, len(ohlcv_rows),
                    )

        if ohlcv_rows is None:
            # Auto-sleep handled by proxy.get()/post()
            dayk_data = _fetch_dayk(sess, code, dayk_proxy)
            if dayk_data is None:
                logger.error("  dayk fetch failed for %s, skipping", code)
                stocks_failed += 1
                continue

            kline = dayk_data.get("kline", []) or []
            trend_start = DEFAULT_START_DATE
            ohlcv_rows = []
            for row in kline:
                parsed = _parse_dayk_row(row, code, name)
                if parsed and parsed["交易日期"] >= trend_start:
                    ohlcv_rows.append(parsed)

            if ohlcv_rows:
                _write_rows(trend_file, ohlcv_rows, write_header=True)
                trend_rows_written += len(ohlcv_rows)
                logger.info(
                    "  -> %s trend: %d rows -> %s",
                    code, len(ohlcv_rows), trend_file.name,
                )
            else:
                logger.info("  -> %s trend: no dayk data", code)
                continue  # no OHLCV -> cannot compute PE dates

        # --- PE requests for this stock (immediately after trend) ---
        if stats_proxy.is_blocked(SSE_TRADE_STATS_URL):
            logger.debug(
                "  [host-blocked] query.sse.com.cn blocked, skipping PE for %s",
                code,
            )
        else:
            f, s = _run_pe_for_stock(
                code, name, ohlcv_rows, archive_dir, sess,
                stats_proxy, all_quarterly_dates, today, force=force,
            )
            pe_rows_fetched += f
            pe_rows_skipped += s
        stocks_done += 1

    logger.info(
        "Per-stock workflow done: %d/%d stocks done, %d failed, "
        "%d trend rows written, %d PE rows fetched, %d PE rows skipped",
        stocks_done, len(stocks), stocks_failed,
        trend_rows_written, pe_rows_fetched, pe_rows_skipped,
    )

    # Build date-grouped PE file for the expected latest biz date.
    pe_group_rows = _build_date_grouped_pe_file(
        trend_dir, archive_dir, stocks, expected_latest_biz_date
    )

    logger.info(
        "Done SSE archive. stocks_done=%d stocks_failed=%d "
        "pe_fetched=%d pe_skipped=%d trend_rows=%d pe_group_rows=%d "
        "archive_dir=%s",
        stocks_done, stocks_failed, pe_rows_fetched, pe_rows_skipped,
        trend_rows_written, pe_group_rows, archive_dir,
    )
    return {
        "downloaded": stocks_done,
        "failed": stocks_failed,
        "pe_rows_fetched": pe_rows_fetched,
        "pe_rows_skipped": pe_rows_skipped,
        "trend_rows_written": trend_rows_written,
        "archive_dir": str(archive_dir),
        "total_stocks": len(stocks),
    }


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description="Download SSE HISTORICAL daily price + 成交统计 PE/turnover "
                    "(default: only stocks held by ETFs). "
                    "Per-stock workflow: trend (cached or API) -> PE -> next "
                    "stock. Trend -> {code}_trend.csv, PE -> {code}_pe.csv "
                    "for quarterly snapshots and >7%% big-jump dates. "
                    "For TODAY's snapshot, use download_sse_trend.py instead."
    )
    ap.add_argument(
        "--no-etf-filter", action="store_true",
        help="Disable the ETF filter and fetch ALL SSE equities "
             "from the SSE list endpoint instead.",
    )
    ap.add_argument("--force", action="store_true",
                    help="Overwrite existing per-stock CSV files.")
    args = ap.parse_args()
    print(download_sse_archive(force=args.force, etf_filter=not args.no_etf_filter))
