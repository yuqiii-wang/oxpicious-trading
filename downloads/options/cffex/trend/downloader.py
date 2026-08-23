"""downloads.options.cffex.trend.downloader — Playwright-based CFFEX options downloader.

Downloads daily options data from the CFFEX "日统计" page
(http://www.cffex.com.cn/cn/rtj.html) using Playwright browser automation.

Browser lifecycle, anti-bot fingerprint rotation and DOM/CSV helpers come
from the shared module _common.playwright (which reuses the anti-bot
policy from downloads._common.core).

Workflow for each date:
  1. Launch a Playwright browser via _common.playwright.playwright_session
  2. Navigate to the trend page
  3. Select "期权" (Options) radio button
  4. Set the target date and click 查询 (Query)
  5. Try downloading the combined CSV via the page's 日行情数据 link,
     filtering to options contracts; fall back to HTML table scraping
  6. Save to temps/cffex_options_trend/YYYYMM/YYYYMMDD_options.csv

The CSV format matches the archive downloader:
  - Same columns: 合约代码, 今开盘, 最高价, 最低价, 成交量, 成交金额,
    持仓量, 持仓变化, 今收盘, 今结算, 前结算, 涨跌1, 涨跌2, Delta
  - Same encoding: UTF-8-sig (with BOM)
  - Only options rows (contract code contains -C- or -P-)
"""

from __future__ import annotations

import csv as csv_mod
import random
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import List, Optional, Set, Tuple

from playwright.sync_api import Page

from _common.playwright import (
    BrowserConfig,
    download_csv_via_link,
    extract_table_rows,
    parse_csv_rows,
    playwright_session,
    sleep_between_requests,
    wait_for_table_data,
)
from downloads._common.core import setup_logger
from downloads.options.cffex.trend.config import (
    BROWSER_TYPE,
    CFFEX_BASE_ORIGIN,
    CFFEX_TREND_URL,
    CSV_HEADERS,
    DATA_LOAD_TIMEOUT,
    DOWNLOAD_SLEEP_SEC,
    HEADLESS,
    OUTPUT_CSV_ENCODING,
    PAGE_NAVIGATION_TIMEOUT,
    SLEEP_JIT_RANGE,
    VIEWPORT_HEIGHT,
    VIEWPORT_WIDTH,
    _format_date_for_filename,
    _format_date_for_input,
)
from downloads.options.cffex.trend.paths import (
    get_trend_month_dir,
    trend_options_csv_path,
)

logger = setup_logger("cffex_options_trend")


# ---------------------------------------------------------------------------
# CFFEX-specific constants
# ---------------------------------------------------------------------------

# Columns that contain numeric data (indices in the CSV row)
# 0:合约代码, 1:今开盘, 2:最高价, 3:最低价, 4:成交量, 5:成交金额,
# 6:持仓量, 7:持仓变化, 8:今收盘, 9:今结算, 10:前结算, 11:涨跌1, 12:涨跌2, 13:Delta
_NUMERIC_COL_INDICES: Set[int] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13}

# Text shown by CFFEX when the queried date has no data
_NO_DATA_TEXT = "没有您所查询的数据"

# Link texts that expose the combined daily CSV download
_CSV_LINK_TEXTS: Tuple[str, ...] = ("日行情数据", "下载数据")

# Shared browser configuration (fingerprint rotation via the anti-bot module)
BROWSER_CONFIG = BrowserConfig(
    browser_type=BROWSER_TYPE,
    headless=HEADLESS,
    viewport_width=VIEWPORT_WIDTH,
    viewport_height=VIEWPORT_HEIGHT,
    locale="zh-CN",
    default_timeout_ms=PAGE_NAVIGATION_TIMEOUT,
    default_navigation_timeout_ms=PAGE_NAVIGATION_TIMEOUT,
)


# ---------------------------------------------------------------------------
# Options filtering and CSV writing
# ---------------------------------------------------------------------------

def _is_option_contract(contract_code: str) -> bool:
    """Check if a contract code represents an options contract.

    Options have format: PREFIX + YYYYMM + '-' + C|P + '-' + STRIKE
    e.g. HO2607-C-2500, IO2607-P-4000, MO2703-C-8400
    """
    return "-C-" in contract_code or "-P-" in contract_code


def _is_summary_row(contract_code: str) -> bool:
    """Check if a row is a summary/subtotal row (not real data)."""
    return contract_code in ("小计", "合计")


