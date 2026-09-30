"""Download Beijing Stock Exchange (BSE) HISTORICAL daily price archive.

This module is the HISTORY/archive half of ``downloads.stock.bse.price``.
For TODAY's snapshot (``temps/bse_trend/bse_trend_stock_<date>.csv``) run
``downloads.stock.bse.price`` instead.

Source studied from the BSE site: the quotation page
(https://www.bse.cn/nq/quotation.html) lists every listed stock, and
clicking into an individual item opens the company detail page
(/products/neeq_listed_companies/company_time_sharing.html?companyCode=...).
That page's 日K chart is fed by
``GET https://www.bse.cn/companyEchartsController/getKLine/list/{code}.do``
with ``begin=0&end=-1&type=dayKline&xxfcbj=2`` — one request per stock
returning its FULL daily OHLCV history since listing (see
``downloads/_common/exchanges/bjs.py`` for the endpoint study).

Output: one CSV per stock under ``temps/bse_archive/``:
  ``{code}_trend.csv`` — unified trend schema (same columns as
  bse_trend_stock / sse_trend_stock), 证券代码 canonicalized to
  ``NNNNNN.BJ`` (+ exchange/board/sec_type columns via ensure_canonical_csv).
  市盈率 stays blank: the kline endpoint does not publish it (the daily
  price snapshot carries PE for current dates).

Resumable: a stock is skipped when its ``{code}_trend.csv`` is valid and
already covers the archive skip threshold — the earliest date found in
``temps/bse_trend/`` (where daily snapshots accumulate), falling back to
the last business day. ``--force`` re-fetches every stock.

Anti-bot: bse.cn fronts every endpoint with a WAF cookie challenge (302 +
Set-Cookie ``C3VK``, ``Ws-Action: cc``); the shared ``requests.Session``
passes it transparently. The per-stock kline sweep hits one endpoint per
individual item (~350 requests), so it runs on the project's LONG_SLEEP_INTERVAL
(90s) cadence — a full backfill takes ~9h. The quotation-list pagination
(~18 requests) uses the same 5s cadence as the daily price downloader,
which has run against that endpoint unblocked.
"""
from __future__ import annotations


import argparse
import csv
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from downloads._common import (
    DEFAULT_START_DATE,
    DEFAULT_TIMEOUT,
    LONG_SLEEP_INTERVAL,
    MIN_VALID_BYTES,
    AntiBotConfig,
    AntiBotProxy,
    ensure_canonical_csv,
    is_valid_file,
    last_business_day,
    resolve_out_dir,
    setup_logger,
)
from downloads._common.exchanges.bjs import (
    BSE_KLINE_URL,
    COLUMNS,
    CSV_ENCODING,
    _write_page,
    fetch_bse_kline,
    fetch_bse_stock_list,
    parse_bse_kline_row,
)

logger = setup_logger("bse_archive")

# Per-stock archive directory, sibling of temps/bse_trend/ (mirrors
# temps/sse_archive/ next to temps/sse_trend/).
ARCHIVE_DIRNAME = "bse_archive"
TREND_DIRNAME = "bse_trend"

# Quotation-list pagination cadence — same 5s the daily price downloader
# uses against the same endpoint.
LIST_SLEEP_SEC = 5.0


def _find_earliest_trend_date(trend_dir: Path) -> Optional[date]:
    """Earliest ``bse_trend_stock_{YYYYMMDD}.csv`` date in the trend dir.

    The archive only needs to cover the historical gap from
    DEFAULT_START_DATE up to this date — daily snapshots cover the rest.
    Returns None when the directory is empty or missing.
    """
    if not trend_dir.exists():
        return None
    dates: List[date] = []
    for f in trend_dir.glob("bse_trend_stock_*.csv"):
        ymd = f.stem.rsplit("_", 1)[-1]
        if len(ymd) == 8 and ymd.isdigit():
            try:
                dates.append(date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8])))
            except ValueError:
                continue
    return min(dates) if dates else None


def _archive_file_covers(trend_file: Path, threshold: str) -> bool:
    """True when ``trend_file`` is valid and its max 交易日期 >= threshold."""
    if not is_valid_file(trend_file, min_bytes=MIN_VALID_BYTES):
        return False
    try:
        with open(trend_file, encoding=CSV_ENCODING, newline="") as f:
            dates = [
                (row.get("交易日期") or "").strip()
                for row in csv.DictReader(f)
            ]
    except (OSError, csv.Error):
        return False
    dates = [d for d in dates if d]
    return bool(dates) and max(dates) >= threshold


