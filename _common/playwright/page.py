"""_common.playwright.page — Common Playwright page/DOM helpers.

Generic utilities shared by Playwright-based downloaders:
  - HTML table extraction with numeric column cleaning
  - Wait-for-data polling (with site-specific "no data" text detection)
  - CSV file download via page links (click capture + HTTP fallback)
  - GBK-encoded CSV file parsing
"""

from __future__ import annotations

import csv
import time
from io import StringIO
from logging import Logger
from pathlib import Path
from typing import List, Optional, Sequence, Set

from playwright.sync_api import Page

from downloads._common.core import setup_logger

logger = setup_logger("playwright_common")


NULL_TOKENS = {"", "--", "-", "—", "null", "NULL", "None", "nan", "NaN"}


def clean_numeric_value(value: str) -> str:
    """Strip thousand-separator commas; map null tokens to empty string."""
    v = value.strip()
    if v in NULL_TOKENS:
        return ""
    return v.replace(",", "")


def clean_numeric_rows(
    rows: List[List[str]],
    numeric_col_indices: Optional[Set[int]] = None,
) -> List[List[str]]:
    """Clean numeric columns of data rows (first row kept as header)."""
    if numeric_col_indices is None:
        return [list(row) for row in rows]
    cleaned: List[List[str]] = []
    for i, row in enumerate(rows):
        if i == 0:
            cleaned.append(list(row))
            continue
        new_row = list(row)
        for idx in numeric_col_indices:
            if idx < len(new_row):
                new_row[idx] = clean_numeric_value(new_row[idx])
        cleaned.append(new_row)
    return cleaned


def extract_table_rows(
    page: Page,
    table_selector: str = "table",
    numeric_col_indices: Optional[Set[int]] = None,
) -> Optional[List[List[str]]]:
    """Extract the first matching table's rows from the DOM.

    Only rows containing <td> cells are collected (rows built purely from
    <th> are skipped); the first collected row is treated as the header.

    Returns list of rows, or None if no table rows found.
    """
    try:
        raw_rows = page.evaluate(
            """(selector) => {
                const table = document.querySelector(selector);
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
            }""",
            table_selector,
        )
        if raw_rows is None:
            return None
        return clean_numeric_rows(raw_rows, numeric_col_indices)
    except Exception as e:
        logger.warning("Table extraction failed: %s", e)
        return None


def page_has_table_data(
    page: Page,
    table_selector: str = "table",
    no_data_text: Optional[str] = None,
    min_tr_rows: int = 2,
) -> bool:
    """Check the page shows table data (header + at least one data row)."""
    try:
        return bool(page.evaluate(
            """(args) => {
                const [selector, noDataText, minRows] = args;
                if (noDataText && document.body.innerText.includes(noDataText)) {
                    return false;
                }
                const table = document.querySelector(selector);
                if (!table) return false;
                return table.querySelectorAll('tr').length >= minRows;
            }""",
            [table_selector, no_data_text or "", min_tr_rows],
        ))
    except Exception:
        return False


def wait_for_table_data(
    page: Page,
    timeout_ms: int = 15000,
    poll_sec: float = 0.5,
    table_selector: str = "table",
    no_data_text: Optional[str] = None,
    min_tr_rows: int = 2,
) -> bool:
    """Poll until the page shows table data or the timeout expires.

    A final check runs after the deadline to avoid a false negative when
    the data lands right at the timeout boundary.
    """
    deadline = time.time() + (timeout_ms / 1000)
    while time.time() < deadline:
        if page_has_table_data(page, table_selector, no_data_text, min_tr_rows):
            return True
        time.sleep(poll_sec)
    return page_has_table_data(page, table_selector, no_data_text, min_tr_rows)


def download_csv_via_link(
    page: Page,
    download_dir: Path,
    *,
    base_origin: str,
    link_texts: Sequence[str] = (),
    relative_prefix: str = "",
    timeout_ms: int = 10000,
    log: Optional[Logger] = None,
) -> Optional[Path]:
    """Download a CSV exposed as a link on the page (e.g. CFFEX 日行情数据).

    1. Find the first <a> whose text contains any of link_texts, falling
       back to any link whose href ends with '.csv'.
    2. Trigger the download by clicking the link (Playwright download
       capture); on failure fall back to an HTTP GET through the page's
       request context (shares cookies with the browser).

    Args:
        page: Playwright page with data loaded.
        download_dir: Directory to save the downloaded file.
        base_origin: Site origin for resolving relative hrefs
            (e.g. "http://www.cffex.com.cn").
        link_texts: Link text keywords used to locate the download link.
        relative_prefix: Prefix appended for non-absolute, non-root hrefs
            (e.g. "/cn/").
        timeout_ms: Timeout for the download capture.
        log: Optional logger; defaults to this module's logger.

    Returns:
        Path to the downloaded CSV file, or None on failure.
    """
    lg = log or logger
    try:
        download_dir.mkdir(parents=True, exist_ok=True)

        href = page.evaluate(
            """(texts) => {
                const links = document.querySelectorAll('a');
                for (const a of links) {
                    const t = a.textContent || '';
                    for (const text of texts) {
                        if (text && t.includes(text)) {
                            return a.getAttribute('href') || '';
                        }
                    }
                }
                for (const a of links) {
                    const href = a.getAttribute('href') || '';
                    if (href.endsWith('.csv')) return href;
                }
                return '';
            }""",
            list(link_texts),
        )
        if not href:
            lg.warning("  No CSV download link found on page")
            return None

        lg.info("  Downloading CSV via link: %s", href)

        if href.startswith("http"):
            full_url = href
        elif href.startswith("/"):
            full_url = f"{base_origin}{href}"
        else:
            full_url = f"{base_origin}{relative_prefix}{href}"

        # Strategy 1: click the link and capture the download
        try:
            with page.expect_download(timeout=timeout_ms) as download_info:
                page.evaluate(
                    """(href) => {
                        const links = document.querySelectorAll('a');
                        for (const a of links) {
                            if ((a.getAttribute('href') || '') === href) {
                                a.click();
                                return true;
                            }
                        }
                        return false;
                    }""",
                    href,
                )
            download = download_info.value
            target = download_dir / download.suggested_filename
            download.save_as(str(target))
            lg.info("  Downloaded CSV to %s", target)
            return target
        except Exception:
            lg.info("  Click download failed, trying direct fetch...")

        # Strategy 2: HTTP GET through the page's request context
        response = page.request.get(full_url)
        if response.ok:
            name = full_url.rstrip("/").split("/")[-1] or "downloaded.csv"
            target = download_dir / name
            target.write_bytes(response.body())
            lg.info("  Downloaded CSV via HTTP to %s", target)
            return target
        lg.warning("  Failed to download CSV: HTTP %d", response.status)
        return None

    except Exception as e:
        lg.warning("  CSV download failed: %s", e)
        return None


def parse_csv_rows(
    csv_path: Path,
    *,
    encoding: str = "gbk",
    numeric_col_indices: Optional[Set[int]] = None,
) -> Optional[List[List[str]]]:
    """Parse a downloaded CSV file into rows (first row = header).

    Many exchange sites serve CSVs in GBK encoding; pass encoding="utf-8"
    for UTF-8 files.
    """
    try:
        text = csv_path.read_bytes().decode(encoding, errors="replace")
        rows: List[List[str]] = list(csv.reader(StringIO(text)))
        if not rows:
            return None
        return clean_numeric_rows(rows, numeric_col_indices)
    except Exception as e:
        logger.warning("Failed to parse downloaded CSV %s: %s", csv_path, e)
        return None
