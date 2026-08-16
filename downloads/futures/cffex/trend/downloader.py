"""downloads.futures.cffex.trend.downloader — Playwright-based CFFEX trend downloader.

Downloads daily futures/options data from the CFFEX "日统计" page
(http://www.cffex.com.cn/cn/rtj.html) using Playwright browser automation.

Workflow for each date:
  1. Launch Playwright Chromium browser (headless)
  2. Navigate to the trend page
  3. Select "期货" (Futures) radio button
  4. Set the target date
  5. Click the 查询 (Query) button
  6. Wait for the data table to load
  7. Extract table data from DOM
  8. Split into futures and options CSVs
  9. Save to temps/cffex_trend/YYYYMM/

The CSV format matches the archive downloader:
  - Same columns: 合约代码, 今开盘, 最高价, 最低价, 成交量, 成交金额,
    持仓量, 持仓变化, 今收盘, 今结算, 前结算, 涨跌1, 涨跌2, Delta
  - Same encoding: UTF-8-sig (with BOM)
  - Same split logic: futures (no option marker) vs options (-C- or -P-)
"""

from __future__ import annotations

import csv as csv_mod
import io
import random
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    sync_playwright,
)

from downloads._common.core import (
    DEFAULT_USER_AGENT,
    setup_logger,
)
from downloads.futures.cffex.trend.config import (
    BROWSER_TYPE,
    CSV_HEADERS,
    CFFEX_TREND_URL,
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
from downloads.futures.cffex.trend.paths import (
    get_trend_month_dir,
    trend_futures_csv_path,
    trend_options_csv_path,
)

logger = setup_logger("cffex_trend")


# ---------------------------------------------------------------------------
# Table extraction from DOM
# ---------------------------------------------------------------------------

# Columns that contain numeric data (indices in the CSV row)
# 0:合约代码, 1:今开盘, 2:最高价, 3:最低价, 4:成交量, 5:成交金额,
# 6:持仓量, 7:持仓变化, 8:今收盘, 9:今结算, 10:前结算, 11:涨跌1, 12:涨跌2, 13:Delta
_NUMERIC_COL_INDICES = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13}

# Tokens that should map to empty string (NULL)
_NULL_TOKENS = {"", "--", "-", "—", "null", "NULL", "None", "nan", "NaN"}


def _clean_numeric_value(value: str) -> str:
    """Clean a numeric string from the CFFEX trend page.

    CFFEX displays numbers with thousand-separator commas (e.g., "4,670.0",
    "18,693"). This function removes commas and handles null tokens.

    Returns cleaned string suitable for numeric parsing.
    """
    v = value.strip()
    if v in _NULL_TOKENS:
        return ""
    # Remove thousand-separator commas
    v = v.replace(",", "")
    return v


def _extract_table_data(page: Page) -> Optional[List[List[str]]]:
    """Extract the data table from the trend page.

    Returns list of rows (each row is a list of strings), or None if
    no data table is found. Numeric values have commas removed.
    """
    try:
        raw_rows = page.evaluate("""
            () => {
                const table = document.querySelector('table');
                if (!table) return null;
                const trs = table.querySelectorAll('tr');
                const result = [];
                for (const tr of trs) {
                    const cells = tr.querySelectorAll('td');
                    if (cells.length > 0) {
                        const row = [];
                        for (const td of cells) {
                            row.push(td.textContent.trim());
                        }
                        result.push(row);
                    }
                }
                return result.length > 0 ? result : null;
            }
        """)
        if raw_rows is None:
            return None

        # Clean numeric values (remove commas) in data rows (skip header)
        cleaned_rows: List[List[str]] = []
        for i, row in enumerate(raw_rows):
            if i == 0:
                # Header row: keep as-is
                cleaned_rows.append(row)
            else:
                cleaned_row = list(row)
                for idx in _NUMERIC_COL_INDICES:
                    if idx < len(cleaned_row):
                        cleaned_row[idx] = _clean_numeric_value(cleaned_row[idx])
                cleaned_rows.append(cleaned_row)

        return cleaned_rows
    except Exception as e:
        logger.warning("Table extraction failed: %s", e)
        return None


def _has_data(page: Page) -> bool:
    """Check if the page has data (not '没有您所查询的数据')."""
    try:
        text = page.evaluate(
            "() => document.body.innerText.includes('没有您所查询的数据')"
        )
        if text:
            return False
        # Also check if there are actual data rows (not just summary)
        has_rows = page.evaluate("""
            () => {
                const table = document.querySelector('table');
                if (!table) return false;
                const rows = table.querySelectorAll('tr');
                // First row is header; we need at least 1 data row
                return rows.length > 1;
            }
        """)
        return has_rows
    except Exception:
        return False


