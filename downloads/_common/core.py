from __future__ import annotations

import logging
import random
import re
import time
import warnings
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

import requests

import pandas as pd

warnings.filterwarnings("ignore", message="Workbook contains no default style")


# ---------------------------------------------------------------------------
# Trading-day calendar — migrated to _common/_holidays_and_weekdays
# ---------------------------------------------------------------------------
# The holiday table (CN_HOLIDAYS / CN_ADJUSTED_WORKDAYS) and trading-day
# helpers (is_trading_day, last_business_day, next_business_day,
# business_days, count_weekdays, date_range_backward, date_range_forward,
# parse_date_window) now live in _common/_holidays_and_weekdays.py.
#
# We re-export them here for backward compatibility so existing
# `from _download_commons import is_trading_day` imports keep working.
# ---------------------------------------------------------------------------
from _common._holidays_and_weekdays import (  # noqa: E402
    CN_ADJUSTED_WORKDAYS,
    CN_HOLIDAYS,
    business_days,
    count_weekdays,
    date_range_backward,
    date_range_forward,
    is_trading_day,
    last_business_day,
    next_business_day,
    parse_date_window,
)


MIN_VALID_BYTES = 1024
EMPTY_HTML_MAX_BYTES = 8192
DEFAULT_TIMEOUT: Tuple[int, int] = (15, 60)

# Shared default sleep seconds between HTTP requests for anti-bot protection.
# Centralized here so the project's anti-bot policy can be changed in one place.
# Individual downloaders may override based on target site's aggressiveness.
DEFAULT_SLEEP_SEC = 20.0
DEFAULT_SHORT_SLEEP_SEC=8.0
# Long sleep for aggressive anti-bot sites (e.g. cninfo, SSE dividend endpoint
# when called at quarterly cadence). 90s between requests makes a full ETF-held
# sweep take ~hours but is the safest cadence for sites that block on volume.
LONG_SLEEP_INTERVAL = 90.0
VERY_LONG_SLEEP_INTERVAL = 300.0
SUPER_LONG_SLEEP_INTERVAL = 600.0

# Shared default start date for all downloaders. Centralized here so the
# project's historical backfill horizon can be changed in one place.
DEFAULT_START_DATE = "2020-01-01"


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
DEFAULT_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "image/avif,image/webp,image/apng,*/*;q=0.8"
)
DEFAULT_ACCEPT_LANG = "zh-CN,zh;q=0.9,en;q=0.8"

COMMON_BASE_HEADERS: Dict[str, str] = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": DEFAULT_ACCEPT,
    "Accept-Language": DEFAULT_ACCEPT_LANG,
    "Connection": "keep-alive",
}

BROWSER_PROFILES: List[Dict[str, str]] = [
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Sec-Ch-Ua": "\"Not/A)Brand\";v=\"8\", \"Chromium\";v=\"126\", \"Google Chrome\";v=\"126\"",
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": "\"Windows\"",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Sec-Ch-Ua": "\"Not/A)Brand\";v=\"8\", \"Chromium\";v=\"125\", \"Google Chrome\";v=\"125\"",
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": "\"Windows\"",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Sec-Ch-Ua": "\"Not/A)Brand\";v=\"8\", \"Chromium\";v=\"126\", \"Google Chrome\";v=\"126\"",
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": "\"macOS\"",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Sec-Ch-Ua": "\"Not/A)Brand\";v=\"8\", \"Chromium\";v=\"126\", \"Google Chrome\";v=\"126\"",
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": "\"Linux\"",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Edge/126.0.0.0"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Sec-Ch-Ua": "\"Not/A)Brand\";v=\"8\", \"Chromium\";v=\"126\", \"Microsoft Edge\";v=\"126\"",
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": "\"Windows\"",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) "
            "Gecko/20100101 Firefox/127.0"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:127.0) "
            "Gecko/20100101 Firefox/127.0"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) "
            "Gecko/20100101 Firefox/127.0"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Sec-Ch-Ua": "\"Not/A)Brand\";v=\"8\", \"Chromium\";v=\"124\", \"Google Chrome\";v=\"124\"",
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": "\"Windows\"",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Edge/125.0.0.0"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Sec-Ch-Ua": "\"Not/A)Brand\";v=\"8\", \"Chromium\";v=\"125\", \"Microsoft Edge\";v=\"125\"",
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": "\"Windows\"",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
    },
]

def random_browser_profile() -> Dict[str, str]:
    return dict(random.choice(BROWSER_PROFILES))


_BROWSER_FINGERPRINT_KEYS = {
    "User-Agent",
    "Sec-Ch-Ua",
    "Sec-Ch-Ua-Mobile",
    "Sec-Ch-Ua-Platform",
    "Sec-Ch-Ua-Platform-Version",
    "Sec-Fetch-Site",
    "Sec-Fetch-Mode",
    "Sec-Fetch-Dest",
    "Sec-Fetch-User",
    "Accept",
    "Accept-Language",
}


def merge_browser_profile(base_headers: Dict[str, str]) -> Dict[str, str]:
    """Overlay random browser fingerprint fields onto base headers.

    Only browser-specific keys (User-Agent, Sec-Ch-Ua-*, Sec-Fetch-*, Accept,
    Accept-Language) are overlaid, preventing site-specific headers like
    Content-Type or Referer from being accidentally overwritten.

    Args:
        base_headers: Base headers dict
    Returns:
        New dict with browser fingerprint fields merged on top
    """
    result = dict(base_headers)
    profile = random_browser_profile()
    for key in _BROWSER_FINGERPRINT_KEYS:
        if key in profile:
            result[key] = profile[key]
    return result


def random_sleep(base_sec: float, jitter_factor: float = 0.5) -> None:
    """Sleep for a random duration around base_sec with jitter.

    Adds anti-bot protection by making request intervals unpredictable.
    The actual sleep time is in [base_sec * (1-jitter), base_sec * (1+jitter)].

    Args:
        base_sec: Base sleep duration in seconds
        jitter_factor: Fraction of base_sec to vary (0.0 = no jitter, 1.0 = 100% jitter)
    """
    if base_sec <= 0:
        return
    jitter = base_sec * jitter_factor
    sleep_time = random.uniform(base_sec - jitter, base_sec + jitter)
    time.sleep(max(0, sleep_time))


def random_sleep_range(min_sec: float, max_sec: float) -> None:
    """Sleep for a random duration between min_sec and max_sec.

    Args:
        min_sec: Minimum sleep duration in seconds
        max_sec: Maximum sleep duration in seconds
    """
    if min_sec <= 0 and max_sec <= 0:
        return
    sleep_time = random.uniform(min(min_sec, max_sec), max(min_sec, max_sec))
    time.sleep(max(0, sleep_time))


RE_DATEKEY_YYYYMMDD = re.compile(r"_(\d{8})\.(xlsx|xls|csv|md|json)$")
RE_YEARKEY_YYYY = re.compile(r"_(\d{4})\.(xlsx|xls|csv|md|json)$")
RE_CHUNKKEY_RANGE = re.compile(r"_(\d{8})_(\d{8})\.(xlsx|xls|csv)$")


_LOGGER_FMT = "%(asctime)s [%(levelname)s] %(message)s"
_LOGGER_DATEFMT = "%Y-%m-%d %H:%M:%S"
_CONFIGURED_LOGGERS: Set[str] = set()


def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    if name not in _CONFIGURED_LOGGERS:
        logging.basicConfig(level=level, format=_LOGGER_FMT, datefmt=_LOGGER_DATEFMT)
        logging.getLogger("urllib3.connection").setLevel(logging.ERROR)
        _CONFIGURED_LOGGERS.add(name)
    return logging.getLogger(name)


