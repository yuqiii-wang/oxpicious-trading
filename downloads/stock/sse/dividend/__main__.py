"""Download Shanghai Stock Exchange (SSE) per-stock DIVIDEND (利润分配/分红) data.

Studies ``https://www.sse.com.cn/assortment/stock/list/info/company/index.shtml?COMPANY_CODE=600008``
  → 利润分配 → 分红 tab and downloads the full dividend history per stock.

The page is JS-rendered; the underlying data is served as JSONP by the same
``http://query.sse.com.cn/commonQuery.do`` endpoint that already powers the
"成交统计" PE/turnover download (see ``downloads/stock/sse/archive/__main__.py``).
The two endpoints differ only in ``sqlId`` and request parameters:

  • 成交统计 (PE)  — sqlId ``COMMON_SSE_CP_GPJCTPZ_GPLB_CJGK_MRGK_C``
                    params: ``SEC_CODE``, ``TX_DATE`` (one date per request)
  • 利润分配-分红  — sqlId ``COMMON_SSE_CP_GPJCTPZ_GPLB_LRFP_FH_L``
                    params: ``COMPANY_CODE``, ``IS_STAR``, ``CONDITION_ZBA``,
                    ``CONDITION_ZBB``, ``pageHelp.*`` (one request returns
                    the full dividend history for the stock)

Response fields (per dividend row, all A-share fields; B-share equivalents
exist with the ``B_`` prefix but are mostly ``-`` for A-only listings):

  A_REG_DATE        — 股权登记日          (YYYYMMDD string)
  A_DIV_DATE        — 除息交易日          (YYYYMMDD string, ex-dividend date)
  A_STOCK_VOL       — 股权登记日总股本(万股)
  A_BEFR_TAX_DIV    — 每股红利(含税)      (per share, yuan)
  A_AFTR_TAX_DIV    — 每股红利(税后)      (per share, yuan)
  A_DIV_TOT         — 分红总额(万元)
  PRE_CLOSE_PRICE   — 除息前日收盘价
  OPEN_PRICE        — 除息报价
  EXCHANGE_RATE     — 汇率 (B-share only)
  ISSUE_VOL         — 发行量(总股本)
  A_STOCK_CODE      — A股代码
  COMPANY_CODE      — 公司代码 (same as input param)
  COMPANY_ABBR      — 公司简称
  FULL_NAME         — 公司全称
  NUM               — 序号

Anti-bot: uses the SAME ``AntiBotProxy`` configuration as the PE requests in
``downloads/stock/sse/archive/__main__.py`` —
``rotate_browser_profile=True``, ``add_random_param=True``,
``enable_host_tracking=True``, ``sleep_jitter=0.3``, base sleep
``LONG_SLEEP_INTERVAL`` (90s). A mandatory ``Referer`` header pointing to
the SSE company page is set via ``build_headers_with_referer``.

Outputs one CSV per stock under ``temps/sse_archive/``:

  ``{code}_dividend_{YYYYMMDD}.csv``

The filename always carries the download date as a suffix so the download
script can decide whether to re-download by checking:

  1. DB table ``stats.stock_dividends`` — if any row for this stock has
     ``last_updated`` within the last 6 months, the data is still fresh; skip.
  2. Local CSV — if any ``{code}_dividend_{YYYYMMDD}.csv`` exists with a
     date within the last 6 months, the data is still fresh; skip.

CSV columns (Chinese headers, mirroring the PE file convention):
  公告日期, 证券代码, 证券简称, 股权登记日, 除息交易日,
  每股红利(含税)(元), 每股红利(税后)(元), 分红总额(万元),
  除息前日收盘价, 除息报价, 总股本(万股)

Notes:
  • The 分红 API does NOT return 公告日期 (announcement date) — only
    ``A_REG_DATE`` (股权登记日) and ``A_DIV_DATE`` (除息交易日) are available.
    The 公告日期 column is kept in the schema for forward compatibility but
    is left empty.
  • 每股红利 values from the API are PER SHARE (not per 10 shares). They are
    stored as-is in yuan per share.
  • Pagination: ``pageHelp.pageSize=50`` fetches up to 50 rows per request;
    most stocks have <10 dividend events so one page is usually enough. The
    code loops pages until ``pageHelp.pageCount`` is exhausted.
  • Three board variants exist for the same sqlId (A股主板 / A股科创板 /
    B股主板). We try A股主板 first; if no rows are returned, we try A股科创板
    (STAR market) and B股主板 in turn. Most stocks only have one board.
  • Resumable: a stock is skipped if (a) a local CSV dated within the last
    6 months exists, or (b) ``stats.stock_dividends`` already has rows for
    the stock with ``last_updated`` within the last 6 months. ``--force``
    overrides both checks.

Usage:
  python -m downloads.stock.sse.dividend                  # ETF-held SSE stocks only (default)
  python -m downloads.stock.sse.dividend --no-etf-filter  # all SSE equities
  python -m downloads.stock.sse.dividend --code 600008    # single stock
  python -m downloads.stock.sse.dividend --force          # overwrite existing files
"""
from __future__ import annotations