def _wait_for_data(page: Page, timeout_ms: int = DATA_LOAD_TIMEOUT) -> bool:
    """Wait for data to appear on the page.

    Returns True if data loaded successfully, False if timeout or no data.
    """
    deadline = time.time() + (timeout_ms / 1000)
    while time.time() < deadline:
        if _has_data(page):
            return True
        time.sleep(0.5)
    return False


# ---------------------------------------------------------------------------
# CSV writing and splitting
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


def _split_csv_futures_options(
    raw_rows: List[List[str]],
    output_dir: Path,
    date_str: str,
) -> Tuple[Path, Path]:
    """Split raw table rows into futures and options CSVs.

    Args:
        raw_rows: List of rows from the table (first row is header).
        output_dir: Directory to write CSV files.
        date_str: Date string for filenames (YYYYMMDD).

    Returns:
        (futures_csv_path, options_csv_path)
    """
    futures_path = output_dir / f"{date_str}_futures.csv"
    options_path = output_dir / f"{date_str}_options.csv"

    futures_rows: List[List[str]] = []
    options_rows: List[List[str]] = []

    # Skip header row (first row), process data rows
    for row in raw_rows[1:]:
        if not row:
            continue
        contract = row[0].strip() if row else ""
        if not contract or _is_summary_row(contract):
            continue
        if _is_option_contract(contract):
            options_rows.append(row)
        else:
            futures_rows.append(row)

    def _write_csv(path: Path, header: List[str], data: List[List[str]]) -> None:
        with open(path, "w", encoding=OUTPUT_CSV_ENCODING, newline="") as f:
            writer = csv_mod.writer(f)
            writer.writerow(header)
            writer.writerows(data)

    _write_csv(futures_path, CSV_HEADERS, futures_rows)
    _write_csv(options_path, CSV_HEADERS, options_rows)

    logger.info(
        "  Split %s: %d futures, %d options",
        date_str, len(futures_rows), len(options_rows),
    )

    return futures_path, options_path


def _write_combined_csv(
    raw_rows: List[List[str]],
    output_dir: Path,
    date_str: str,
) -> Path:
    """Write the combined CSV (before splitting).

    The combined CSV is saved as YYYYMMDD_1.csv for reference.
    """
    combined_path = output_dir / f"{date_str}_1.csv"
    with open(combined_path, "w", encoding=OUTPUT_CSV_ENCODING, newline="") as f:
        writer = csv_mod.writer(f)
        for row in raw_rows:
            writer.writerow(row)
    return combined_path


# ---------------------------------------------------------------------------
# Playwright browser management
# ---------------------------------------------------------------------------

