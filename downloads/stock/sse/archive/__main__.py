"""Download Shanghai Stock Exchange (SSE) HISTORICAL daily price data.

This module is the HISTORY/archive half of the former ``download_sse_price``.
It downloads full OHLCV history via the dayk endpoint. For TODAY's snapshot
(latest biz date), use ``download_sse_trend.py`` instead — it fetches the SSE
list endpoint (the same data source as ``stream_sse_price.py``).

The module's former PE half (静态市盈率 + 总换手率 via the 成交概况 endpoint,
``_run_pe_for_stock`` + the trade-stats fetch helpers) migrated to the trend
side on 2026-11-20: the weekly anchor crawl now lives in
``downloads.stock.sse.trend_pe`` and the daily latest-missing-tail sweep in
``downloads.stock.sse.pe_latest`` (both append to the same
``temps/sse_archive/{code}_pe.csv`` files this backfill seeded). Only the
ETF-held universe loader (``load_target_stocks``) and the list-endpoint
fetcher (``_fetch_all_stocks``) are still imported from here.

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

One output is produced per stock under ``temps/sse_archive/``:
  ``{code}_trend.csv`` — full OHLCV daily price history fetched from the
  dayk endpoint (yunhq.sse.com.cn), one row per trading day. Always
  (re)written when the stock's dayk data is fetched, so the archive
  reflects the latest API response.

Output columns (empty when the field is not published by SSE):
    交易日期,证券代码,证券简称,前收,开盘,最高,最低,今收,
    涨跌幅（%）,成交量(万股),成交金额(万元),市盈率

Resumable: existing ``{code}_trend.csv`` is read to skip already-fetched
data. ``--force`` re-fetches everything.

The entry point is the :class:`SseStockArchiveDownload` DownloadJob — the
former ``download_sse_archive`` body is its method (verbatim) and pure
helpers/constants stay module-level.
"""

from __future__ import annotations


import argparse
import csv
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from downloads._common import (
    DEFAULT_START_DATE,
    MIN_VALID_BYTES,
    AntiBotProxy,
    build_headers_with_referer,
    DownloadJob,
    is_valid_file,
    last_business_day,
    resolve_out_dir,
    service_urls,
    setup_logger,
)
from downloads._common.exchanges.sse import (
    CSV_ENCODING,
    JSONP_CALLBACK,
    LIST_SELECT_FIELDS,
    PAGE_SIZE,
    _fmt_num,
    _num,
    _parse_jsonp,
    _write_rows,
)
from _common.db_commons import get_db_connection


# --- History (dayk) constants ----------------------------------------------
SERVICE = "downloads.stock.sse.archive"

# Endpoint URLs — the service's external_request_config JSON row (urls
# object) is the single source; a missing role raises at the point of use.
URLS = service_urls(SERVICE)
SSE_DAYK_URL = URLS["sse_dayk_url"]
# yunhq list/dayk headers — the page referer comes from the row's "referer"
# role (the shared helper's old SSE_HEADERS constant).
SSE_HEADERS = build_headers_with_referer(URLS["referer"], extra={"Accept": "*/*"})
# Default start date for trend data (imported from _download_commons).
# The dayk endpoint returns the full listing history (some SSE stocks date
# back to 1999); by default we only keep data from 2020-01-01 onwards.
# Field tokens for dayk endpoint
DAYK_SELECT_FIELDS = "date,open,high,low,close,volume,amount,prev_close"
DAYK_MAX_DAYS = 10000  # Fetch up to 10000 trading days per stock

