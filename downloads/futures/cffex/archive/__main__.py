"""Download CFFEX (China Financial Futures Exchange) historical archive data.

Studies ``http://www.cffex.com.cn/cn/lssjxz.html`` and downloads monthly ZIP
archives from ``http://www.cffex.com.cn/sj/historysj/{YYYYMM}/zip/{YYYYMM}.zip``.

Each ZIP contains daily settlement CSV files named ``{YYYYMMDD}_1.csv``
(GBK-encoded) with columns:
    合约代码, 今开盘, 最高价, 最低价, 成交量, 成交金额,
    持仓量, 持仓变化, 今收盘, 今结算, 前结算, 涨跌1, 涨跌2, Delta

Archive-only: downloads from 2020-01 to the last completed month.
Uses AntiBotProxy with LONG_SLEEP_INTERVAL (90s) for anti-bot protection.
"""

from __future__ import annotations


import io
import shutil
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import requests

from downloads._common import (
    DEFAULT_TIMEOUT,
    EMPTY_HTML_MAX_BYTES,
    LONG_SLEEP_INTERVAL,
    MIN_VALID_BYTES,
    AntiBotConfig,
    AntiBotProxy,
    build_headers_with_referer,
    clean_table_cell,
    is_error_html,
    is_valid_file,
    resolve_out_dir,
    safe_write_bytes,
    setup_logger,
)

CFFEX_BASE_URL = "http://www.cffex.com.cn/sj/historysj"
CFFEX_REFERER = "http://www.cffex.com.cn/cn/lssjxz.html"

ARCHIVE_DIRNAME = "cffex_archive"
SOURCE_CSV_ENCODING = "gbk"
OUTPUT_CSV_ENCODING = "utf-8-sig"

START_DATE = date(2020, 1, 1)

logger = setup_logger("cffex_archive")


def _first_of_previous_month(d: date) -> date:
    """Return the first day of the month before *d*'s month."""
    last_of_prev = d.replace(day=1) - timedelta(days=1)
    return last_of_prev.replace(day=1)


def _last_completed_month(today: Optional[date] = None) -> date:
    """Return the first day of the last completed month.

    CFFEX usually publishes the previous month's data by the 5th of the
    current month. We use the 5th as a safe cutoff:

    - On or before the 5th: the latest published data is from
      *two* months ago (the previous month may not be final yet).
    - After the 5th: the latest published data is from *one* month ago.
    """
    if today is None:
        today = date.today()
    if today.day <= 5:
        # Aug 3 -> Jun 1 (go back 2 months)
        return _first_of_previous_month(_first_of_previous_month(today))
    # Aug 16 -> Jul 1 (go back 1 month)
    return _first_of_previous_month(today)


def _month_range(start: date, end: date) -> List[date]:
    """Generate a list of first-of-month dates from start to end (inclusive)."""
    months: List[date] = []
    current = start.replace(day=1)
    end_month = end.replace(day=1)
    while current <= end_month:
        months.append(current)
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
    return months


def _month_key(d: date) -> str:
    """Return YYYYMM string for a date."""
    return d.strftime("%Y%m")


def _build_month_url(month_key: str) -> str:
    """Build the download URL for a given month key (YYYYMM)."""
    return f"{CFFEX_BASE_URL}/{month_key}/zip/{month_key}.zip"