import argparse
import csv
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dateutil.relativedelta import relativedelta

from downloads._common.core import (
    DEFAULT_TIMEOUT,
    LONG_SLEEP_INTERVAL,
    MIN_VALID_BYTES,
    AntiBotConfig,
    AntiBotProxy,
    build_headers_with_referer,
    is_valid_file,
    resolve_out_dir,
    setup_logger,
)
from downloads.stock.sse._common.list_endpoint import (
    CSV_ENCODING,
    JSONP_CALLBACK,
    _parse_jsonp,
)

# --- Endpoint constants -----------------------------------------------------
# Same JSONP endpoint as the PE/turnover "成交统计" download — only the sqlId
# and request parameters differ.
SSE_DIVIDEND_URL = "http://query.sse.com.cn/commonQuery.do"
SSE_DIVIDEND_SQL_ID = "COMMON_SSE_CP_GPJCTPZ_GPLB_LRFP_FH_L"

# Referer is mandatory for this endpoint — without it the API returns
# {"success":"false","error":"System Error..."}. Pointing to the company page
# (the page that hosts the 利润分配/分红 tab) is the most natural choice.
SSE_COMPANY_REFERER = (
    "http://www.sse.com.cn/assortment/stock/list/info/company/index.shtml"
)
SSE_DIVIDEND_HEADERS = build_headers_with_referer(
    SSE_COMPANY_REFERER, extra={"Accept": "*/*"}
)

# Page size — the dividend endpoint supports up to ~50 rows per page. Most
# stocks have <10 dividend events so one page is enough; the code still
# paginates to be safe.
DIVIDEND_PAGE_SIZE = 50

# Per-stock archive directory — same as the PE files (temps/sse_archive/).
# Each stock gets one CSV per download date: {code}_dividend_{YYYYMMDD}.csv
DIVIDEND_DIRNAME = "sse_archive"

# Output columns (Chinese headers, mirroring the PE file convention).
# 公告日期 is kept for forward compatibility but the SSE 分红 API does not
# return it — the column is left empty.
DIVIDEND_COLUMNS: List[str] = [
    "公告日期",
    "证券代码",
    "证券简称",
    "股权登记日",
    "除息交易日",
    "每股红利(含税)(元)",
    "每股红利(税后)(元)",
    "分红总额(万元)",
    "除息前日收盘价",
    "除息报价",
    "总股本(万股)",
]

logger = setup_logger("sse_dividend_download")


# ---------------------------------------------------------------------------
# Freshness check — skip download if data is already up-to-date
# ---------------------------------------------------------------------------
# Dividend history is slow-moving (a stock pays dividends at most a few times
# per year), and main.sh schedules this download quarterly. A 6-month
# freshness window therefore safely skips re-downloading stocks that have
# already been fetched within the last half year, regardless of whether the
# CSV was written today or months ago.
FRESHNESS_WINDOW_MONTHS = 6


def _today_str() -> str:
    """Return today's date as YYYYMMDD string (for CSV filename suffix)."""
    return date.today().strftime("%Y%m%d")


def _freshness_cutoff() -> date:
    """Return the earliest date still considered fresh (today minus 6 months)."""
    return date.today() - relativedelta(months=FRESHNESS_WINDOW_MONTHS)