# Per-stock archive directory: one CSV per stock holding its full fetched
# history. Lives under temps/sse_archive/, sibling of the date-grouped
# temps/sse_trend/ output. The PE files ({code}_pe.csv) that the former PE
# half of this module seeded live in the SAME directory — trend_pe and
# pe_latest (their new owners) import this dirname constant from here.
ARCHIVE_DIRNAME = "sse_archive"

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
        URLS["list_equity"],
        params=params,
        headers=SSE_HEADERS,
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
        if proxy.is_blocked(URLS["list_equity"]):
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
    via ``stock_identity.exchange = 'SS'`` (instead of SZSE's ``exchange='SZ'``).
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
            "SELECT MAX(date) FROM stats.stock_identity WHERE exchange = 'SS'"
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
        # with stock_identity filtered to the latest date + exchange='SS'.
        # Ordered by importance rank 0 -> 9 (stats.sec_importance_ranks,
        # monthly build) then code: index-heavyweight stocks are archived
        # first so an interrupted sweep has covered the important codes
        # (LEFT JOIN: codes missing from the rank table sink to the tail).
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
              LEFT JOIN stats.sec_importance_ranks r ON r.code = si.code
             WHERE si.exchange = 'SS'
               AND si.date = %s
             ORDER BY r.importance_rank ASC NULLS LAST, si.code
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


class SseStockArchiveDownload(DownloadJob):
    """DownloadJob for the SSE HISTORICAL price archive fetch cycle.

    The former module-level ``download_sse_archive`` orchestrator became the
    method below; the pure parsers/fetch helpers and constants above stay
    module-level. The per-stock ``_run_pe_for_stock`` workflow moved to
    ``downloads.stock.sse.trend_pe`` (2026-11-20) — this backfill is
    OHLCV/dayk only now.
    """

    description = ("Download SSE HISTORICAL daily price history "
                   "(default: only stocks held by ETFs). "
                   "Per stock: trend (cached or API) -> {code}_trend.csv. "
                   "For TODAY's snapshot, use download_sse_trend.py instead; "
                   "for PE, downloads.stock.sse.trend_pe (weekly) / "
                   "pe_latest (nightly).")
    component = "sse_download"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--no-etf-filter", action="store_true",
            help="Disable the ETF filter and fetch ALL SSE equities "
                 "from the SSE list endpoint instead.",
        )
        parser.add_argument("--force", action="store_true",
                            help="Overwrite existing per-stock CSV files.")

    # ---------------------------------------------------------------------------
    # Main entry point
    # ---------------------------------------------------------------------------
    def download_sse_archive(
        self,
        out_root: Optional[str] = None,
        *,
        force: bool = False,
        session: Optional[requests.Session] = None,
        etf_filter: bool = True,
    ) -> dict:
        """Download SSE HISTORICAL daily price data (archive mode).

        Studies ``https://www.sse.com.cn/market/price/trends/`` 行情走势 and
        downloads the full date range available from the dayk slider — the entire
        trading history of each stock, not a user-specified year range.

        Per-stock workflow: fetch the full OHLCV daily history from the dayk
        endpoint (yunhq.sse.com.cn) and write ``{code}_trend.csv``. (The
        former per-stock PE step moved to downloads.stock.sse.trend_pe.)

        Output lives under ``temps/sse_archive/``. Resumable: an existing
        ``{code}_trend.csv`` covering the skip threshold is reused unless
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

        Former module-level orchestrator — moved verbatim into the job class;
        ``run()`` calls it and logs the returned summary dict.
        """
        archive_dir = resolve_out_dir(
            str(Path(__file__).resolve()), ARCHIVE_DIRNAME, out_root
        )
        sess = session or requests.Session()

        today = date.today()
        # Calculate the expected latest trading day: today if it's a trading day,
        # otherwise the most recent previous trading day. Used as the skip-check
        # fallback when the sse_trend directory is empty/missing.
        expected_latest_biz_date = last_business_day(today)

        # Find the earliest date from sse_trend directory to determine the start of
        # daily snapshot tracking. The archive only needs to cover from DEFAULT_START_DATE
        # (2020-01-01) up to this earliest date — stocks with archive data already
        # covering this range can be skipped.
        trend_dir = resolve_out_dir(
            str(Path(__file__).resolve()), "sse_trend", out_root
        )
        earliest_trend_date = _find_earliest_biz_date_from_trend_dir(trend_dir)
        # Fall back to expected_latest_biz_date if sse_trend dir is empty/missing
        skip_check_date = earliest_trend_date or expected_latest_biz_date
        skip_check_str = skip_check_date.isoformat()

        logger.info("Today: %s, expected latest trading day: %s", today, expected_latest_biz_date)
        if earliest_trend_date:
            logger.info("Earliest biz date from sse_trend dir: %s", earliest_trend_date)
            logger.info("Archive skip threshold: %s (covers %s to %s)", skip_check_str, DEFAULT_START_DATE, skip_check_str)

        # --- Pre-scan existing archive files FIRST before DB/API ---
        # This is critical for idempotency: only fetch for stocks that actually
        # need updating. A stock is skipped when its {code}_trend.csv covers
        # up to skip_check_str (earliest sse_trend date).
        up_to_date_stocks: set = set()
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
                        up_to_date_stocks.add(code)
                except Exception:
                    continue
            logger.info(
                "Pre-scanned archive: %d stocks already up-to-date (skip them)",
                len(up_to_date_stocks),
            )

        # dayk + list endpoints (yunhq.sse.com.cn) run on the service's
        # "dayk" external_request_config row.
        dayk_proxy = AntiBotProxy(service=SERVICE, request_type="dayk")

        # Build the target stock list. When etf_filter=False, _fetch_all_stocks
        # hits yunhq.sse.com.cn on the dayk proxy's pacing row.
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
            "trend (cached or API) -> next stock",
            len(stocks),
        )

        stocks_done = 0
        stocks_failed = 0
        trend_rows_written = 0

        # ----- Per-stock workflow: trend (cached or API) -> next stock -----
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
                    # canonicalize 证券代码 -> "NNNNNN.SS" + exchange/board/sec_type cols
                    from downloads._common import ensure_canonical_csv
                    ensure_canonical_csv(trend_file, "SS", sec_type="stock")
                    trend_rows_written += len(ohlcv_rows)
                    logger.info(
                        "  -> %s trend: %d rows -> %s",
                        code, len(ohlcv_rows), trend_file.name,
                    )
                else:
                    logger.info("  -> %s trend: no dayk data", code)
                    continue  # no OHLCV -> cannot compute PE dates

            stocks_done += 1

        logger.info(
            "Per-stock workflow done: %d/%d stocks done, %d failed, "
            "%d trend rows written",
            stocks_done, len(stocks), stocks_failed, trend_rows_written,
        )

        logger.info(
            "Done SSE archive. stocks_done=%d stocks_failed=%d "
            "trend_rows=%d archive_dir=%s",
            stocks_done, stocks_failed, trend_rows_written, archive_dir,
        )
        return {
            "downloaded": stocks_done,
            "failed": stocks_failed,
            "trend_rows_written": trend_rows_written,
            "archive_dir": str(archive_dir),
            "total_stocks": len(stocks),
        }

    def run(self) -> None:
        """One fetch cycle (the former __main__ body verbatim)."""
        logger.info(self.download_sse_archive(force=self.args.force, etf_filter=not self.args.no_etf_filter))


if __name__ == "__main__":
    SseStockArchiveDownload().execute()
