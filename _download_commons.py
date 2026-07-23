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


MIN_VALID_BYTES = 1024
EMPTY_HTML_MAX_BYTES = 8192
DEFAULT_TIMEOUT: Tuple[int, int] = (15, 60)

# Shared default sleep seconds between HTTP requests for anti-bot protection.
# Centralized here so the project's anti-bot policy can be changed in one place.
# Individual downloaders may override based on target site's aggressiveness.
DEFAULT_SLEEP_SEC = 20.0

# Shared default start date for all downloaders. Centralized here so the
# project's historical backfill horizon can be changed in one place.
DEFAULT_START_DATE = "2022-01-01"


def _parse_dates(date_strings: List[str]) -> Set[date]:
    return {datetime.strptime(s, "%Y-%m-%d").date() for s in date_strings}


CN_HOLIDAYS: Set[date] = _parse_dates([
    # 2021
    "2021-01-01",
    "2021-02-11", "2021-02-12", "2021-02-13", "2021-02-14", "2021-02-15", "2021-02-16", "2021-02-17",
    "2021-04-04", "2021-04-05", "2021-04-06",
    "2021-05-01", "2021-05-02", "2021-05-03", "2021-05-04", "2021-05-05",
    "2021-06-14",
    "2021-09-21",
    "2021-10-01", "2021-10-02", "2021-10-03", "2021-10-04", "2021-10-05", "2021-10-06", "2021-10-07",
    # 2022
    "2022-01-01",
    "2022-01-31", "2022-02-01", "2022-02-02", "2022-02-03", "2022-02-04", "2022-02-05", "2022-02-06",
    "2022-04-03", "2022-04-04", "2022-04-05",
    "2022-05-01", "2022-05-02", "2022-05-03", "2022-05-04",
    "2022-06-03", "2022-06-04", "2022-06-05",
    "2022-09-10", "2022-09-11", "2022-09-12",
    "2022-10-01", "2022-10-02", "2022-10-03", "2022-10-04", "2022-10-05", "2022-10-06", "2022-10-07",
    # 2023
    "2023-01-01",
    "2023-01-21", "2023-01-22", "2023-01-23", "2023-01-24", "2023-01-25", "2023-01-26", "2023-01-27",
    "2023-04-05",
    "2023-05-01", "2023-05-02", "2023-05-03", "2023-05-04", "2023-05-05",
    "2023-06-22", "2023-06-23", "2023-06-24",
    "2023-09-29", "2023-09-30",
    "2023-10-01", "2023-10-02", "2023-10-03", "2023-10-04", "2023-10-05", "2023-10-06",
    # 2024
    "2024-01-01",
    "2024-02-10", "2024-02-11", "2024-02-12", "2024-02-13", "2024-02-14", "2024-02-15", "2024-02-16", "2024-02-17",
    "2024-04-04", "2024-04-05", "2024-04-06",
    "2024-05-01", "2024-05-02", "2024-05-03", "2024-05-04", "2024-05-05",
    "2024-06-10",
    "2024-09-17",
    "2024-10-01", "2024-10-02", "2024-10-03", "2024-10-04", "2024-10-05", "2024-10-06", "2024-10-07",
    # 2025
    "2025-01-01",
    "2025-01-29", "2025-01-30", "2025-01-31", "2025-02-01", "2025-02-02", "2025-02-03", "2025-02-04",
    "2025-04-04",
    "2025-05-01", "2025-05-02", "2025-05-03", "2025-05-04", "2025-05-05",
    "2025-06-02",
    "2025-09-08",
    "2025-10-01", "2025-10-02", "2025-10-03", "2025-10-04", "2025-10-05", "2025-10-06", "2025-10-07",
    # 2026
    "2026-01-01",
    "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20", "2026-02-21", "2026-02-22", "2026-02-23",
    "2026-04-06",
    "2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04", "2026-05-05",
    "2026-06-22",
    "2026-09-28",
    "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05", "2026-10-06", "2026-10-07",
])