def build_headers_with_referer(referer: str, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    h = dict(COMMON_BASE_HEADERS)
    h["Referer"] = referer
    if extra:
        h.update(extra)
    return h


def build_default_session(headers: Optional[Dict[str, str]] = None) -> requests.Session:
    s = requests.Session()
    base = dict(COMMON_BASE_HEADERS)
    if headers:
        base.update(headers)
    s.headers.update(base)
    return s


# Project root = three levels up from this module
# (downloads/_common/core.py -> _common -> downloads -> <project root>).
# Computed once at import time so all downloaders write ``temps/`` under the
# project root regardless of where the calling script lives in the tree.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def resolve_out_dir(
    caller_file: str,
    out_dirname: str,
    out_root: Optional[str] = None,
) -> Path:
    # *caller_file* is kept for backward compatibility but no longer drives
    # the output location — scripts now live at varying depths under
    # ``downloads/``, so the project root is derived from this module's path.
    out_dir = Path(out_root) if out_root else _PROJECT_ROOT / "temps" / out_dirname
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


# is_trading_day / last_business_day / next_business_day / business_days /
# count_weekdays / date_range_backward / date_range_forward / parse_date_window
# are re-exported from _common._holidays_and_weekdays at the top of this module.


def is_valid_file(path: Path, *, min_bytes: int = MIN_VALID_BYTES) -> bool:
    try:
        return path.exists() and path.is_file() and path.stat().st_size >= min_bytes
    except OSError:
        return False


def is_fresh_today(path: Path, *, min_bytes: int = MIN_VALID_BYTES, hour: int = 17) -> bool:
    """Check if a file exists, is valid, and was modified at or after *hour* on today's date.

    Args:
        path: Path to the file
        min_bytes: Minimum valid file size (default MIN_VALID_BYTES)
        hour: Hour threshold (0-23) — file must be modified at or after this hour today

    Returns:
        True if file exists, has valid size, and was modified today at or after the specified hour;
        False otherwise.
    """
    if not is_valid_file(path, min_bytes=min_bytes):
        return False
    try:
        mtime = path.stat().st_mtime
        mtime_dt = datetime.fromtimestamp(mtime)
        today = datetime.now()
        # Check if modification date is today
        if mtime_dt.date() != today.date():
            return False
        # Check if modification hour is >= threshold
        return mtime_dt.hour >= hour
    except OSError:
        return False


def is_error_html(
    content_type: str,
    content: bytes,
    *,
    max_html_bytes: int = EMPTY_HTML_MAX_BYTES,
) -> bool:
    if "html" not in content_type.lower():
        return False
    if len(content) > max_html_bytes:
        return False
    text = content.decode("utf-8", errors="ignore")
    return "错误" in text or "error" in text.lower()


def normalize_code_column(df: pd.DataFrame, suffix: str) -> pd.DataFrame:
    """Normalize the 证券代码 column: zero-pad to 6 digits and append *suffix*.

    Handles three input shapes produced by the SZSE/SSE Excel exports:
      * float   like 1.0          -> "000001.SZ"
      * str     like "000001"     -> "000001.SZ"
      * str     like "000001.SZ"  -> unchanged (idempotent)

    Only the exact column named 证券代码 is processed. Other code-like
    columns (合约编码, 标的证券简称（代码）, etc.) are left untouched.
    """
    if not suffix:
        return df
    suffix = suffix if suffix.startswith(".") else "." + suffix
    if "证券代码" not in df.columns:
        return df
    result = df.copy()
    series = result["证券代码"]
    if series.dtype == float or (series.dtype == object and series.astype(str).str.contains(r"^\d+\.0$", regex=True, na=False).any()):
        cleaned = pd.to_numeric(series, errors="coerce")
        cleaned = cleaned.apply(lambda v: f"{int(v):06d}{suffix}" if pd.notna(v) else "")
    else:
        cleaned = series.astype(str).str.strip()
        mask = ~cleaned.str.contains(r"\.", regex=True, na=False)
        cleaned = cleaned.where(~mask, cleaned[mask].str.zfill(6) + suffix)
    result["证券代码"] = cleaned
    return result


def _normalize_raw_code(val: Any) -> str:
    """Normalize a raw code cell to a 6-digit zero-padded string.

    Handles float exports (1.0 -> "000001"), bare numeric strings
    ("399001" -> "399001"), and already-suffixed strings ("399001.SZ" ->
    "399001"). Non-numeric strings are returned unchanged.
    """
    s = str(val).strip()
    if not s:
        return ""
    if "." in s:
        s = s.split(".", 1)[0]
    if s.isdigit():
        return s.zfill(6)
    return s


def filter_by_code(df: pd.DataFrame, code_filter: List[str]) -> pd.DataFrame:
    """Keep only rows whose code column value matches one of *code_filter*.

    Looks for the first of these columns (in order): 证券代码, 指数代码.
    Raw cell values are normalized via :func:`_normalize_raw_code` before
    comparison, so callers can pass bare 6-digit codes like
    ``["399001", "399006"]`` regardless of how Excel exported the column
    (int, float-with-trailing-.0, or already-suffixed). Returns *df*
    unchanged if no recognized code column exists or *code_filter* is empty.
    """
    if not code_filter:
        return df
    code_col = None
    for cand in ("证券代码", "指数代码"):
        if cand in df.columns:
            code_col = cand
            break
    if code_col is None:
        return df
    normalized = df[code_col].map(_normalize_raw_code)
    wanted = {_normalize_raw_code(c) for c in code_filter}
    mask = normalized.isin(wanted)
    return df[mask].reset_index(drop=True)


def convert_xlsx_to_csv(
    xlsx_path: Path,
    *,
    sheet_name: Any = 0,
    csv_path: Optional[Path] = None,
    encoding: str = "utf-8-sig",
    logger: Optional[logging.Logger] = None,
    log_tag: str = "",
    code_suffix: str = "",
    code_filter: Optional[List[str]] = None,
) -> Optional[Path]:
    """Convert an xlsx file to CSV.

    *code_suffix* is appended to the 证券代码 column (e.g. ".SZ") via
    :func:`normalize_code_column`. *code_filter*, when provided, keeps only
    rows whose 证券代码 / 指数代码 value (normalized to a 6-digit string)
    is in the list — used to extract a subset of rows (e.g. only
    399001 / 399006 from a full-index export) into the CSV. The xlsx itself
    is left untouched.
    """
    if not xlsx_path.exists() or not xlsx_path.is_file():
        if logger:
            logger.warning(
                "%sconvert_xlsx_to_csv: xlsx not found: %s", log_tag, xlsx_path,
            )
        return None

    suffix = xlsx_path.suffix.lower()
    if suffix not in (".xlsx", ".xls"):
        if logger:
            logger.warning(
                "%sconvert_xlsx_to_csv: unsupported extension %s for %s",
                log_tag, suffix, xlsx_path.name,
            )
        return None

    if csv_path is None:
        csv_path = xlsx_path.with_suffix(".csv")

    try:
        df = pd.read_excel(xlsx_path, sheet_name=sheet_name, dtype=object)
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(df, dict):
            first_sheet = next(iter(df.values()))
            first_sheet = normalize_dataframe_numbers(first_sheet)
            if code_filter:
                first_sheet = filter_by_code(first_sheet, code_filter)
            if code_suffix:
                first_sheet = normalize_code_column(first_sheet, code_suffix)
            first_sheet.to_csv(csv_path, index=False, encoding=encoding)
            n_sheets = len(df)
            rows = len(first_sheet)
        else:
            df = normalize_dataframe_numbers(df)
            if code_filter:
                df = filter_by_code(df, code_filter)
            if code_suffix:
                df = normalize_code_column(df, code_suffix)
            df.to_csv(csv_path, index=False, encoding=encoding)
            n_sheets = 1
            rows = len(df)
    except Exception as e:
        if logger:
            logger.error(
                "%sconvert_xlsx_to_csv failed for %s: %s",
                log_tag, xlsx_path.name, e,
            )
        return None

    if logger:
        sz = csv_path.stat().st_size if csv_path.exists() else 0
        logger.info(
            "%sconverted %s -> %s (sheets=%d rows=%d csv_bytes=%d)",
            log_tag, xlsx_path.name, csv_path.name, n_sheets, rows, sz,
        )
    return csv_path