def _filter_to_options(raw_rows: List[List[str]]) -> List[List[str]]:
    """Filter raw table rows to only options contracts.

    Removes summary rows (小计, 合计) and futures contracts (no -C- or -P-).

    Returns:
        List of rows containing only options contracts (excluding header).
    """
    options_rows: List[List[str]] = []
    for row in raw_rows:
        if not row:
            continue
        contract = row[0].strip() if row else ""
        if not contract or _is_summary_row(contract):
            continue
        if _is_option_contract(contract):
            options_rows.append(row)
    return options_rows


def _write_options_csv(
    raw_rows: List[List[str]],
    output_dir: Path,
    date_str: str,
) -> Optional[Path]:
    """Write options-only CSV file from raw table rows.

    Args:
        raw_rows: List of rows from the table (first row is header).
        output_dir: Directory to write CSV file.
        date_str: Date string for filenames (YYYYMMDD).

    Returns:
        Path to the written CSV file, or None if no options data.
    """
    options_rows = _filter_to_options(raw_rows)
    if not options_rows:
        logger.info("  %s: no options contracts found", date_str)
        return None

    return _write_filtered_csv(options_rows, output_dir, date_str)


def _write_filtered_csv(
    options_rows: List[List[str]],
    output_dir: Path,
    date_str: str,
) -> Optional[Path]:
    """Write pre-filtered options rows to CSV.

    Args:
        options_rows: List of option contract rows (already filtered).
        output_dir: Directory to write CSV file.
        date_str: Date string for filenames (YYYYMMDD).

    Returns:
        Path to the written CSV file, or None if empty.
    """
    if not options_rows:
        logger.info("  %s: no options contracts found", date_str)
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    options_path = output_dir / f"{date_str}_options.csv"

    with open(options_path, "w", encoding=OUTPUT_CSV_ENCODING, newline="") as f:
        writer = csv_mod.writer(f)
        writer.writerow(CSV_HEADERS)
        writer.writerows(options_rows)

    logger.info(
        "  Saved %s: %d options rows → %s",
        date_str, len(options_rows), options_path.name,
    )
    return options_path


# ---------------------------------------------------------------------------
# Page interaction (CFFEX-specific)
# ---------------------------------------------------------------------------

def _navigate_to_trend_page(page: Page) -> None:
    """Navigate to the CFFEX trend page and wait for it to load."""
    page.goto(CFFEX_TREND_URL, wait_until="domcontentloaded")
    page.wait_for_selector("#actualDate", timeout=PAGE_NAVIGATION_TIMEOUT)
    page.wait_for_selector("button.btn-query", timeout=5000)


def _query_date(page: Page, target_date: date) -> bool:
    """Query options data for a specific date on the trend page.

    Args:
        page: Playwright page.
        target_date: Date to query.

    Returns:
        True if data was loaded successfully, False otherwise.
    """
    date_str = _format_date_for_input(target_date)

    try:
        page.evaluate("""
            () => {
                const radios = document.querySelectorAll('input[name="radio"]');
                for (const r of radios) {
                    if (r.value === '期权') {
                        r.checked = true;
                        r.dispatchEvent(new Event('change', {bubbles: true}));
                        r.dispatchEvent(new Event('click', {bubbles: true}));
                    }
                }
            }
        """)
    except Exception as e:
        logger.warning("Failed to select options radio: %s", e)

    try:
        page.evaluate(f"""
            () => {{
                const dateInput = document.getElementById('actualDate');
                if (dateInput) {{
                    dateInput.value = '{date_str}';
                    dateInput.dispatchEvent(new Event('input', {{bubbles: true}}));
                    dateInput.dispatchEvent(new Event('change', {{bubbles: true}}));
                }}
            }}
        """)
    except Exception as e:
        logger.warning("Failed to set date %s: %s", date_str, e)
        return False

    try:
        page.evaluate("""
            () => {
                const btn = document.querySelector('button.btn-query');
                if (btn) {
                    btn.click();
                    return true;
                }
                const buttons = document.querySelectorAll('button');
                for (const b of buttons) {
                    if (b.textContent.trim() === '查询') {
                        b.click();
                        return true;
                    }
                }
                return false;
            }
        """)
    except Exception as e:
        logger.warning("Failed to click query button: %s", e)
        return False

    if not wait_for_table_data(
        page,
        timeout_ms=DATA_LOAD_TIMEOUT,
        no_data_text=_NO_DATA_TEXT,
    ):
        logger.info("No data found for %s (may be holiday/weekend)", date_str)
        return False

    return True