CN_ADJUSTED_WORKDAYS: Set[date] = _parse_dates([
    # 2021
    "2021-02-07", "2021-02-20",
    "2021-04-03",
    "2021-04-25", "2021-05-08",
    "2021-06-13",
    "2021-09-19",
    "2021-09-26", "2021-10-09",
    # 2022
    "2022-01-29", "2022-01-30",
    "2022-04-02",
    "2022-04-30",
    "2022-09-18",
    "2022-09-25", "2022-10-08",
    # 2023
    "2023-01-28", "2023-01-29",
    "2023-04-08",
    "2023-04-29", "2023-04-30",
    "2023-06-25",
    "2023-10-07", "2023-10-08",
    # 2024
    "2024-02-04", "2024-02-18",
    "2024-04-07",
    "2024-04-28", "2024-05-11",
    "2024-06-08",
    "2024-09-15",
    "2024-09-29", "2024-10-12",
    # 2025
    "2025-01-26", "2025-01-27",
    "2025-04-07",
    "2025-04-27", "2025-05-10",
    "2025-06-01",
    "2025-09-06",
    "2025-09-28", "2025-10-11",
    # 2026
    "2026-02-14", "2026-02-24",
    "2026-04-05",
    "2026-04-26", "2026-05-09",
    "2026-06-20",
    "2026-09-26",
    "2026-09-27", "2026-10-10",
])

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


def resolve_out_dir(
    caller_file: str,
    out_dirname: str,
    out_root: Optional[str] = None,
) -> Path:
    script_dir = Path(caller_file).resolve().parent
    out_dir = Path(out_root) if out_root else script_dir / "temps" / out_dirname
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def is_trading_day(d: date) -> bool:
    if d in CN_ADJUSTED_WORKDAYS:
        return True
    if d in CN_HOLIDAYS:
        return False
    return d.weekday() < 5


def last_business_day(ref: Optional[date] = None) -> date:
    """Return the most recent trading day on or before ``ref`` (default: today).

    Takes into account weekends, Chinese public holidays (CN_HOLIDAYS), and
    adjusted workdays (CN_ADJUSTED_WORKDAYS).
    """
    d = ref if ref is not None else date.today()
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def next_business_day(ref: Optional[date] = None) -> date:
    """Return the next trading day on or after ``ref`` (default: today).

    Takes into account weekends, Chinese public holidays (CN_HOLIDAYS), and
    adjusted workdays (CN_ADJUSTED_WORKDAYS).
    """
    d = ref if ref is not None else date.today()
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def date_range_backward(end_date: date, start_date: date) -> Iterable[date]:
    cur = end_date
    while cur >= start_date:
        yield cur
        cur -= timedelta(days=1)


def date_range_forward(start_date: date, end_date: date) -> Iterable[date]:
    cur = start_date
    while cur <= end_date:
        yield cur
        cur += timedelta(days=1)


def count_weekdays(start_date: date, end_date: date) -> int:
    return sum(1 for d in date_range_backward(end_date, start_date) if is_trading_day(d))


def business_days(start_date: date, end_date: date, *, reverse: bool = True) -> List[date]:
    gen = date_range_backward(end_date, start_date) if reverse else date_range_forward(start_date, end_date)
    return [d for d in gen if is_trading_day(d)]


def parse_date_window(
    *,
    end_date: Optional[str] = None,
    start_date: Optional[str] = None,
    default_end: Optional[date] = None,
    lookback_days: Optional[int] = None,
    lookback_years: Optional[int] = None,
) -> Tuple[date, date]:
    today = date.today()
    if end_date:
        _end = datetime.strptime(end_date, "%Y-%m-%d").date()
    elif default_end is not None:
        _end = default_end
    else:
        _end = last_business_day(today)
    if start_date:
        _start = datetime.strptime(start_date, "%Y-%m-%d").date()
    else:
        if lookback_years is not None:
            extra_days = int(lookback_years * 365 + 30)
            _start = _end - timedelta(days=extra_days)
        elif lookback_days is not None:
            _start = _end - timedelta(days=lookback_days)
        else:
            _start = _end - timedelta(days=365 * 3 + 30)
    if _end < _start:
        raise ValueError(f"end_date ({_end}) must be >= start_date ({_start})")
    return _start, _end


def is_valid_file(path: Path, *, min_bytes: int = MIN_VALID_BYTES) -> bool:
    try:
        return path.exists() and path.is_file() and path.stat().st_size >= min_bytes
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


def convert_xlsx_to_csv(
    xlsx_path: Path,
    *,
    sheet_name: Any = 0,
    csv_path: Optional[Path] = None,
    encoding: str = "utf-8-sig",
    logger: Optional[logging.Logger] = None,
    log_tag: str = "",
    code_suffix: str = "",
) -> Optional[Path]:
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
            if code_suffix:
                first_sheet = normalize_code_column(first_sheet, code_suffix)
            first_sheet.to_csv(csv_path, index=False, encoding=encoding)
            n_sheets = len(df)
            rows = len(first_sheet)
        else:
            df = normalize_dataframe_numbers(df)
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
) -> bool:
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