RE_NUMERIC_PATTERN = re.compile(r"^[+-]?[\d,._\s]+(?:[,.]\d+)?$")


def normalize_numeric_string(val: str) -> Optional[float]:
    s = str(val).strip()
    if not s:
        return None
    if not RE_NUMERIC_PATTERN.match(s):
        return None

    original = s
    s = s.replace(" ", "")

    if s == "":
        return None

    has_comma = "," in s
    has_dot = "." in s
    has_underscore = "_" in s

    s = s.replace("_", "")

    if has_comma and has_dot:
        last_comma = s.rfind(",")
        last_dot = s.rfind(".")
        if last_dot > last_comma:
            decimal_sep = "."
            thousands_sep = ","
        else:
            decimal_sep = ","
            thousands_sep = "."
        s = s.replace(thousands_sep, "")
        s = s.replace(decimal_sep, ".")
    elif has_comma and not has_dot:
        parts = s.split(",")
        if len(parts) > 1 and len(parts[-1]) <= 2:
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    elif has_dot and not has_comma:
        parts = s.split(".")
        if len(parts) > 1 and len(parts[-1]) <= 2:
            pass
        else:
            s = s.replace(".", "")

    try:
        return float(s)
    except ValueError:
        return None


def normalize_dataframe_numbers(df: pd.DataFrame, *, threshold: float = 0.9) -> pd.DataFrame:
    result = df.copy()
    for col in result.columns:
        col_dtype = result[col].dtype
        if col_dtype != object and str(col_dtype) != "str":
            continue
        col_lower = str(col).lower()
        if "code" in col_lower or "代码" in str(col) or "编码" in str(col):
            continue
        col_values = result[col].dropna()
        if len(col_values) == 0:
            continue

        success_count = 0
        total_count = len(col_values)

        for val in col_values:
            normalized = normalize_numeric_string(val)
            if normalized is not None:
                success_count += 1

        success_rate = success_count / total_count
        if success_rate >= threshold:
            normalized_series = result[col].apply(normalize_numeric_string)
            try:
                result[col] = pd.to_numeric(normalized_series, errors="coerce")
            except ValueError:
                result[col] = normalized_series

    return result


def safe_write_bytes(
    out_file: Path,
    content: bytes,
    *,
    min_bytes: int = MIN_VALID_BYTES,
    logger: Optional[logging.Logger] = None,
    log_tag: str = "",
    auto_convert: bool = True,
) -> bool:
    """Save *content* to *out_file*.

    For ``.xlsx``/``.xls`` files, the CSV conversion is also triggered
    unless *auto_convert* is False — in which case the caller is expected
    to invoke :func:`convert_xlsx_to_csv` itself (e.g. with a code_filter
    that this auto-conversion path does not apply).
    """
    if len(content) < min_bytes:
        if logger:
            logger.warning(
                "%s content too small (%d bytes), skipping save", log_tag, len(content),
            )
        return False
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "wb") as f:
        f.write(content)
    if logger:
        sz = out_file.stat().st_size
        logger.info("%s saved %s (%d bytes)", log_tag, out_file.name, sz)

    if auto_convert:
        suffix = out_file.suffix.lower()
        if suffix in (".xlsx", ".xls"):
            convert_xlsx_to_csv(
                out_file,
                sheet_name=0,
                logger=logger,
                log_tag=log_tag,
            )

    return True


def read_csv_preferred(
    xlsx_path: Path,
    *,
    dtype: Any = None,
    sheet_name: Any = 0,
    encoding: str = "utf-8-sig",
    min_csv_bytes: int = 64,
    convert_on_fallback: bool = True,
    logger: Optional[logging.Logger] = None,
    log_tag: str = "",
    **read_kwargs: Any,
) -> Optional[pd.DataFrame]:
    """Read CSV if present alongside xlsx_path, else fall back to xlsx.

    Prefers the CSV intermediary for speed and lower memory use. On xlsx
    fallback, optionally triggers convert_xlsx_to_csv so the CSV exists
    on next read.

    Parameters
    ----------
    xlsx_path : Path
        Canonical xlsx file path (used to derive csv path = with_suffix(".csv")).
    dtype : dtype or dict of {col: dtype}, optional
        Column dtype overrides forwarded to read_csv / read_excel.
    sheet_name : str or int or list, default 0
        Sheet selector for the xlsx fallback path only.
    encoding : str, default "utf-8-sig"
        CSV encoding used by both read and (re)write.
    min_csv_bytes : int, default 64
        Treat a CSV smaller than this as corrupt/invalid and fall back to xlsx.
    convert_on_fallback : bool, default True
        If True and we had to read from xlsx, also write the companion CSV so
        subsequent reads hit the fast path.
    logger / log_tag : forwarded to convert_xlsx_to_csv when triggered.
    **read_kwargs
        Additional kwargs forwarded to read_csv/read_excel.
    """
    if isinstance(xlsx_path, str):
        xlsx_path = Path(xlsx_path)
    csv_path = xlsx_path.with_suffix(".csv")

    csv_ok = False
    if csv_path.exists():
        try:
            sz = csv_path.stat().st_size
        except OSError:
            sz = 0
        if sz >= min_csv_bytes:
            csv_ok = True

    if csv_ok:
        try:
            return pd.read_csv(
                csv_path,
                dtype=dtype,
                encoding=encoding,
                low_memory=False,
                **read_kwargs,
            )
        except Exception as e:
            if logger:
                logger.warning(
                    "%sread_csv_preferred: csv read failed for %s (%s); "
                    "falling back to xlsx",
                    log_tag, csv_path.name, e,
                )

    if not xlsx_path.exists() or not xlsx_path.is_file():
        if logger:
            logger.warning(
                "%sread_csv_preferred: xlsx not found: %s", log_tag, xlsx_path,
            )
        return None

    try:
        df = pd.read_excel(
            xlsx_path,
            sheet_name=sheet_name,
            dtype=dtype,
            **read_kwargs,
        )
    except Exception as e:
        if logger:
            logger.warning(
                "%sread_csv_preferred: xlsx read failed for %s: %s",
                log_tag, xlsx_path.name, e,
            )
        return None

    if convert_on_fallback:
        convert_xlsx_to_csv(
            xlsx_path,
            sheet_name=sheet_name,
            csv_path=csv_path,
            encoding=encoding,
            logger=logger,
            log_tag=log_tag,
        )

    return df


# ---------------------------------------------------------------------------
# Scan-phase helpers: inspect local filesystem for valid cached files
# ---------------------------------------------------------------------------

def scan_valid_files(
    out_dir: Path,
    *,
    glob_pattern: str = "*",
    min_bytes: int = MIN_VALID_BYTES,
) -> Dict[Path, int]:
    if not out_dir.exists():
        return {}
    result: Dict[Path, int] = {}
    for p in out_dir.glob(glob_pattern):
        if not p.is_file():
            continue
        try:
            sz = p.stat().st_size
        except OSError:
            continue
        if sz >= min_bytes:
            result[p] = sz
    return result