def _launch_browser(playwright: Playwright) -> Tuple[Browser, BrowserContext, Page]:
    """Launch a Playwright browser with appropriate settings.

    Returns (browser, context, page).
    """
    browser = playwright.chromium.launch(
        headless=HEADLESS,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ],
    )
    context = browser.new_context(
        viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
        user_agent=DEFAULT_USER_AGENT,
        locale="zh-CN",
        extra_http_headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    page = context.new_page()
    page.set_default_timeout(PAGE_NAVIGATION_TIMEOUT)
    page.set_default_navigation_timeout(PAGE_NAVIGATION_TIMEOUT)

    # Remove webdriver flag to avoid bot detection
    page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
    """)

    return browser, context, page


def _navigate_to_trend_page(page: Page) -> None:
    """Navigate to the CFFEX trend page and wait for it to load."""
    page.goto(CFFEX_TREND_URL, wait_until="domcontentloaded")
    # Wait for the key elements to be present
    page.wait_for_selector("#actualDate", timeout=PAGE_NAVIGATION_TIMEOUT)
    page.wait_for_selector("button.btn-query", timeout=5000)


def _query_date(page: Page, target_date: date) -> bool:
    """Query data for a specific date on the trend page.

    Args:
        page: Playwright page.
        target_date: Date to query.

    Returns:
        True if data was loaded successfully, False otherwise.
    """
    date_str = _format_date_for_input(target_date)

    # Step 1: Make sure futures radio is selected
    try:
        page.evaluate("""
            () => {
                const radios = document.querySelectorAll('input[name="radio"]');
                for (const r of radios) {
                    if (r.value === '期货') {
                        r.checked = true;
                        r.dispatchEvent(new Event('change', {bubbles: true}));
                        r.dispatchEvent(new Event('click', {bubbles: true}));
                    }
                }
            }
        """)
    except Exception as e:
        logger.warning("Failed to select futures radio: %s", e)

    # Step 2: Set the date
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

    # Step 3: Click the query button
    try:
        page.evaluate("""
            () => {
                const btn = document.querySelector('button.btn-query');
                if (btn) {
                    btn.click();
                    return true;
                }
                // Fallback: find button by text
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

    # Step 4: Wait for data to load
    if not _wait_for_data(page):
        if _has_data(page):
            return True
        logger.info("No data found for %s (may be holiday/weekend)", date_str)
        return False

    return True


# ---------------------------------------------------------------------------
# Main download function
# ---------------------------------------------------------------------------

def download_one_day(
    target_date: date,
    out_root: Optional[str] = None,
    page: Optional[Page] = None,
    browser: Optional[Browser] = None,
    context: Optional[BrowserContext] = None,
) -> Optional[Tuple[Path, Path]]:
    """Download trend data for a single day.

    Args:
        target_date: Trading date to download.
        out_root: Optional override for output directory.
        page: Optional pre-existing Playwright page (for batch operations).
        browser: Optional pre-existing browser.
        context: Optional pre-existing context.

    Returns:
        (futures_csv_path, options_csv_path) or None if no data.
    """
    date_str = _format_date_for_filename(target_date)
    output_dir = get_trend_month_dir(date_str[:6], out_root)
    output_dir.mkdir(parents=True, exist_ok=True)

    futures_path = trend_futures_csv_path(target_date, out_root)
    options_path = trend_options_csv_path(target_date, out_root)

    # Skip if already downloaded
    if futures_path.exists() and futures_path.stat().st_size > 100:
        logger.info("  %s already exists, skipping", date_str)
        return futures_path, options_path

    pw = None
    own_browser = False
    try:
        if page is None or browser is None:
            pw = sync_playwright().start()
            browser, context, page = _launch_browser(pw)
            own_browser = True

        # Navigate to the trend page
        _navigate_to_trend_page(page)

        # Wait a moment for the page to settle
        time.sleep(2)

        # Query the date
        success = _query_date(page, target_date)
        if not success:
            logger.info("  No data for %s (trading day but no data available)", date_str)
            return None

        # Extract table data
        table_data = _extract_table_data(page)
        if not table_data or len(table_data) < 2:
            logger.warning("  No table data extracted for %s", date_str)
            return None

        # Write combined CSV
        _write_combined_csv(table_data, output_dir, date_str)

        # Split into futures/options
        futures_path, options_path = _split_csv_futures_options(
            table_data, output_dir, date_str,
        )

        logger.info(
            "  Downloaded %s: %d futures rows, %d options rows",
            date_str,
            len([r for r in table_data[1:] if r and not _is_summary_row(r[0]) and not _is_option_contract(r[0])]),
            len([r for r in table_data[1:] if r and not _is_summary_row(r[0]) and _is_option_contract(r[0])]),
        )

        return futures_path, options_path

    except Exception as e:
        logger.error("  Download failed for %s: %s", date_str, e)
        return None
    finally:
        if own_browser and browser:
            browser.close()
        if pw is not None:
            pw.stop()


def download_trend_batch(
    dates: List[date],
    out_root: Optional[str] = None,
    sleep_sec: float = DOWNLOAD_SLEEP_SEC,
) -> dict:
    """Download trend data for a batch of dates using a single browser session.

    This is more efficient than launching a new browser for each date.

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

    with sync_playwright() as pw:
        browser, context, page = _launch_browser(pw)

        try:
            for i, target_date in enumerate(dates):
                date_str = _format_date_for_filename(target_date)
                futures_path = trend_futures_csv_path(target_date, out_root)

                # Skip if already downloaded
                if futures_path.exists() and futures_path.stat().st_size > 100:
                    logger.info(
                        "[%d/%d] %s already exists, skipping",
                        i + 1, len(dates), date_str,
                    )
                    result["skipped"] += 1
                    continue

                logger.info("[%d/%d] Downloading %s ...", i + 1, len(dates), date_str)

                try:
                    # Navigate to trend page
                    _navigate_to_trend_page(page)
                    time.sleep(1 + random.uniform(0, 2))

                    # Query the date
                    success = _query_date(page, target_date)
                    if not success:
                        logger.info("  No data for %s", date_str)
                        result["no_data"] += 1
                        continue

                    # Extract data
                    table_data = _extract_table_data(page)
                    if not table_data or len(table_data) < 2:
                        logger.warning("  No table data for %s", date_str)
                        result["failed"] += 1
                        continue

                    # Save
                    output_dir = get_trend_month_dir(date_str[:6], out_root)
                    output_dir.mkdir(parents=True, exist_ok=True)

                    _write_combined_csv(table_data, output_dir, date_str)
                    _split_csv_futures_options(table_data, output_dir, date_str)

                    result["downloaded"] += 1

                except Exception as e:
                    logger.error("  Failed for %s: %s", date_str, e)
                    result["failed"] += 1

                # Sleep between downloads with jitter
                if i < len(dates) - 1:
                    jitter = random.uniform(-SLEEP_JIT_RANGE, SLEEP_JIT_RANGE)
                    actual_sleep = max(2.0, sleep_sec + jitter)
                    logger.info("  Sleeping %.1fs ...", actual_sleep)
                    time.sleep(actual_sleep)

        finally:
            browser.close()

    return result