SHANGHAI_INDEX_CODES = {
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
        if code in SHANGHAI_INDEX_CODES:
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
    exchange = get_exchange_from_code(code)
    if exchange:
        return code + "." + exchange
    import warnings
    warnings.warn(
        f"Cannot determine exchange for stock code '{code}'. "
        f"Codes 000xxx/001xxx are ambiguous (used by both Shanghai indices and Shenzhen stocks). "
        f"Pass 'market' parameter explicitly. Returning code without suffix."
    )
    return code


def strip_exchange_suffix(stock_code: str) -> str:
    code = str(stock_code).strip()
    if "." in code:
        parts = code.split(".")
        if len(parts) == 2 and parts[1] in ("SS", "SZ"):
            return parts[0]
    return code


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
) -> DayDownloadPlan:
    type_keys = list(type_configs.keys())
    prefix_map = {tk: type_configs[tk]["prefix"] for tk in type_keys}
    prefixes = list(prefix_map.values())

    present_by_prefix = scan_present_day_keys(
        out_dir, prefixes=prefixes, min_bytes=min_bytes, ext_glob=ext_glob,
    )

    all_dates = business_days(start_date, end_date, reverse=sort_newest_first) if weekdays_only else list(
        reversed(list(date_range_backward(end_date, start_date))) if sort_newest_first else list(date_range_forward(start_date, end_date))
    )
    if not weekdays_only and not sort_newest_first:
        all_dates = list(date_range_forward(start_date, end_date))
    elif not weekdays_only and sort_newest_first:
        all_dates = list(date_range_backward(end_date, start_date))

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
) -> YearDownloadPlan:
    type_keys = list(type_configs.keys())
    prefix_map = {tk: type_configs[tk]["prefix"] for tk in type_keys}
    prefixes = list(prefix_map.values())

    present_by_prefix = scan_present_year_keys(
        out_dir, prefixes=prefixes, min_bytes=min_bytes, ext_glob=ext_glob,
    )

    years = list(range(start_date.year, end_date.year + 1))
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
) -> ChunkDownloadPlan:
    type_keys = list(type_configs.keys())
    prefix_map = {tk: type_configs[tk]["prefix"] for tk in type_keys}
    prefixes = list(prefix_map.values())

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
# Unified HTTP request functions with anti-bot mechanisms and 4xx detection
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
    if host_tracker and host_tracker.is_blocked(url):
        if logger:
            logger.warning("%sskipping request to blocked host: %s", log_tag, url)
        return None

    final_params = dict(params or {})
    if anti_bot:
        final_params["random"] = random.random()

    final_headers = dict(headers or {})
    if anti_bot:
        final_headers = merge_browser_profile(final_headers)

    try:
        resp = session.get(url, params=final_params, headers=final_headers, timeout=timeout)
        if 400 <= resp.status_code < 500:
            if host_tracker:
                host_tracker.record_error(url, resp.status_code, f"HTTP {resp.status_code}")
            if logger:
                logger.error("%sHTTP %d for %s", log_tag, resp.status_code, url)
            return None
        resp.raise_for_status()
        return resp
    except requests.RequestException as e:
        status_code = getattr(e.response, "status_code", None)
        if status_code is not None and 400 <= status_code < 500 and host_tracker:
            host_tracker.record_error(url, status_code, str(e))
        if logger:
            logger.warning("%sRequest failed: %s", log_tag, e)
        return None


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
    if host_tracker and host_tracker.is_blocked(url):
        if logger:
            logger.warning("%sskipping request to blocked host: %s", log_tag, url)
        return None

    final_params = dict(params or {})
    if anti_bot:
        final_params["random"] = random.random()

    final_headers = dict(headers or {})
    if anti_bot:
        final_headers = merge_browser_profile(final_headers)

    try:
        resp = session.post(
            url,
            params=final_params,
            data=data,
            headers=final_headers,
            timeout=timeout,
        )
        if 400 <= resp.status_code < 500:
            if host_tracker:
                host_tracker.record_error(url, resp.status_code, f"HTTP {resp.status_code}")
            if logger:
                logger.error("%sHTTP %d for %s", log_tag, resp.status_code, url)
            return None
        resp.raise_for_status()
        return resp
    except requests.RequestException as e:
        status_code = getattr(e.response, "status_code", None)
        if status_code is not None and 400 <= status_code < 500 and host_tracker:
            host_tracker.record_error(url, status_code, str(e))
        if logger:
            logger.warning("%sRequest failed: %s", log_tag, e)
        return None


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