def _decode_csv_bytes(raw: bytes) -> bytes:
    """Decode raw GBK CSV bytes and re-encode as UTF-8-sig (with BOM).

    CFFEX ZIPs contain GBK-encoded CSVs. We convert them to UTF-8 with
    BOM (0xEF, 0xBB, 0xBF) so modern editors and Excel can display
    Chinese characters correctly on Windows.
    """
    text = raw.decode(SOURCE_CSV_ENCODING, errors="replace")
    return text.encode(OUTPUT_CSV_ENCODING, errors="replace")


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
    source_csv: Path,
    output_dir: Path,
    logger_tag: str = "",
) -> Tuple[Path, Path]:
    """Split a daily CSV into futures and options CSVs.

    CFFEX daily settlement CSVs contain both futures and options
    contracts mixed together. This function reads the source CSV and
    produces two output files:

    - ``{YYYYMMDD}_futures.csv``: rows where contract code has no
      option marker (just prefix + date, e.g. IC2607, T2609)
    - ``{YYYYMMDD}_options.csv``: rows where contract code contains
      ``-C-`` or ``-P-`` (e.g. HO2607-C-2500)

    Summary rows (小计, 合计) are excluded from both outputs.

    Returns paths to (futures_csv, options_csv).
    """
    import csv as csv_mod

    date_prefix = source_csv.stem.replace("_1", "")
    futures_path = output_dir / f"{date_prefix}_futures.csv"
    options_path = output_dir / f"{date_prefix}_options.csv"

    with open(source_csv, "r", encoding=OUTPUT_CSV_ENCODING) as f:
        reader = csv_mod.reader(f)
        header = next(reader)
        rows = list(reader)

    futures_rows = []
    options_rows = []

    for row in rows:
        if not row:
            continue
        contract = row[0].strip()
        if not contract or _is_summary_row(contract):
            continue
        # canonical output: whitespace-free cells + null tokens ("--" etc.)
        # rewritten to "" so pandas infers numeric columns as float64
        cleaned = [clean_table_cell(c) for c in row]
        if _is_option_contract(contract):
            options_rows.append(cleaned)
        else:
            futures_rows.append(cleaned)

    def _write_csv(path: Path, header: List[str], data: List[List[str]]) -> None:
        import csv as csv_mod
        with open(path, "w", encoding=OUTPUT_CSV_ENCODING, newline="") as f:
            writer = csv_mod.writer(f)
            writer.writerow(header)
            writer.writerows(data)

    _write_csv(futures_path, header, futures_rows)
    _write_csv(options_path, header, options_rows)

    logger.info(
        "%s split %s: %d futures, %d options",
        logger_tag, source_csv.name, len(futures_rows), len(options_rows),
    )

    return futures_path, options_path


def _extract_csvs_from_zip(
    zip_content: bytes,
    extract_dir: Path,
    logger_tag: str = "",
) -> List[Path]:
    """Extract all CSV files from ZIP content into extract_dir.

    Each CSV is decoded from GBK and re-encoded as UTF-8-sig (with BOM),
    then split into ``{date}_futures.csv`` and ``{date}_options.csv``.
    The original combined CSV is kept as well.

    Returns list of all extracted/split file paths.
    """
    extracted: List[Path] = []
    extract_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(io.BytesIO(zip_content)) as zf:
        csv_files = [n for n in zf.namelist() if n.endswith(".csv")]
        if not csv_files:
            logger.warning("%s no CSV files found in ZIP", logger_tag)
            return extracted

        for csv_name in csv_files:
            target = extract_dir / csv_name
            with zf.open(csv_name) as src:
                raw_data = src.read()
            output_data = _decode_csv_bytes(raw_data)
            with open(target, "wb") as dst:
                dst.write(output_data)
            extracted.append(target)

            # Split into futures and options CSVs
            fut_path, opt_path = _split_csv_futures_options(
                target, extract_dir, logger_tag=logger_tag,
            )
            extracted.extend([fut_path, opt_path])

    logger.info(
        "%s extracted %d CSVs (combined + futures + options) -> %s",
        logger_tag, len(extracted), extract_dir,
    )
    return extracted


def _get_expected_csv_names(zip_content: bytes) -> Set[str]:
    """Get the set of expected CSV filenames from a ZIP's content."""
    with zipfile.ZipFile(io.BytesIO(zip_content)) as zf:
        return {n for n in zf.namelist() if n.endswith(".csv")}


_UTF8_BOM = b"\xef\xbb\xbf"


def _has_utf8_bom(filepath: Path) -> bool:
    """Check if a file starts with the UTF-8 BOM."""
    try:
        with open(filepath, "rb") as f:
            return f.read(3) == _UTF8_BOM
    except OSError:
        return False