def _is_fresh_local(archive_dir: Path, code: str) -> bool:
    """Check if a local dividend CSV dated within the last 6 months exists.

    Scans ``archive_dir`` for any ``{code}_dividend_{YYYYMMDD}.csv`` whose
    filename date is at or after ``today - FRESHNESS_WINDOW_MONTHS``. Legacy
    files without a date suffix (``{code}_dividend.csv``) are NOT considered
    fresh — they were written before the date-suffix convention was
    introduced.

    Uses a 1-byte minimum (not MIN_VALID_BYTES=1024) because dividend CSVs
    for stocks with few events can be <700 bytes — still valid.
    """
    if not archive_dir.exists():
        return False
    cutoff = _freshness_cutoff()
    for f in archive_dir.glob(f"{code}_dividend_*.csv"):
        parts = f.stem.split("_")
        # Expected stem: {code}_dividend_{YYYYMMDD}
        if len(parts) < 3 or parts[-2] != "dividend":
            continue
        date_str = parts[-1]
        if len(date_str) != 8 or not date_str.isdigit():
            continue
        try:
            file_date = date(
                int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8])
            )
        except ValueError:
            continue
        if file_date >= cutoff and is_valid_file(f, min_bytes=1):
            return True
    return False


def _is_fresh_db(conn, full_code: str) -> bool:
    """Check if ``stats.stock_dividends`` has rows for this stock updated
    within the last 6 months.

    ``full_code`` is the DB-format code with exchange suffix (e.g. ``600008.SS``).
    Returns True if at least one row exists with ``last_updated::date >=
    today - FRESHNESS_WINDOW_MONTHS``.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM stats.stock_dividends "
                "WHERE code = %s AND last_updated::date >= %s LIMIT 1",
                (full_code, _freshness_cutoff()),
            )
            return cur.fetchone() is not None
    except Exception as e:
        logger.debug("DB freshness check failed for %s: %s", full_code, e)
        return False


# ---------------------------------------------------------------------------
# Board variants — try A股主板 first, then A股科创板 (STAR), then B股主板.
# Most stocks only have data for one board; iterating through all three
# ensures we don't miss STAR/B-share listings.
# ---------------------------------------------------------------------------
BOARD_VARIANTS: List[Dict[str, str]] = [
    # A股主板 (A-share main board)
    {"IS_STAR": "0", "CONDITION_ZBA": "1", "CONDITION_ZBB": ""},
    # A股科创板 (A-share STAR market)
    {"IS_STAR": "1", "CONDITION_ZBA": "1", "CONDITION_ZBB": ""},
    # B股主板 (B-share main board)
    {"IS_STAR": "0", "CONDITION_ZBA": "", "CONDITION_ZBB": "1"},
]


def _fetch_dividend_page(
    session: requests.Session,
    company_code: str,
    board_variant: Dict[str, str],
    page_no: int,
    proxy: AntiBotProxy,
) -> Optional[Dict[str, Any]]:
    """Fetch one page of the SSE dividend endpoint for a single stock+board.

    Returns the parsed JSONP payload (a dict), or None on fetch failure /
    blocked host. Caller is responsible for inspecting ``result`` /
    ``pageHelp`` to decide whether to keep going.
    """
    params = {
        "jsonCallBack": JSONP_CALLBACK,
        "isPagination": "true",
        "pageHelp.pageSize": str(DIVIDEND_PAGE_SIZE),
        "pageHelp.pageNo": str(page_no),
        "pageHelp.beginPage": str(page_no),
        "pageHelp.cacheSize": str(page_no),
        "pageHelp.endPage": str(page_no),
        "sqlId": SSE_DIVIDEND_SQL_ID,
        "COMPANY_CODE": company_code,
        **board_variant,
    }
    resp = proxy.get(
        session,
        SSE_DIVIDEND_URL,
        params=params,
        headers=SSE_DIVIDEND_HEADERS,
        timeout=DEFAULT_TIMEOUT,
        logger=logger,
        log_tag=f"[dividend {company_code} p{page_no}]",
    )
    if resp is None:
        return None
    return _parse_jsonp(resp.text)


def _fetch_all_dividend_rows(
    session: requests.Session,
    company_code: str,
    proxy: AntiBotProxy,
) -> Tuple[List[Dict[str, Any]], str]:
    """Fetch ALL dividend rows for one stock across all board variants.

    Iterates through BOARD_VARIANTS (A股主板 → A股科创板 → B股主板). For each
    variant, paginates until ``pageHelp.pageCount`` is exhausted or the
    response has no ``result`` array. Returns the concatenated raw result
    dicts plus the board label that yielded the data ("A_MAIN" / "A_STAR" /
    "B_MAIN"). Stops at the first variant that returns rows (a stock is
    listed on only one board).

    Returns (rows, board_label). When the host is blocked mid-fetch, returns
    whatever was collected so far.
    """
    board_labels = ["A_MAIN", "A_STAR", "B_MAIN"]
    for variant, label in zip(BOARD_VARIANTS, board_labels):
        if proxy.is_blocked(SSE_DIVIDEND_URL):
            logger.warning(
                "  [host-blocked] query.sse.com.cn blocked mid-stock %s",
                company_code,
            )
            return [], label

        all_rows: List[Dict[str, Any]] = []
        page_no = 1
        total_pages = 1  # updated from the first page's response
        while page_no <= total_pages:
            if proxy.is_blocked(SSE_DIVIDEND_URL):
                logger.warning(
                    "  [host-blocked] query.sse.com.cn blocked mid-pagination %s p%d",
                    company_code, page_no,
                )
                break
            payload = _fetch_dividend_page(
                session, company_code, variant, page_no, proxy
            )
            if payload is None:
                break  # network failure; stop this variant
            result_arr = payload.get("result") or []
            if not result_arr:
                break  # no data for this variant; try next board
            all_rows.extend(result_arr)
            # Update total_pages from the first page.
            page_help = payload.get("pageHelp") or {}
            if page_no == 1:
                total_pages = int(page_help.get("pageCount", 1) or 1)
            page_no += 1

        if all_rows:
            return all_rows, label

    return [], ""


def _parse_dividend_row(
    raw: Dict[str, Any], company_code: str
) -> Dict[str, Any]:
    """Map one SSE dividend result dict to the CSV row schema.

    Date fields come back as "YYYYMMDD" strings — converted to "YYYY-MM-DD".
    Numeric fields are coerced to float when possible, otherwise left blank.
    """
    def _date(yyyymmdd: Any) -> str:
        s = str(yyyymmdd).strip() if yyyymmdd is not None else ""
        if len(s) == 8 and s.isdigit():
            return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
        return ""

    def _num(val: Any) -> Any:
        if val is None or val == "" or val == "-":
            return ""
        try:
            return round(float(val), 4)
        except (TypeError, ValueError):
            return val

    return {
        "公告日期": "",  # SSE 分红 API does NOT return announcement date
        "证券代码": str(raw.get("A_STOCK_CODE") or company_code),
        "证券简称": str(raw.get("COMPANY_ABBR") or ""),
        "股权登记日": _date(raw.get("A_REG_DATE")),
        "除息交易日": _date(raw.get("A_DIV_DATE")),
        "每股红利(含税)(元)": _num(raw.get("A_BEFR_TAX_DIV")),
        "每股红利(税后)(元)": _num(raw.get("A_AFTR_TAX_DIV")),
        "分红总额(万元)": _num(raw.get("A_DIV_TOT")),
        "除息前日收盘价": _num(raw.get("PRE_CLOSE_PRICE")),
        "除息报价": _num(raw.get("OPEN_PRICE")),
        "总股本(万股)": _num(raw.get("A_STOCK_VOL")),
    }


def _write_dividend_csv(
    path: Path, rows: List[Dict[str, Any]]
) -> None:
    """(Re)write the dividend CSV, sorted ascending by 除息交易日."""
    rows_sorted = sorted(
        rows, key=lambda r: (r.get("除息交易日") or "", r.get("股权登记日") or "")
    )
    with open(path, "w", encoding=CSV_ENCODING, newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=DIVIDEND_COLUMNS, extrasaction="ignore"
        )
        writer.writeheader()
        for r in rows_sorted:
            writer.writerow(r)


def _read_existing_dividend_rows(path: Path) -> List[Dict[str, Any]]:
    """Read an existing dividend CSV and return rows (for skip detection)."""
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding=CSV_ENCODING, newline="") as f:
            for r in csv.DictReader(f):
                rows.append(r)
    except (OSError, csv.Error):
        pass
    return rows


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def download_sse_dividends(
    out_root: Optional[str] = None,
    *,
    sleep_sec: float = LONG_SLEEP_INTERVAL,
    force: bool = False,
    session: Optional[requests.Session] = None,
    etf_filter: bool = True,
    code_filter: Optional[str] = None,
) -> dict:
    """Download SSE per-stock dividend (利润分配/分红) data.

    For each SSE stock (default: ETF-held only; ``--no-etf-filter`` for all),
    fetches the full dividend history via the SSE ``commonQuery.do`` endpoint
    (sqlId ``COMMON_SSE_CP_GPJCTPZ_GPLB_LRFP_FH_L``) and writes one CSV per
    stock under ``temps/sse_archive/{code}_dividend_{YYYYMMDD}.csv``.

    Anti-bot: ``AntiBotProxy`` with ``rotate_browser_profile``,
    ``add_random_param``, ``enable_host_tracking``, ``sleep_jitter=0.3``,
    base sleep ``LONG_SLEEP_INTERVAL`` (90s).

    Resumable: a stock is skipped if (a) a local CSV dated within the last
    6 months exists, or (b) ``stats.stock_dividends`` already has rows for
    the stock with ``last_updated`` within the last 6 months. ``--force``
    overrides both checks.
    """
    archive_dir = resolve_out_dir(
        str(Path(__file__).resolve()), DIVIDEND_DIRNAME, out_root
    )
    sess = session or requests.Session()
    today_suffix = _today_str()

    # Resolve the target stock list.
    # ``--code`` overrides everything (single-stock mode).
    # ``--no-etf-filter`` fetches all SSE equities from the list endpoint.
    # Default: ETF-held SSE stocks via load_target_stocks (DB-backed).
    db_conn = None
    if code_filter:
        bare = code_filter.split(".")[0]
        stocks: List[Tuple[str, str]] = [(bare, "")]
        logger.info("Single-stock mode: %s", code_filter)
    elif etf_filter:
        # load_target_stocks lives in the archive module's __main__ namespace
        # — import lazily so the dividend module can be used without the
        # archive module's full import side-effects.
        from utils.db_commons import get_db_connection
        from downloads.stock.sse.archive.__main__ import load_target_stocks
        db_conn = get_db_connection()
        try:
            stocks = load_target_stocks(db_conn)
        except Exception:
            db_conn.close()
            db_conn = None
            raise
    else:
        # Reuse the list-endpoint fetcher from the archive module — it hits
        # yunhq.sse.com.cn which needs the full 20s cadence, so build a
        # separate dayk-style proxy for it.
        from downloads._common.core import DEFAULT_SLEEP_SEC
        list_proxy = AntiBotProxy(AntiBotConfig(
            base_sleep_sec=DEFAULT_SLEEP_SEC,
            sleep_jitter=0.3,
            rotate_browser_profile=True,
            add_random_param=True,
            enable_host_tracking=True,
            timeout=DEFAULT_TIMEOUT,
        ))
        from downloads.stock.sse.archive.__main__ import _fetch_all_stocks
        stocks = _fetch_all_stocks(sess, list_proxy)
    if not stocks:
        logger.error("No stocks found")
        if db_conn:
            db_conn.close()
        return {"downloaded": 0, "failed": 1, "archive_dir": str(archive_dir)}

    # Freshness check — skip stocks that are already up-to-date.
    # Checks BOTH: (a) local CSV dated within the last 6 months, (b) DB rows
    # with last_updated within the last 6 months. Skips the stock if either
    # is fresh (unless --force).
    if not force:
        fresh_local = 0
        fresh_db = 0
        filtered: List[Tuple[str, str]] = []
        for code, name in stocks:
            if _is_fresh_local(archive_dir, code):
                fresh_local += 1
                continue
            if db_conn and _is_fresh_db(db_conn, f"{code}.SS"):
                fresh_db += 1
                continue
            filtered.append((code, name))
        stocks = filtered
        logger.info(
            "Freshness check (%d-month window): %d stocks skipped (local CSV fresh), "
            "%d skipped (DB fresh), %d to process.",
            FRESHNESS_WINDOW_MONTHS, fresh_local, fresh_db, len(stocks),
        )
    if db_conn:
        db_conn.close()

    logger.info(
        "Starting SSE dividend download: %d stocks. "
        "Per-stock: fetch dividend history -> write {code}_dividend_%s.csv",
        len(stocks), today_suffix,
    )

    # Dividend endpoint (query.sse.com.cn) — quarterly cadence so we use the
    # long 90s sleep for maximum stealth. AntiBotProxy handles fingerprint
    # rotation, random param, sleep jitter, and host blocking detection.
    dividend_proxy = AntiBotProxy(AntiBotConfig(
        base_sleep_sec=sleep_sec,
        sleep_jitter=0.3,
        rotate_browser_profile=True,
        add_random_param=True,
        enable_host_tracking=True,
        timeout=DEFAULT_TIMEOUT,
    ))

    stocks_done = 0
    stocks_failed = 0
    rows_fetched = 0
    rows_empty = 0

    for idx, (code, name) in enumerate(stocks):
        if dividend_proxy.is_blocked(SSE_DIVIDEND_URL):
            logger.warning(
                "  [host-blocked] query.sse.com.cn blocked, stopping"
            )
            stocks_failed += len(stocks) - idx
            break

        if (idx + 1) % 25 == 0 or idx == 0:
            logger.info(
                "Stock %d/%d: %s %s", idx + 1, len(stocks), code, name,
            )

        out_file = archive_dir / f"{code}_dividend_{today_suffix}.csv"

        # Fetch all dividend rows (iterates board variants internally).
        raw_rows, board_label = _fetch_all_dividend_rows(
            sess, code, dividend_proxy
        )

        if not raw_rows:
            logger.info(
                "  -> %s dividend: no data (tried A_MAIN/A_STAR/B_MAIN)",
                code,
            )
            rows_empty += 1
            # Write an empty marker file so the freshness check skips it
            # next time. This avoids re-fetching stocks with no dividend history.
            if not out_file.exists():
                _write_dividend_csv(out_file, [])
            continue

        # Parse rows to the CSV schema.
        parsed_rows = [
            _parse_dividend_row(r, code) for r in raw_rows
        ]
        # Dedupe by 除息交易日 — the API occasionally returns duplicate
        # rows when a stock has both A and B boards; keep the first.
        seen_dates: set = set()
        deduped_rows: List[Dict[str, Any]] = []
        for r in parsed_rows:
            key = (r.get("除息交易日"), r.get("股权登记日"))
            if key in seen_dates:
                continue
            seen_dates.add(key)
            deduped_rows.append(r)

        _write_dividend_csv(out_file, deduped_rows)
        rows_fetched += len(deduped_rows)
        stocks_done += 1

        logger.info(
            "  -> %s dividend [%s]: %d rows -> %s",
            code, board_label, len(deduped_rows), out_file.name,
        )

    logger.info(
        "Done SSE dividend download. stocks_done=%d stocks_failed=%d "
        "rows_fetched=%d stocks_empty=%d archive_dir=%s",
        stocks_done, stocks_failed, rows_fetched, rows_empty, archive_dir,
    )
    return {
        "downloaded": stocks_done,
        "failed": stocks_failed,
        "rows_fetched": rows_fetched,
        "stocks_empty": rows_empty,
        "archive_dir": str(archive_dir),
        "total_stocks": len(stocks),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Download SSE per-stock dividend (利润分配/分红) data. "
                    "Anti-bot: AntiBotProxy with rotate_browser_profile, "
                    "add_random_param, enable_host_tracking, sleep_jitter=0.3, "
                    "base sleep LONG_SLEEP_INTERVAL (90s). "
                    "Output: {code}_dividend_{YYYYMMDD}.csv under temps/sse_archive/. "
                    "Skips stocks with a fresh local CSV or DB rows updated "
                    "within the last 6 months."
    )
    ap.add_argument(
        "--no-etf-filter", action="store_true",
        help="Disable the ETF filter and fetch ALL SSE equities from the "
             "SSE list endpoint instead.",
    )
    ap.add_argument(
        "--code", type=str, default=None,
        help="Single-stock mode: download dividend data for one stock "
             "(bare 6-digit code or suffixed). Overrides --no-etf-filter.",
    )
    ap.add_argument(
        "--force", action="store_true",
        help="Force download: ignore freshness checks (local CSV + DB "
             "last_updated) and re-download all stocks.",
    )
    args = ap.parse_args()
    print(download_sse_dividends(
        force=args.force,
        etf_filter=not args.no_etf_filter,
        code_filter=args.code,
    ))