def _save_options_data(
    page: Page,
    output_dir: Path,
    date_str: str,
) -> Optional[Path]:
    """Save options data for a queried date to CSV.

    Strategy 1: download the combined daily CSV via the page's
    日行情数据 link and filter it to options rows.
    Strategy 2 (fallback): scrape the HTML table.

    Returns the written options CSV path, or None if no data.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        csv_path = download_csv_via_link(
            page,
            Path(tmp_dir),
            base_origin=CFFEX_BASE_ORIGIN,
            link_texts=_CSV_LINK_TEXTS,
            relative_prefix="/cn/",
            log=logger,
        )
        if csv_path:
            csv_rows = parse_csv_rows(
                csv_path, numeric_col_indices=_NUMERIC_COL_INDICES,
            )
            if csv_rows and len(csv_rows) >= 2:
                written = _write_filtered_csv(
                    _filter_to_options(csv_rows), output_dir, date_str,
                )
                if written:
                    return written
                logger.info("  CSV download had no options data, trying HTML...")

    table_data = extract_table_rows(
        page, numeric_col_indices=_NUMERIC_COL_INDICES,
    )
    if not table_data or len(table_data) < 2:
        logger.warning("  No table data for %s", date_str)
        return None

    return _write_options_csv(table_data, output_dir, date_str)


# ---------------------------------------------------------------------------
# Main download functions
# ---------------------------------------------------------------------------

def download_one_day(
    target_date: date,
    out_root: Optional[str] = None,
    page: Optional[Page] = None,
) -> Optional[Path]:
    """Download options trend data for a single day.

    Tries CSV download first (more reliable), falls back to HTML scraping.

    Args:
        target_date: Trading date to download.
        out_root: Optional override for output directory.
        page: Optional pre-existing Playwright page (for batch operations).

    Returns:
        Path to the options CSV file, or None if no data.
    """
    date_str = _format_date_for_filename(target_date)
    output_dir = get_trend_month_dir(date_str[:6], out_root)
    output_dir.mkdir(parents=True, exist_ok=True)

    options_path = trend_options_csv_path(target_date, out_root)

    if options_path.exists() and options_path.stat().st_size > 100:
        logger.info("  %s already exists, skipping", date_str)
        return options_path

    if page is not None:
        return _download_with_page(page, target_date, output_dir, date_str)

    with playwright_session(BROWSER_CONFIG) as (_browser, _context, own_page):
        return _download_with_page(own_page, target_date, output_dir, date_str)


def _download_with_page(
    page: Page,
    target_date: date,
    output_dir: Path,
    date_str: str,
) -> Optional[Path]:
    """Navigate, query and save one date using an existing page."""
    try:
        _navigate_to_trend_page(page)
        time.sleep(2)

        if not _query_date(page, target_date):
            logger.info("  No data for %s", date_str)
            return None

        return _save_options_data(page, output_dir, date_str)
    except Exception as e:
        logger.error("  Download failed for %s: %s", date_str, e)
        return None


def download_trend_batch(
    dates: List[date],
    out_root: Optional[str] = None,
    sleep_sec: float = DOWNLOAD_SLEEP_SEC,
) -> dict:
    """Download options trend data for a batch of dates using a single browser session.

    Tries CSV download first (more reliable), falls back to HTML scraping.

    Args:
        dates: List of dates to download.
        out_root: Optional override for output directory.
        sleep_sec: Base sleep seconds between downloads.

    Returns:
        Summary dict with counts.
    """
    result = {
        "downloaded": 0,
        "skipped": 0,
        "failed": 0,
        "no_data": 0,
    }

    with playwright_session(BROWSER_CONFIG) as (_browser, _context, page):
        for i, target_date in enumerate(dates):
            date_str = _format_date_for_filename(target_date)
            options_path = trend_options_csv_path(target_date, out_root)

            if options_path.exists() and options_path.stat().st_size > 100:
                logger.info(
                    "[%d/%d] %s already exists, skipping",
                    i + 1, len(dates), date_str,
                )
                result["skipped"] += 1
                continue

            logger.info("[%d/%d] Downloading %s ...", i + 1, len(dates), date_str)

            try:
                _navigate_to_trend_page(page)
                time.sleep(1 + random.uniform(0, 2))

                if not _query_date(page, target_date):
                    logger.info("  No data for %s", date_str)
                    result["no_data"] += 1
                    continue

                output_dir = get_trend_month_dir(date_str[:6], out_root)
                output_dir.mkdir(parents=True, exist_ok=True)

                written = _save_options_data(page, output_dir, date_str)
                if written:
                    result["downloaded"] += 1
                else:
                    result["no_data"] += 1

            except Exception as e:
                logger.error("  Failed for %s: %s", date_str, e)
                result["failed"] += 1

            if i < len(dates) - 1:
                sleep_between_requests(sleep_sec, SLEEP_JIT_RANGE, log=logger)

    return result