_MIN_FUTURES_OPTIONS_PAIRS = 10  # A typical month has >=10 trading days


def _count_valid_futures_options_pairs(month_dir: Path) -> int:
    """Count days that have both valid _futures.csv and _options.csv files."""
    futures_dates = set()
    options_dates = set()
    for f in month_dir.glob("*_futures.csv"):
        if _has_utf8_bom(f):
            futures_dates.add(f.stem.replace("_futures", ""))
    for f in month_dir.glob("*_options.csv"):
        if _has_utf8_bom(f):
            options_dates.add(f.stem.replace("_options", ""))
    return len(futures_dates & options_dates)


def _is_month_complete_heuristic(month_dir: Path) -> bool:
    """Heuristic check: does month_dir have enough valid futures+options pairs?

    Used when the ZIP is already deleted and we only have the extracted
    directory to inspect. A typical month has >= 10 trading-day pairs.
    """
    if not month_dir.exists():
        return False
    return _count_valid_futures_options_pairs(month_dir) >= _MIN_FUTURES_OPTIONS_PAIRS


def download_cffex_archive(
    out_root: Optional[str] = None,
    start_date: date = START_DATE,
    end_date: Optional[date] = None,
    force: bool = False,
    session: Optional[requests.Session] = None,
) -> dict:
    """Download CFFEX monthly archive ZIPs and extract CSV files.

    Downloads from ``start_date`` (default 2020-01-01) to
    ``end_date`` (default last completed month). Each month's ZIP is
    downloaded once and all contained CSVs are extracted into a
    month-named subdirectory. Months already downloaded are skipped
    (``--force`` re-downloads everything).

    Uses AntiBotProxy with LONG_SLEEP_INTERVAL (90s) between requests
    for anti-bot protection.
    """
    archive_dir = resolve_out_dir(
        str(Path(__file__).resolve()), ARCHIVE_DIRNAME, out_root,
    )
    sess = session or requests.Session()
    headers = build_headers_with_referer(CFFEX_REFERER)

    if end_date is None:
        end_date = _last_completed_month()

    months = _month_range(start_date, end_date)
    logger.info(
        "CFFEX archive: %d months to download (%s -> %s)",
        len(months),
        start_date.strftime("%Y-%m"),
        end_date.strftime("%Y-%m"),
    )

    proxy = AntiBotProxy(AntiBotConfig(
        base_sleep_sec=LONG_SLEEP_INTERVAL,
        sleep_jitter=0.3,
        rotate_browser_profile=True,
        add_random_param=True,
        enable_host_tracking=True,
        timeout=DEFAULT_TIMEOUT,
    ))

    downloaded = 0
    skipped = 0
    failed = 0

    for i, month in enumerate(months):
        mk = _month_key(month)
        zip_path = archive_dir / f"{mk}.zip"
        month_dir = archive_dir / mk
        url = _build_month_url(mk)
        tag = f"[{mk}]"

        if proxy.is_blocked(CFFEX_BASE_URL):
            logger.warning(
                "  [host-blocked] cffex.com.cn blocked, stopping at month %s", mk,
            )
            failed += len(months) - i
            break

        # --- Skip check 1: month_dir already has valid futures+options pairs ---
        if not force and _is_month_complete_heuristic(month_dir):
            logger.info(
                "%s already extracted and valid (%d days), skipping",
                tag, _count_valid_futures_options_pairs(month_dir),
            )
            skipped += 1
            continue

        # --- Repair: combined CSVs exist but haven't been split yet ---
        if not force and month_dir.exists():
            combined_csvs = sorted(month_dir.glob("*_1.csv"))
            if combined_csvs and not list(month_dir.glob("*_futures.csv")):
                logger.info(
                    "%s found combined CSVs without split files, splitting...",
                    tag,
                )
                for combined in combined_csvs:
                    _split_csv_futures_options(
                        combined, month_dir, logger_tag=tag,
                    )
                if _is_month_complete_heuristic(month_dir):
                    downloaded += 1
                    continue
                else:
                    logger.warning(
                        "%s split incomplete, will re-download", tag,
                    )

        # --- Skip check 2: ZIP still exists (backward compat) ---
        if not force and is_valid_file(zip_path, min_bytes=MIN_VALID_BYTES):
            try:
                with open(zip_path, "rb") as f:
                    zip_content = f.read()
                expected = _get_expected_csv_names(zip_content)
                if _is_month_complete(month_dir, expected):
                    logger.info(
                        "%s already downloaded and extracted (%d CSVs), skipping",
                        tag, len(expected),
                    )
                    skipped += 1
                    zip_path.unlink(missing_ok=True)
                    continue
                else:
                    logger.info(
                        "%s ZIP exists, extracting missing CSVs...", tag,
                    )
                    _extract_csvs_from_zip(zip_content, month_dir, logger_tag=tag)
                    zip_path.unlink(missing_ok=True)
                    downloaded += 1
                    continue
            except Exception:
                logger.warning(
                    "%s existing ZIP is corrupt, will re-download", tag,
                )
                zip_path.unlink(missing_ok=True)

        # --- Re-download if month_dir exists but is incomplete ---
        if not force and month_dir.exists() and not _is_month_complete_heuristic(month_dir):
            logger.info(
                "%s incomplete extraction found, re-downloading...", tag,
            )
            shutil.rmtree(month_dir, ignore_errors=True)

        # --- Force re-download: clean the month directory ---
        if force and month_dir.exists():
            shutil.rmtree(month_dir, ignore_errors=True)
        if force and zip_path.exists():
            zip_path.unlink(missing_ok=True)

        # --- Download the ZIP ---
        logger.info(
            "%s downloading %s (%d/%d)...", tag, url, i + 1, len(months),
        )
        resp = proxy.get(
            sess,
            url,
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
            logger=logger,
            log_tag=tag,
        )

        if resp is None:
            logger.error("%s download failed", tag)
            failed += 1
            continue

        # Validate Content-Type: expect application/zip
        content_type = resp.headers.get("Content-Type", "")
        if "zip" not in content_type.lower() and is_error_html(
            content_type, resp.content, max_html_bytes=EMPTY_HTML_MAX_BYTES,
        ):
            logger.warning(
                "%s got HTML response (no data for this month?), skipping",
                tag,
            )
            failed += 1
            continue

        if "zip" not in content_type.lower():
            logger.warning(
                "%s unexpected Content-Type: %s, skipping",
                tag, content_type,
            )
            failed += 1
            continue

        # Extract CSVs directly from response content (no need to save to disk)
        try:
            _extract_csvs_from_zip(resp.content, month_dir, logger_tag=tag)
        except Exception as e:
            logger.error(
                "%s extraction failed: %s", tag, e,
            )
            failed += 1
            continue

        # Delete the ZIP — keep only the extracted directory and CSVs
        zip_path.unlink(missing_ok=True)

        downloaded += 1

    logger.info(
        "Done CFFEX archive. downloaded=%d skipped=%d failed=%d archive_dir=%s",
        downloaded, skipped, failed, archive_dir,
    )

    return {
        "downloaded": downloaded,
        "skipped": skipped,
        "failed": failed,
        "archive_dir": str(archive_dir),
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Download CFFEX historical archive data",
    )
    parser.add_argument(
        "--start",
        default="2020-01",
        help="Start month (YYYY-MM, default: 2020-01)",
    )
    parser.add_argument(
        "--end",
        default=None,
        help="End month (YYYY-MM, default: last completed month)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download all months even if already cached",
    )
    parser.add_argument(
        "--out-root",
        default=None,
        help="Override output directory root",
    )
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y-%m").date()
    end = None
    if args.end:
        end = datetime.strptime(args.end, "%Y-%m").date()

    result = download_cffex_archive(
        out_root=args.out_root,
        start_date=start,
        end_date=end,
        force=args.force,
    )

    print(f"\nSummary: {result}")