def download_bse_archive(
    out_root: Optional[str] = None,
    *,
    force: bool = False,
    limit: Optional[int] = None,
    session: Optional[requests.Session] = None,
) -> dict:
    """Download the BSE per-stock historical daily archive.

    Walks the quotation list, then "clicks into" every individual item by
    calling the company detail page's kline endpoint once per stock and
    writing ``temps/bse_archive/{code}_trend.csv`` (full history since
    listing, rows filtered to >= DEFAULT_START_DATE).

    ``limit`` caps the number of stocks fetched (testing only); ``force``
    re-fetches stocks whose archive file already covers the threshold.
    """
    archive_dir = resolve_out_dir(__file__, ARCHIVE_DIRNAME, out_root)
    trend_dir = resolve_out_dir(__file__, TREND_DIRNAME, out_root)
    sess = session or requests.Session()

    threshold = _find_earliest_trend_date(trend_dir) or last_business_day(date.today())
    threshold_str = threshold.isoformat()
    logger.info(
        "Archive skip threshold: %s (covers %s .. %s; daily bse_trend covers the rest)",
        threshold_str, DEFAULT_START_DATE, threshold_str,
    )

    list_proxy = AntiBotProxy(AntiBotConfig(
        rotate_browser_profile=False,
        base_sleep_sec=LIST_SLEEP_SEC,
        enable_host_tracking=True,
        timeout=DEFAULT_TIMEOUT,
    ))
    stocks = fetch_bse_stock_list(sess, proxy=list_proxy)
    if not stocks:
        logger.error("No BSE stocks found")
        return {"downloaded": 0, "failed": 1, "archive_dir": str(archive_dir)}

    # Pre-scan: skip stocks whose archive file already covers the threshold.
    skip_codes: set = set()
    if not force:
        for code, _name in stocks:
            trend_file = archive_dir / f"{code}_trend.csv"
            if _archive_file_covers(trend_file, threshold_str):
                skip_codes.add(code)
        if skip_codes:
            logger.info(
                "Pre-scanned archive: %d/%d stocks already cover %s, skipping",
                len(skip_codes), len(stocks), threshold_str,
            )

    # Per-stock kline sweep — one endpoint hit per individual item, so use
    # the project's long anti-bot cadence (LONG_SLEEP_INTERVAL, 90s).
    kline_proxy = AntiBotProxy(AntiBotConfig(
        rotate_browser_profile=False,
        base_sleep_sec=LONG_SLEEP_INTERVAL,
        sleep_jitter=0.3,
        enable_host_tracking=True,
        timeout=DEFAULT_TIMEOUT,
    ))

    downloaded = 0
    skipped = 0
    failed = 0
    fetched = 0
    rows_written = 0
    for code, name in stocks:
        if code in skip_codes:
            skipped += 1
            continue
        if limit is not None and fetched >= limit:
            break
        if kline_proxy.is_blocked(BSE_KLINE_URL.format(code=code)):
            logger.warning("[host-blocked] bse.cn blocked, stopping kline sweep")
            break

        # Auto-sleep handled by proxy.get() inside fetch_bse_kline.
        records = fetch_bse_kline(sess, code, proxy=kline_proxy)
        fetched += 1
        if records is None:
            logger.error("kline fetch failed for %s, skipping", code)
            failed += 1
            continue

        canonical = f"{code}.BJ"
        rows = [
            row
            for row in (
                parse_bse_kline_row(rec, canonical, name) for rec in records
            )
            if row is not None and row["交易日期"] >= DEFAULT_START_DATE
        ]
        if not rows:
            logger.info("-> %s %s: no kline rows", code, name)
            failed += 1
            continue

        trend_file = archive_dir / f"{code}_trend.csv"
        _write_page(trend_file, rows, write_header=True)
        # canonicalize 证券代码 -> "NNNNNN.BJ" + exchange/board/sec_type columns
        # so build loaders can filter by plain column equality (no regex)
        ensure_canonical_csv(trend_file, "BJ", sec_type="stock", logger=logger)
        downloaded += 1
        rows_written += len(rows)
        logger.info(
            "-> %s %s: %d rows (%s .. %s) -> %s",
            code, name, len(rows), rows[0]["交易日期"], rows[-1]["交易日期"],
            trend_file.name,
        )

    logger.info(
        "Done BSE archive. downloaded=%d failed=%d skipped=%d fetched=%d "
        "rows=%d archive_dir=%s",
        downloaded, failed, skipped, fetched, rows_written, archive_dir,
    )
    return {
        "downloaded": downloaded,
        "failed": failed,
        "skipped": skipped,
        "fetched": fetched,
        "rows_written": rows_written,
        "total_stocks": len(stocks),
        "archive_dir": str(archive_dir),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Download BSE HISTORICAL per-stock daily price archive "
                    "(kline endpoint behind the company detail page, one "
                    "request per stock at the 90s long-sleep cadence). "
                    "For TODAY's snapshot, use downloads.stock.bse.price."
    )
    ap.add_argument("--force", action="store_true",
                    help="Re-fetch stocks whose archive file already covers "
                         "the skip threshold.")
    ap.add_argument("--limit", type=int, default=None,
                    help="Fetch at most N stocks (testing only).")
    args = ap.parse_args()
    logger.info(download_bse_archive(force=args.force, limit=args.limit))