# Pattern A: {prefix}_{YYYYMMDD}.{ext}
def _extract_datekey(name: str, prefix: str) -> Optional[date]:
    if not name.startswith(prefix + "_"):
        return None
    m = RE_DATEKEY_YYYYMMDD.search(name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def scan_present_day_keys(
    out_dir: Path,
    *,
    prefixes: Iterable[str],
    min_bytes: int = MIN_VALID_BYTES,
    ext_glob: str = "*.xlsx",
) -> Dict[str, Set[date]]:
    present: Dict[str, Set[date]] = {p: set() for p in prefixes}
    valid_files = scan_valid_files(out_dir, glob_pattern=ext_glob, min_bytes=min_bytes)
    for path in valid_files:
        for prefix in prefixes:
            d = _extract_datekey(path.name, prefix)
            if d is not None:
                present[prefix].add(d)
                break
    return present


# Pattern B: {prefix}_{YYYY}.{ext}
def _extract_yearkey(name: str, prefix: str) -> Optional[int]:
    if not name.startswith(prefix + "_"):
        return None
    m = RE_YEARKEY_YYYY.search(name)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def scan_present_year_keys(
    out_dir: Path,
    *,
    prefixes: Iterable[str],
    min_bytes: int = MIN_VALID_BYTES,
    ext_glob: str = "*.xlsx",
) -> Dict[str, Set[int]]:
    present: Dict[str, Set[int]] = {p: set() for p in prefixes}
    valid_files = scan_valid_files(out_dir, glob_pattern=ext_glob, min_bytes=min_bytes)
    for path in valid_files:
        for prefix in prefixes:
            y = _extract_yearkey(path.name, prefix)
            if y is not None:
                present[prefix].add(y)
                break
    return present


# Pattern C: {prefix}_{YYYYMMDD}_{YYYYMMDD}.{ext}
def _extract_chunkkey(name: str, prefix: str) -> Optional[Tuple[date, date]]:
    if not name.startswith(prefix + "_"):
        return None
    m = RE_CHUNKKEY_RANGE.search(name)
    if not m:
        return None
    try:
        s = datetime.strptime(m.group(1), "%Y%m%d").date()
        e = datetime.strptime(m.group(2), "%Y%m%d").date()
        return (s, e)
    except ValueError:
        return None


def scan_present_chunk_keys(
    out_dir: Path,
    *,
    prefixes: Iterable[str],
    min_bytes: int = MIN_VALID_BYTES,
    ext_glob: str = "*.xlsx",
) -> Dict[str, Set[Tuple[date, date]]]:
    present: Dict[str, Set[Tuple[date, date]]] = {p: set() for p in prefixes}
    valid_files = scan_valid_files(out_dir, glob_pattern=ext_glob, min_bytes=min_bytes)
    for path in valid_files:
        for prefix in prefixes:
            key = _extract_chunkkey(path.name, prefix)
            if key is not None:
                present[prefix].add(key)
                break
    return present


# Pattern D: arbitrary by filename (returns set of valid filenames)
def scan_present_filenames(
    out_dir: Path,
    *,
    glob_pattern: str = "*.md",
    min_bytes: int = 200,
) -> Set[str]:
    valid_files = scan_valid_files(out_dir, glob_pattern=glob_pattern, min_bytes=min_bytes)
    return {p.name for p in valid_files.keys()}


RE_DATEKEY_YYYYMMDD_DASH = re.compile(r"_(\d{4}-\d{2}-\d{2})_")


def scan_present_dates_with_pattern(
    out_dir: Path,
    *,
    prefixes: Iterable[str],
    min_bytes: int = MIN_VALID_BYTES,
    ext_glob: str = "*.md",
    date_pattern: re.Pattern = RE_DATEKEY_YYYYMMDD_DASH,
    date_format: str = "%Y-%m-%d",
) -> Dict[str, Set[date]]:
    present: Dict[str, Set[date]] = {p: set() for p in prefixes}
    valid_files = scan_valid_files(out_dir, glob_pattern=ext_glob, min_bytes=min_bytes)
    for path in valid_files:
        for prefix in prefixes:
            if path.name.startswith(prefix + "_"):
                m = date_pattern.search(path.name)
                if m:
                    try:
                        d = datetime.strptime(m.group(1), date_format).date()
                        present[prefix].add(d)
                        break
                    except ValueError:
                        continue
    return present


# ---------------------------------------------------------------------------
# DB-based scan helpers — DEPRECATED
# ---------------------------------------------------------------------------
# These thin wrappers delegate to _common.pre_check_and_load.check_identity() /
# check_identity_years() and are kept only for backward
# compatibility. New code should call check_identity / check_identity_years
# directly.
# ---------------------------------------------------------------------------

def get_existing_dates_from_db(
    table_name: str,
    date_column: str = "date",
) -> Set[date]:
    """Query the database for existing dates in a table (sync, DEPRECATED).

    .. deprecated::
        Use :func:`_common.pre_check_and_load.check_identity` instead, which returns the
        complementary set (missing dates) and properly skips holidays and
        weekends. This wrapper queries the raw present-date set without any
        holiday awareness.

    Args:
        table_name: table name with optional schema prefix
                    (e.g., "stats.etf_identity").
        date_column: name of the date column (default "date").

    Returns:
        Set of ``datetime.date`` objects present in the table.
    """
    from _common.db_commons import get_db_connection, _build_identity_where_clause, _build_identity_params
    conn = get_db_connection()
    try:
        schema, table = _parse_table_name_local(table_name)
        # Query ALL present dates (no range filter); use IS NOT NULL guard.
        # We reuse the identifier-quoting helper to avoid SQL injection on
        # the table/column names, but the predicate is just IS NOT NULL.
        from psycopg import sql
        where = sql.SQL("{col} IS NOT NULL").format(col=sql.Identifier(date_column))
        query = sql.SQL("SELECT DISTINCT {col} FROM {tbl} WHERE {where}").format(
            col=sql.Identifier(date_column),
            tbl=sql.Identifier(schema, table) if schema else sql.Identifier(table),
            where=where,
        )
        with conn.cursor() as cur:
            cur.execute(query)
            return {row[0] for row in cur.fetchall() if row[0] is not None}
    finally:
        conn.close()


def _parse_table_name_local(table_name: str):
    """Parse a (schema, table) tuple — same logic as _db_commons._parse_table_name."""
    parts = table_name.split(".", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return None, parts[0]


def get_existing_years_from_db(
    table_name: str,
    date_column: str = "date",
) -> Set[int]:
    """Query the database for years that have at least one row in a table (DEPRECATED).

    .. deprecated::
        Use :func:`_common.pre_check_and_load.check_identity_years` instead.

    Args:
        table_name: table name with optional schema prefix.
        date_column: name of the date column (default "date").

    Returns:
        Set of years (int) that have data in the table.
    """
    from _common.db_commons import get_db_connection
    from psycopg import sql
    conn = get_db_connection()
    schema, table = _parse_table_name_local(table_name)
    try:
        tbl = sql.Identifier(schema, table) if schema else sql.Identifier(table)
        query = sql.SQL(
            'SELECT DISTINCT EXTRACT(YEAR FROM {col})::int '
            "FROM {tbl} "
            "WHERE {col} IS NOT NULL"
        ).format(col=sql.Identifier(date_column), tbl=tbl)
        with conn.cursor() as cur:
            cur.execute(query)
            return {row[0] for row in cur.fetchall() if row[0] is not None}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Stock code normalization helpers
# ---------------------------------------------------------------------------
#
# CRITICAL: Shanghai and Shenzhen stock code prefixes overlap in the 000xxx/001xxx range!
#
# Shanghai Stock Exchange (SSE):
#   - 600xxx, 601xxx, 603xxx, 605xxx: Shanghai-listed stocks
#   - 688xxx: STAR Market (科创板)
#   - 000xxx, 001xxx: Shanghai indices (e.g., 000001=SSE Composite, 000300=CSI 300)
#
# Shenzhen Stock Exchange (SZSE):
#   - 000xxx, 001xxx: Shenzhen main board stocks (e.g., 000001=Ping An Bank)
#   - 002xxx, 003xxx: SME board (中小板)
#   - 300xxx, 301xxx: ChiNext board (创业板)
#
# AMBIGUITY: Code 000001 could refer to either:
#   - SSE Composite Index (Shanghai)
#   - Ping An Bank (Shenzhen)
#
# Solution: When processing data, ALWAYS pass the 'market' parameter to add_exchange_suffix().
# When the market is known, we use it to disambiguate. When market is not known:
#   - Unambiguous prefixes (600-605, 688, 002-003, 300-301) are auto-classified
#   - Known Shanghai index codes are mapped to .SS
#   - Other 000xxx/001xxx codes trigger a warning and are returned without suffix
#
# ETF Codes (NO overlap between exchanges):
#   Shanghai (SSE):   510xxx, 511xxx, 512xxx, 513xxx, 515xxx, 516xxx, 518xxx, 56xxx
#   Shenzhen (SZSE):  150xxx, 159xxx, 16xxx
#   Unlike stock codes, ETF prefixes are completely non-overlapping between exchanges,
#   so no suffix disambiguation is needed for ETFs.
# ---------------------------------------------------------------------------

SHANGHAI_EXCLUSIVE_PREFIXES = ("600", "601", "603", "605", "688")
SHENZHEN_EXCLUSIVE_PREFIXES = ("002", "003", "300", "301")

AMBIGUOUS_PREFIXES = ("000", "001")

SHANGHAI_BROADMARKET_INDEX_CODES = {
    "000001",
    "000002",
    "000003",
    "000008",
    "000009",
    "000016",
    "000300",
    "000905",
}


def get_exchange_from_code(stock_code: str) -> Optional[str]:
    code = str(stock_code).strip()
    if len(code) != 6:
        return None
    prefix = code[:3]
    if prefix in SHANGHAI_EXCLUSIVE_PREFIXES:
        return "SS"
    if prefix in SHENZHEN_EXCLUSIVE_PREFIXES:
        return "SZ"
    if prefix in AMBIGUOUS_PREFIXES:
        if code in SHANGHAI_BROADMARKET_INDEX_CODES:
            return "SS"
    return None


def add_exchange_suffix(stock_code: str, market: Optional[str] = None) -> str:
    code = str(stock_code).strip()
    if "." in code:
        return code
    if len(code) != 6:
        return code
    if market:
        if "上海" in market:
            return code + ".SS"
        if "深圳" in market:
            return code + ".SZ"
        if "北京" in market:
            return code + ".BJ"
        if "香港" in market:
            return code + ".HK"
    exchange = get_exchange_from_code(code)
    if exchange:
        return code + "." + exchange
    import warnings
    prefix = code[:3]
    if prefix in AMBIGUOUS_PREFIXES:
        reason = (
            f"Codes {prefix}xxx are ambiguous (used by both Shanghai indices "
            f"and Shenzhen stocks)"
        )
    else:
        reason = f"Unrecognized prefix '{prefix}' (not a stock/index prefix)"
    warnings.warn(
        f"Cannot determine exchange for code '{code}'. {reason}. "
        f"Pass 'market' parameter explicitly. Returning code without suffix."
    )
    return code


def strip_exchange_suffix(stock_code: str) -> str:
    code = str(stock_code).strip()
    if "." in code:
        parts = code.split(".")
        if len(parts) == 2 and parts[1] in ("SS", "SZ", "BJ", "HK"):
            return parts[0]
    return code


# ---------------------------------------------------------------------------
# Classification JSON loader — replacement for _classification.ICONIC_INDEXES
# ---------------------------------------------------------------------------

_CLASSIFICATION_JSON_PATH = Path(__file__).resolve().parents[2] / "_common" / "sec_statics" / "sec_classification.json"


def load_classification_indices() -> Dict[str, Dict[str, Any]]:
    """Load index classifications from ``sec_classification.json``.

    Returns a dict keyed by index code, where each value is the full index
    entry from the JSON::

        {
            "name": str,
            "exchange": Optional[str],   # "SS" | "SZ" | "BJ" | "HK" | None
            "sector_id": str,
            "industry_id": str,
            "tags": List[Dict[str, str]],
            "n_days": int,
            "first_date": Optional[str],
            "last_date": Optional[str],
        }

    This replaces ``_classification.ICONIC_INDEXES`` (code → short name) and
    ``_classification.classify_index()`` (name → sector/industry) by providing
    the pre-classified data directly from the authoritative JSON cache.
    """
    import json as _json
    if not _CLASSIFICATION_JSON_PATH.is_file():
        return {}
    with _CLASSIFICATION_JSON_PATH.open("r", encoding="utf-8") as f:
        state = _json.load(f)
    return state.get("indices", {})


def load_classification_index_names() -> Dict[str, str]:
    """Return a flat ``{code: name}`` dict from ``sec_classification.json``.

    Convenience wrapper around :func:`load_classification_indices` for callers
    that only need the code → name mapping (drop-in replacement for
    ``ICONIC_INDEXES``).
    """
    return {
        code: info.get("name", code)
        for code, info in load_classification_indices().items()
    }


# ---------------------------------------------------------------------------
# High-level: Scan + Plan orchestrators for common patterns
# ---------------------------------------------------------------------------

@dataclass
class DayDownloadPlanItem:
    type_key: str
    prefix: str
    day: date


@dataclass
class DayDownloadPlan:
    items: List[DayDownloadPlanItem] = field(default_factory=list)
    present_count: int = 0
    total_expected: int = 0

    def summary_str(self) -> str:
        return (
            f"expected={self.total_expected} cached={self.present_count} "
            f"missing_to_download={len(self.items)}"
        )


def build_day_download_plan(
    *,
    out_dir: Path,
    start_date: date,
    end_date: date,
    type_configs: Dict[str, Dict[str, Any]],
    min_bytes: int = MIN_VALID_BYTES,
    weekdays_only: bool = True,
    sort_newest_first: bool = True,
    ext_glob: str = "*.xlsx",
    db_table: Optional[str] = None,
    db_date_column: str = "date",
    db_code_suffix: Optional[str] = None,
    skip_empty_markers: bool = False,
) -> DayDownloadPlan:
    """Build a per-day download plan.

    When *db_table* is provided, missing trading days are computed via
    :func:`_common.pre_check_and_load.check_identity` (which skips holidays and weekends
    via ``_common._holidays_and_weekdays``) instead of scanning the local
    filesystem.  All types share the same DB-derived missing-date set —
    a date missing from the DB is queued for download for every type.

    *db_code_suffix* is forwarded to ``check_identity`` as the
    ``code_suffix`` filter, so multi-source identity tables (e.g.
    ``stats.stock_identity`` fed by SZSE + SSE + BSE) can be queried
    per-exchange.

    *skip_empty_markers* — when True, also scans local ``*.csv`` files
    (including 0-byte empty markers created when a date was previously
    fetched but the server returned no data) and treats those dates as
    "already tried" so they are excluded from the download plan. This
    works in both DB-first and filesystem-scan modes. In DB-first mode,
    dates present in the DB are already excluded; *skip_empty_markers*
    additionally excludes dates that have a local empty-marker CSV but
    are not yet in the DB (download was attempted, no data found, build
    step has not run).
    """
    type_keys = list(type_configs.keys())
    prefix_map = {tk: type_configs[tk]["prefix"] for tk in type_keys}
    prefixes = list(prefix_map.values())

    all_dates = business_days(start_date, end_date, reverse=sort_newest_first) if weekdays_only else list(
        reversed(list(date_range_backward(end_date, start_date))) if sort_newest_first else list(date_range_forward(start_date, end_date))
    )
    if not weekdays_only and not sort_newest_first:
        all_dates = list(date_range_forward(start_date, end_date))
    elif not weekdays_only and sort_newest_first:
        all_dates = list(date_range_backward(end_date, start_date))

    if db_table:
        # check_identity returns the set of expected trading days that are
        # NOT in the identity table; the present set is the complement within
        # all_dates. skip_holidays matches the weekdays_only filter so the
        # expected-date generation matches all_dates.
        from _common.pre_check_and_load import check_identity
        missing_dates = check_identity(
            db_table, start_date, end_date,
            date_column=db_date_column,
            code_suffix=db_code_suffix,
            skip_holidays=weekdays_only,
        )
        present_set = set(all_dates) - missing_dates
        present_by_prefix: Dict[str, Set[date]] = {p: set(present_set) for p in prefixes}
        if skip_empty_markers:
            # Also exclude dates with local empty-marker CSVs (already tried,
            # no data found, not yet in DB).
            empty_marker_dates = scan_present_day_keys(
                out_dir, prefixes=prefixes, min_bytes=0, ext_glob="*.csv",
            )
            for p in prefixes:
                present_by_prefix[p] |= empty_marker_dates.get(p, set())
    else:
        present_by_prefix = scan_present_day_keys(
            out_dir, prefixes=prefixes, min_bytes=min_bytes, ext_glob=ext_glob,
        )
        if skip_empty_markers:
            # Also count dates with empty-marker CSVs (0 bytes) as present.
            empty_marker_dates = scan_present_day_keys(
                out_dir, prefixes=prefixes, min_bytes=0, ext_glob="*.csv",
            )
            for p in prefixes:
                present_by_prefix[p] |= empty_marker_dates.get(p, set())

    plan = DayDownloadPlan()
    plan.total_expected = len(all_dates) * len(type_keys)

    present_total = 0
    for tk in type_keys:
        prefix = prefix_map[tk]
        present = present_by_prefix.get(prefix, set())
        present_total += len(present & set(all_dates))

    plan.present_count = present_total

    for d in all_dates:
        for tk in type_keys:
            prefix = prefix_map[tk]
            present = present_by_prefix.get(prefix, set())
            if d not in present:
                plan.items.append(DayDownloadPlanItem(type_key=tk, prefix=prefix, day=d))

    return plan


@dataclass
class YearDownloadPlanItem:
    type_key: str
    prefix: str
    year: int


@dataclass
class YearDownloadPlan:
    items: List[YearDownloadPlanItem] = field(default_factory=list)
    present_count: int = 0
    total_expected: int = 0

    def summary_str(self) -> str:
        return (
            f"expected={self.total_expected} cached={self.present_count} "
            f"missing_to_download={len(self.items)}"
        )


def build_year_download_plan(
    *,
    out_dir: Path,
    start_date: date,
    end_date: date,
    type_configs: Dict[str, Dict[str, Any]],
    min_bytes: int = MIN_VALID_BYTES,
    always_refresh_years: Optional[Set[int]] = None,
    ext_glob: str = "*.xlsx",
    db_table: Optional[str] = None,
    db_date_column: str = "date",
) -> YearDownloadPlan:
    """Build a per-year download plan.

    When *db_table* is provided, years with NO row in the DB table within
    [start_date, end_date] are computed via
    :func:`_common.pre_check_and_load.check_identity_years` instead of scanning the local
    filesystem. A year is "present" if it has at least one row.
    """
    type_keys = list(type_configs.keys())
    prefix_map = {tk: type_configs[tk]["prefix"] for tk in type_keys}
    prefixes = list(prefix_map.values())

    years = list(range(start_date.year, end_date.year + 1))

    if db_table:
        from _common.pre_check_and_load import check_identity_years
        missing_years = check_identity_years(
            db_table, start_date, end_date,
            date_column=db_date_column,
        )
        present_years_set = set(years) - missing_years
        present_by_prefix: Dict[str, Set[int]] = {p: set(present_years_set) for p in prefixes}
    else:
        present_by_prefix = scan_present_year_keys(
            out_dir, prefixes=prefixes, min_bytes=min_bytes, ext_glob=ext_glob,
        )

    plan = YearDownloadPlan()
    plan.total_expected = len(years) * len(type_keys)

    always_refresh = always_refresh_years or set()

    for tk in type_keys:
        prefix = prefix_map[tk]
        present = present_by_prefix.get(prefix, set())
        for y in years:
            if y in present and y not in always_refresh:
                plan.present_count += 1
            else:
                plan.items.append(YearDownloadPlanItem(type_key=tk, prefix=prefix, year=y))

    return plan


@dataclass
class ChunkDownloadPlanItem:
    type_key: str
    prefix: str
    chunk_start: date
    chunk_end: date


@dataclass
class ChunkDownloadPlan:
    items: List[ChunkDownloadPlanItem] = field(default_factory=list)
    present_count: int = 0
    total_expected: int = 0

    def summary_str(self) -> str:
        return (
            f"expected={self.total_expected} cached={self.present_count} "
            f"missing_to_download={len(self.items)}"
        )


def build_chunk_download_plan(
    *,
    out_dir: Path,
    chunks_by_type: Dict[str, List[Tuple[date, date]]],
    type_configs: Dict[str, Dict[str, Any]],
    min_bytes: int = MIN_VALID_BYTES,
    ext_glob: str = "*.xlsx",
    db_table: Optional[str] = None,
    db_date_column: str = "date",
) -> ChunkDownloadPlan:
    """Build a per-chunk download plan.

    When *db_table* is provided, a chunk is considered "present" if its end
    date exists in the DB table. Missing chunk-end dates are computed via
    :func:`_common.pre_check_and_load.check_identity` with ``skip_holidays=False`` (chunk
    end dates may fall on non-trading days). A chunk is "missing" if its
    end date is in the missing set.

    This is a heuristic — the build script processes chunks sequentially, so
    the end date being present implies the chunk was fully processed.
    """
    type_keys = list(type_configs.keys())
    prefix_map = {tk: type_configs[tk]["prefix"] for tk in type_keys}
    prefixes = list(prefix_map.values())

    if db_table:
        # Find the overall span covering every chunk's [cs, ce] so we can
        # run a single check_identity query covering all chunk end dates.
        all_chunks_flat = [c for chunks in chunks_by_type.values() for c in chunks]
        if all_chunks_flat:
            min_cs = min(c[0] for c in all_chunks_flat)
            max_ce = max(c[1] for c in all_chunks_flat)
            from _common.pre_check_and_load import check_identity
            # skip_holidays=False because chunk end dates may be weekends/holidays
            missing_dates = check_identity(
                db_table, min_cs, max_ce,
                date_column=db_date_column,
                skip_holidays=False,
            )
        else:
            missing_dates = set()
        # A chunk (cs, ce) is "present" if ce is NOT in the missing set.
        present_by_prefix: Dict[str, Set[Tuple[date, date]]] = {
            p: {(cs, ce) for (cs, ce) in chunks_by_type.get(tk, []) if ce not in missing_dates}
            for p, tk in zip(prefixes, type_keys)
        }
    else:
        present_by_prefix = scan_present_chunk_keys(
            out_dir, prefixes=prefixes, min_bytes=min_bytes, ext_glob=ext_glob,
        )

    plan = ChunkDownloadPlan()

    for tk in type_keys:
        prefix = prefix_map[tk]
        present = present_by_prefix.get(prefix, set())
        chunks = chunks_by_type.get(tk, [])
        plan.total_expected += len(chunks)
        for (cs, ce) in chunks:
            key = (cs, ce)
            if key in present:
                plan.present_count += 1
            else:
                plan.items.append(
                    ChunkDownloadPlanItem(type_key=tk, prefix=prefix, chunk_start=cs, chunk_end=ce)
                )

    return plan


# ---------------------------------------------------------------------------
# Host blocking detection: track 4xx errors per host and skip subsequent requests
# ---------------------------------------------------------------------------

@dataclass
class HostStatus:
    blocked: bool = False
    blocked_reason: str = ""
    last_error_time: float = 0.0
    error_count: int = 0


class HostStatusTracker:
    def __init__(self):
        self._host_status: Dict[str, HostStatus] = {}

    def is_blocked(self, url: str) -> bool:
        host = self._extract_host(url)
        status = self._host_status.get(host)
        return status is not None and status.blocked

    def record_error(self, url: str, status_code: int, reason: str = "") -> None:
        host = self._extract_host(url)
        status = self._host_status.setdefault(host, HostStatus())
        status.error_count += 1
        status.last_error_time = time.time()
        if 400 <= status_code < 500:
            status.blocked = True
            status.blocked_reason = reason or f"HTTP {status_code}"
            logger = setup_logger("host_tracker")
            logger.warning("Host %s blocked due to %s", host, status.blocked_reason)

    def unblock(self, url: str) -> None:
        host = self._extract_host(url)
        if host in self._host_status:
            self._host_status[host].blocked = False
            self._host_status[host].blocked_reason = ""

    def get_status(self, url: str) -> Optional[HostStatus]:
        host = self._extract_host(url)
        return self._host_status.get(host)

    @staticmethod
    def _extract_host(url: str) -> str:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            return parsed.netloc or parsed.hostname or url
        except Exception:
            return url


# ---------------------------------------------------------------------------
# Unified Anti-Bot Proxy: consolidates all anti-bot mechanisms into one class
# ---------------------------------------------------------------------------

@dataclass
class AntiBotConfig:
    """Configuration for anti-bot behavior.
    
    All anti-bot features are enabled by default and can be selectively disabled.
    """
    # Browser fingerprint rotation
    rotate_browser_profile: bool = True
    # Add random parameter to requests
    add_random_param: bool = True
    # Sleep between requests
    enable_sleep: bool = True
    # Base sleep duration (seconds)
    base_sleep_sec: float = DEFAULT_SLEEP_SEC
    # Jitter factor for sleep (0.0 = no jitter, 1.0 = 100% jitter)
    sleep_jitter: float = 0.5
    # Track host blocking (4xx errors)
    enable_host_tracking: bool = True
    # Timeout for requests
    timeout: Tuple[int, int] = DEFAULT_TIMEOUT


class AntiBotProxy:
    """Unified anti-bot proxy that consolidates browser fingerprint rotation,
    sleep with jitter, host blocking detection, and request parameter randomization.
    
    This class provides a single interface for all anti-bot mechanisms, making
    it easy to configure and use across the entire codebase.
    
    Example usage:
        proxy = AntiBotProxy(base_sleep_sec=20.0)
        session = requests.Session()
        
        # Simple GET with anti-bot protection
        resp = proxy.get(session, url, headers=base_headers)
        
        # POST with custom sleep
        resp = proxy.post(session, url, data=payload, sleep_sec=30.0)
        
        # Manual sleep after processing
        proxy.sleep()
        
        # Check if host is blocked
        if proxy.is_blocked(url):
            # Handle blocked host
            pass
    """
    
    def __init__(self, config: Optional[AntiBotConfig] = None):
        """Initialize the anti-bot proxy with optional configuration.
        
        Args:
            config: AntiBotConfig instance. If None, defaults are used.
        """
        self.config = config or AntiBotConfig()
        self._host_tracker = HostStatusTracker() if self.config.enable_host_tracking else None
    
    def get(
        self,
        session: requests.Session,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[Tuple[int, int]] = None,
        sleep_sec: Optional[float] = None,
        anti_bot: bool = True,
        logger: Optional[logging.Logger] = None,
        log_tag: str = "",
    ) -> Optional[requests.Response]:
        """Perform a GET request with anti-bot protection.
        
        Args:
            session: requests.Session instance
            url: URL to fetch
            params: Query parameters
            headers: Request headers
            timeout: Request timeout (overrides config.timeout)
            sleep_sec: Custom sleep duration after request
            anti_bot: If False, skip anti-bot mechanisms
            logger: Logger for warnings/errors
            log_tag: Tag for log messages
        
        Returns:
            requests.Response on success, None on failure or blocked host
        """
        return self._request(
            session, "get", url,
            params=params, headers=headers, data=None,
            timeout=timeout, sleep_sec=sleep_sec,
            anti_bot=anti_bot, logger=logger, log_tag=log_tag,
        )
    
    def post(
        self,
        session: requests.Session,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Any] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[Tuple[int, int]] = None,
        sleep_sec: Optional[float] = None,
        anti_bot: bool = True,
        logger: Optional[logging.Logger] = None,
        log_tag: str = "",
    ) -> Optional[requests.Response]:
        """Perform a POST request with anti-bot protection.
        
        Args:
            session: requests.Session instance
            url: URL to fetch
            params: Query parameters
            data: POST body data
            headers: Request headers
            timeout: Request timeout (overrides config.timeout)
            sleep_sec: Custom sleep duration after request
            anti_bot: If False, skip anti-bot mechanisms
            logger: Logger for warnings/errors
            log_tag: Tag for log messages
        
        Returns:
            requests.Response on success, None on failure or blocked host
        """
        return self._request(
            session, "post", url,
            params=params, headers=headers, data=data,
            timeout=timeout, sleep_sec=sleep_sec,
            anti_bot=anti_bot, logger=logger, log_tag=log_tag,
        )
    
    def _request(
        self,
        session: requests.Session,
        method: str,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        data: Optional[Any] = None,
        timeout: Optional[Tuple[int, int]] = None,
        sleep_sec: Optional[float] = None,
        anti_bot: bool = True,
        logger: Optional[logging.Logger] = None,
        log_tag: str = "",
    ) -> Optional[requests.Response]:
        """Internal request handler with anti-bot protection."""
        # Check host blocking
        if self._host_tracker and self._host_tracker.is_blocked(url):
            if logger:
                logger.warning("%sskipping request to blocked host: %s", log_tag, url)
            return None
        
        # Prepare parameters with anti-bot randomization
        final_params = dict(params or {})
        if anti_bot and self.config.add_random_param:
            final_params["random"] = random.random()
        
        # Prepare headers with browser fingerprint rotation
        final_headers = dict(headers or {})
        if anti_bot and self.config.rotate_browser_profile:
            final_headers = merge_browser_profile(final_headers)
        
        # Use custom or configured timeout
        request_timeout = timeout if timeout is not None else self.config.timeout
        
        try:
            if method == "get":
                resp = session.get(url, params=final_params, headers=final_headers, timeout=request_timeout)
            else:
                resp = session.post(url, params=final_params, data=data, headers=final_headers, timeout=request_timeout)
            
            # Handle 4xx errors (potential blocking)
            if 400 <= resp.status_code < 500:
                if self._host_tracker:
                    self._host_tracker.record_error(url, resp.status_code, f"HTTP {resp.status_code}")
                if logger:
                    logger.error("%sHTTP %d for %s", log_tag, resp.status_code, url)
                return None
            
            resp.raise_for_status()
            
            # Sleep after successful request
            if self.config.enable_sleep:
                self.sleep(sleep_sec=sleep_sec)
            
            return resp
            
        except requests.RequestException as e:
            status_code = getattr(e.response, "status_code", None)
            if status_code is not None and 400 <= status_code < 500 and self._host_tracker:
                self._host_tracker.record_error(url, status_code, str(e))
            if logger:
                logger.warning("%sRequest failed: %s", log_tag, e)
            return None
    
    def sleep(self, sleep_sec: Optional[float] = None) -> None:
        """Sleep with jitter based on configured base sleep duration.
        
        Args:
            sleep_sec: Custom sleep duration. If None, uses config.base_sleep_sec.
        """
        if not self.config.enable_sleep:
            return
        
        base = sleep_sec if sleep_sec is not None else self.config.base_sleep_sec
        if base <= 0:
            return
        
        jitter = base * self.config.sleep_jitter
        sleep_time = random.uniform(base - jitter, base + jitter)
        time.sleep(max(0, sleep_time))
    
    def sleep_range(self, min_sec: float, max_sec: float) -> None:
        """Sleep for a random duration between min_sec and max_sec.
        
        Args:
            min_sec: Minimum sleep duration
            max_sec: Maximum sleep duration
        """
        if not self.config.enable_sleep:
            return
        
        if min_sec <= 0 and max_sec <= 0:
            return
        
        sleep_time = random.uniform(min(min_sec, max_sec), max(min_sec, max_sec))
        time.sleep(max(0, sleep_time))
    
    def is_blocked(self, url: str) -> bool:
        """Check if the host for the given URL is blocked.
        
        Args:
            url: URL to check
        
        Returns:
            True if blocked, False otherwise or if host tracking is disabled
        """
        if self._host_tracker is None:
            return False
        return self._host_tracker.is_blocked(url)
    
    def unblock(self, url: str) -> None:
        """Unblock the host for the given URL.
        
        Args:
            url: URL whose host should be unblocked
        """
        if self._host_tracker is not None:
            self._host_tracker.unblock(url)
    
    def record_error(self, url: str, status_code: int, reason: str = "") -> None:
        """Record an error for the host of the given URL.
        
        Args:
            url: URL where error occurred
            status_code: HTTP status code
            reason: Optional reason for the error
        """
        if self._host_tracker is not None:
            self._host_tracker.record_error(url, status_code, reason)
    
    def get_host_status(self, url: str) -> Optional[HostStatus]:
        """Get the status of the host for the given URL.
        
        Args:
            url: URL to check
        
        Returns:
            HostStatus if available, None otherwise
        """
        if self._host_tracker is None:
            return None
        return self._host_tracker.get_status(url)


# ---------------------------------------------------------------------------
# Unified HTTP request functions with anti-bot mechanisms and 4xx detection
# 
# Note: These functions are now wrappers around AntiBotProxy for backward
# compatibility. New code should prefer using AntiBotProxy directly.
# ---------------------------------------------------------------------------

def safe_get(
    session: requests.Session,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: Tuple[int, int] = DEFAULT_TIMEOUT,
    host_tracker: Optional[HostStatusTracker] = None,
    anti_bot: bool = True,
    logger: Optional[logging.Logger] = None,
    log_tag: str = "",
) -> Optional[requests.Response]:
    """Backward-compatible wrapper for GET requests with anti-bot protection.
    
    This function is now implemented using AntiBotProxy internally. New code
    should prefer using AntiBotProxy directly for more flexibility.
    
    Args:
        session: requests.Session instance
        url: URL to fetch
        params: Query parameters
        headers: Request headers
        timeout: Request timeout
        host_tracker: Optional HostStatusTracker for blocking detection
        anti_bot: If False, skip anti-bot mechanisms
        logger: Logger for warnings/errors
        log_tag: Tag for log messages
    
    Returns:
        requests.Response on success, None on failure or blocked host
    """
    # Create a temporary AntiBotProxy with host_tracker if provided
    config = AntiBotConfig(
        rotate_browser_profile=anti_bot,
        add_random_param=anti_bot,
        enable_sleep=False,  # Legacy safe_get doesn't sleep automatically
        enable_host_tracking=host_tracker is not None,
        timeout=timeout,
    )
    proxy = AntiBotProxy(config)
    
    # If a host_tracker was provided, use it instead of creating a new one
    if host_tracker is not None:
        proxy._host_tracker = host_tracker
    
    return proxy.get(
        session, url,
        params=params,
        headers=headers,
        anti_bot=anti_bot,
        logger=logger,
        log_tag=log_tag,
    )


def safe_post(
    session: requests.Session,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    data: Optional[Any] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: Tuple[int, int] = DEFAULT_TIMEOUT,
    host_tracker: Optional[HostStatusTracker] = None,
    anti_bot: bool = True,
    logger: Optional[logging.Logger] = None,
    log_tag: str = "",
) -> Optional[requests.Response]:
    """Backward-compatible wrapper for POST requests with anti-bot protection.
    
    This function is now implemented using AntiBotProxy internally. New code
    should prefer using AntiBotProxy directly for more flexibility.
    
    Args:
        session: requests.Session instance
        url: URL to fetch
        params: Query parameters
        data: POST body data
        headers: Request headers
        timeout: Request timeout
        host_tracker: Optional HostStatusTracker for blocking detection
        anti_bot: If False, skip anti-bot mechanisms
        logger: Logger for warnings/errors
        log_tag: Tag for log messages
    
    Returns:
        requests.Response on success, None on failure or blocked host
    """
    # Create a temporary AntiBotProxy with host_tracker if provided
    config = AntiBotConfig(
        rotate_browser_profile=anti_bot,
        add_random_param=anti_bot,
        enable_sleep=False,  # Legacy safe_post doesn't sleep automatically
        enable_host_tracking=host_tracker is not None,
        timeout=timeout,
    )
    proxy = AntiBotProxy(config)
    
    # If a host_tracker was provided, use it instead of creating a new one
    if host_tracker is not None:
        proxy._host_tracker = host_tracker
    
    return proxy.post(
        session, url,
        params=params,
        data=data,
        headers=headers,
        anti_bot=anti_bot,
        logger=logger,
        log_tag=log_tag,
    )


# ---------------------------------------------------------------------------
# Execution helpers: iterate over a plan and run user callbacks
# ---------------------------------------------------------------------------

@dataclass
class RunStats:
    downloaded: int = 0
    skipped_cached: int = 0
    failed: int = 0
    empty: int = 0
    files: List[str] = field(default_factory=list)

    def to_dict(self, **extra: Any) -> Dict[str, Any]:
        d = {
            "downloaded": self.downloaded,
            "skipped_cached": self.skipped_cached,
            "failed": self.failed,
            "empty": self.empty,
            "files": list(self.files),
        }
        d.update(extra)
        return d


def run_plan_with_sleep(
    items: Iterable[Any],
    *,
    download_fn: Callable[[Any], Optional[Path]],
    sleep_sec: float,
    stats: Optional[RunStats] = None,
    logger: Optional[logging.Logger] = None,
    log_label: str = "",
    quick_sleep_multiplier: float = 0.1,
) -> RunStats:
    stats = stats or RunStats()
    try:
        for item in items:
            result_path = download_fn(item)
            if result_path is not None:
                stats.downloaded += 1
                stats.files.append(str(result_path))
            else:
                stats.failed += 1
            time.sleep(sleep_sec)
    except KeyboardInterrupt:
        if logger:
            logger.warning("%sInterrupted by user", log_label)
    return stats
