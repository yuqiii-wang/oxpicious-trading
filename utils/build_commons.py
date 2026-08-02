"""
_build_commons.py — Shared utilities for build_* scripts.

Consolidates the common logic duplicated across the 8 build scripts:

  • Windows UTF-8 stdout setup                  → setup_utf8_stdout()
  • Number / date / time parsing                → parse_num / parse_date / parse_time
  • Filename YMD extraction + date filtering    → ymd_from_filename / in_range / ymd_to_date
  • Common argparse (--start-date/--end-date)   → add_common_build_args()
  • DB connection with graceful exit            → get_db_or_exit()
  • Missing-data detection against DB           → find_missing_dates / find_missing_keys
  • Source-CSV filtering by missing dates       → filter_source_files_by_missing_dates
  • Consistent build-script header              → print_build_header()

Re-exports the async DB primitives from _db_commons so build scripts can
import everything they need from a single module:

    from _build_commons import (
        setup_utf8_stdout, parse_num, parse_date, parse_time,
        ymd_from_filename, in_range, ymd_to_date, ymd_to_iso,
        add_common_build_args, get_db_or_exit,
        find_missing_dates, find_missing_keys,
        filter_source_files_by_missing_dates, print_build_header,
        get_db_connection_async, get_existing_keys_async,
        bulk_upsert_async, truncate_table_async,
    )

Design principle: build scripts query the DB FIRST to learn which
(date, code) pairs are already present, then read ONLY the source CSV
files whose dates are missing, and insert only the missing rows. This
replaces the old "build the whole dataset then skip existing rows"
pattern.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import glob
import locale as _locale
import os
import re
import sys
from typing import Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Re-export async DB primitives (single import source for build scripts)
# ---------------------------------------------------------------------------
from utils.db_commons import (
    get_db_connection_async,
    get_existing_keys_async,
    bulk_upsert_async,
    truncate_table_async,
    fetch_codes_with_recent_data_async,
    RECENT_TRADING_DAYS,
)
from utils._holidays_and_weekdays import recent_trading_day_cutoff


# ============================================================================
# stdout encoding (Windows console fix) — duplicated in 6/8 build scripts
# ============================================================================
def setup_utf8_stdout() -> None:
    """Force UTF-8 on stdout/stderr so Chinese ETF/index names print
    correctly on the Windows default console (cp936/gbk).

    Idempotent: safe to call multiple times. Silently no-ops on POSIX.
    """
    try:
        _locale.setlocale(_locale.LC_ALL, "")
    except Exception:
        pass
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# ============================================================================
# Number / date / time parsing
# ============================================================================
_NUM_INVALID_TOKENS = {"", "--", "-", "—", "null", "NULL", "None", "nan", "NaN"}


def parse_num(s, default: float = 0.0) -> Optional[float]:
    """Coerce a string/number to float.

    Returns ``default`` on failure (NaN-like). Pass ``default=np.nan`` for
    the debt/index convention that propagates NaN, or ``default=0.0`` (the
    default) for the ETF/stock convention that fills zeros.

    Examples:
        parse_num("1,234.5")    → 1234.5
        parse_num("--")         → 0.0
        parse_num(None)         → 0.0
        parse_num("", np.nan)   → nan
    """
    if s is None:
        return default
    if isinstance(s, (int, float)):
        try:
            v = float(s)
            return v if np.isfinite(v) else default
        except Exception:
            return default
    txt = str(s).strip()
    if txt in _NUM_INVALID_TOKENS:
        return default
    txt = txt.replace(",", "").replace("，", "").replace(" ", "").replace("\u3000", "")
    try:
        v = float(txt)
        return v if np.isfinite(v) else default
    except Exception:
        return default


def parse_date(val) -> Optional[datetime.date]:
    """Parse a date value into a ``datetime.date`` object.

    Accepts ``YYYYMMDD``, ``YYYY-MM-DD``, ``YYYY/MM/DD``. Returns None on
    failure. Always returns ``datetime.date`` (not str) so asyncpg can
    encode it for a DATE column without raising
    "expected a date instance, got 'str'".
    """
    if val is None:
        return None
    if isinstance(val, datetime.date) and not isinstance(val, datetime.datetime):
        return val
    if isinstance(val, datetime.datetime):
        return val.date()
    s = str(val).strip()
    if not s:
        return None
    s = s.replace("-", "").replace("/", "")
    if len(s) == 8 and s.isdigit():
        try:
            return datetime.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        except ValueError:
            return None
    return None


def parse_time(val) -> Optional[datetime.time]:
    """Parse a HH:MM[:SS] string into a ``datetime.time`` object."""
    if val is None or isinstance(val, datetime.time):
        return val
    s = str(val).strip()
    if not s:
        return None
    parts = s.split(":")
    try:
        if len(parts) == 3:
            return datetime.time(int(parts[0]), int(parts[1]), int(parts[2]))
        if len(parts) == 2:
            return datetime.time(int(parts[0]), int(parts[1]))
    except ValueError:
        return None
    return None


# ============================================================================
# Filename / date-range helpers — duplicated in 3+ build scripts
# ============================================================================
def ymd_from_filename(path: str, prefix: str = "") -> Optional[str]:
    """Extract the YYYYMMDD token from a CSV filename.

    If ``prefix`` is given, the filename must start with it (after the
    basename). Returns None if no 8-digit date token is found.
    """
    b = os.path.basename(path)
    if prefix and not b.startswith(prefix):
        return None
    m = re.search(r"(\d{8})", b)
    return m.group(1) if m else None


def in_range(ymd: Optional[str], start_ymd: Optional[str], end_ymd: Optional[str]) -> bool:
    """Return True if ``ymd`` (YYYYMMDD string) is within [start, end]."""
    if ymd is None:
        return False
    if start_ymd and ymd < start_ymd:
        return False
    if end_ymd and ymd > end_ymd:
        return False
    return True


def ymd_to_date(ymd: str) -> Optional[datetime.date]:
    """Convert a YYYYMMDD string to a ``datetime.date``."""
    if not ymd or len(ymd) != 8 or not ymd.isdigit():
        return None
    try:
        return datetime.date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8]))
    except ValueError:
        return None


def ymd_to_iso(ymd: str) -> str:
    """Convert a YYYYMMDD string to 'YYYY-MM-DD' (no validation)."""
    if not ymd or len(ymd) != 8:
        return ymd or ""
    return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"


def iso_to_ymd(iso_date: str) -> str:
    """Convert 'YYYY-MM-DD' to YYYYMMDD (no validation)."""
    return (iso_date or "").replace("-", "")


# ============================================================================
# Common argparse — duplicated in 5/8 build scripts
# ============================================================================
def add_common_build_args(parser: argparse.ArgumentParser) -> None:
    """Add the --start-date / --end-date / --force flags used by every
    date-driven build script.

    Call after creating the parser; scripts may add their own additional
    arguments before or after.
    """
    parser.add_argument("--start-date", default=None, help="YYYY-MM-DD inclusive")
    parser.add_argument("--end-date",   default=None, help="YYYY-MM-DD inclusive")
    parser.add_argument("--force", action="store_true",
                        help="Rebuild all data (truncate target tables first)")


# ============================================================================
# DB connection helper
# ============================================================================
# Exceptions that are worth retrying: the DB may briefly be unreachable
# (Docker container restarting, transient TCP refusal, momentary timeout).
# Auth/catalog errors are NOT retried — those never succeed on retry.
# asyncpg is optional at import time (graceful fallback below).
try:
    import asyncpg as _asyncpg
    _TRANSIENT_EXC = (
        OSError,
        _asyncpg.PostgresConnectionError,
        _asyncpg.ConnectionDoesNotExistError,
        _asyncpg.ConnectionFailureError,
    )
except ImportError:  # pragma: no cover
    _asyncpg = None
    _TRANSIENT_EXC = (OSError,)

# (delay_seconds, label) for each retry attempt after the first failure.
# With a 30s connect timeout, the worst-case total before giving up is:
#   30 + 3 + 30 + 10 + 30 + 30 + 30 = 163s
# which is enough to outlast the heaviest checkpoints observed (149s, triggered
# by bulk inserts of hundreds of thousands of rows across multiple tables).
_RETRY_BACKOFF = [(3.0, "3s"), (10.0, "10s"), (30.0, "30s")]


async def get_db_or_exit():
    """Connect to the database (async) or sys.exit(1) on failure.

    Retries transient connection errors (OSError / asyncpg connection
    errors) with a short backoff before giving up. Auth and catalog errors
    (wrong password / wrong database) fail immediately without retry.

    Prints a FATAL line on failure with the exception type and repr so that
    empty-message asyncpg exceptions remain diagnosable. Used by every
    build script's main().
    """
    last_exc: Optional[Exception] = None
    for attempt in range(len(_RETRY_BACKOFF) + 1):
        try:
            conn = await get_db_connection_async()
            if attempt > 0:
                print(f"    [DB] Connected successfully on attempt "
                      f"{attempt + 1}", flush=True)
            else:
                print("    [DB] Connected successfully", flush=True)
            return conn
        except Exception as e:
            last_exc = e
            transient = isinstance(e, _TRANSIENT_EXC)
            if not transient or attempt >= len(_RETRY_BACKOFF):
                # Permanent error, or out of retries — give up.
                msg = str(e).strip()
                if msg:
                    print(f"    [FATAL] Database connection failed: "
                          f"{type(e).__name__}: {msg}", flush=True)
                else:
                    print(f"    [FATAL] Database connection failed: "
                          f"{type(e).__name__} (no message) repr={e!r}",
                          flush=True)
                sys.exit(1)
            # Transient — wait and retry.
            delay, label = _RETRY_BACKOFF[attempt]
            print(f"    [WARN] Connection attempt {attempt + 1} failed "
                  f"({type(e).__name__}); retrying in {label} …",
                  flush=True)
            await asyncio.sleep(delay)

    # Unreachable — loop always exits via return or sys.exit.
    print(f"    [FATAL] Database connection failed: {last_exc!r}", flush=True)
    sys.exit(1)


# ============================================================================
# Missing-data detection — the core of the new "only build what's missing" flow
# ============================================================================
async def find_missing_dates(
    conn,
    table: str,
    source_dates: Iterable[datetime.date],
) -> Set[datetime.date]:
    """Return the subset of ``source_dates`` not already present in ``table``.

    ``table`` must have a ``date`` column (the typical debt_identity /
    stock_identity / etf_identity / index_identity pattern).

    Returns an empty set if ``source_dates`` is empty or all dates are
    already in the DB. This is the date-only variant; for (date, code)
    tables use find_missing_keys().
    """
    source_set = set(source_dates)
    if not source_set:
        return set()
    existing = await get_existing_keys_async(conn, table, ["date"])
    # existing is a set of 1-tuples like {(date,), ...}
    existing_dates = {t[0] for t in existing}
    return source_set - existing_dates


async def find_missing_keys(
    conn,
    table: str,
    key_cols: Sequence[str],
    source_keys: Iterable[tuple],
) -> Set[tuple]:
    """Return the subset of ``source_keys`` not already present in ``table``.

    ``key_cols`` is e.g. ``["date", "code"]`` or ``["date", "contract_code"]``
    or ``["date", "code", "time"]``. ``source_keys`` is an iterable of tuples
    matching the column order.

    This is the multi-column variant; for date-only tables use
    find_missing_dates().
    """
    source_set = set(source_keys)
    if not source_set:
        return set()
    existing = await get_existing_keys_async(conn, table, list(key_cols))
    return source_set - existing


# ============================================================================
# Source-CSV filtering by missing dates
# ============================================================================
def filter_source_files_by_missing_dates(
    files: Sequence[str],
    missing_dates: Set[datetime.date],
    prefix: str = "",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[str]:
    """Select only source CSV files whose YMD is in ``missing_dates`` AND
    within the [start_date, end_date] range.

    This is the bridge between the DB-driven missing-data detection and
    the CSV-file-based source data: the DB tells us WHICH dates we need,
    and this function translates that into WHICH files to read.

    Args:
        files: list of CSV file paths to filter
        missing_dates: set of datetime.date that are missing from DB
        prefix: filename prefix to validate (e.g. "szse_trend_etf_")
        start_date: optional 'YYYY-MM-DD' lower bound (inclusive)
        end_date: optional 'YYYY-MM-DD' upper bound (inclusive)

    Returns:
        List of file paths whose date is in missing_dates and within range.
    """
    if not missing_dates:
        return []
    missing_ymd = {d.strftime("%Y%m%d") for d in missing_dates}
    start_ymd = iso_to_ymd(start_date) if start_date else None
    end_ymd = iso_to_ymd(end_date) if end_date else None
    out: List[str] = []
    for path in files:
        ymd = ymd_from_filename(path, prefix)
        if ymd is None:
            continue
        if ymd not in missing_ymd:
            continue
        if not in_range(ymd, start_ymd, end_ymd):
            continue
        out.append(path)
    return out


def select_source_files_in_range(
    files: Sequence[str],
    prefix: str = "",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[str]:
    """Select source CSV files within the [start_date, end_date] range,
    without any DB-driven filtering. Used when the caller wants to read
    all available source files for the given range (e.g. for full rebuild
    or for tables without a date column).
    """
    start_ymd = iso_to_ymd(start_date) if start_date else None
    end_ymd = iso_to_ymd(end_date) if end_date else None
    out: List[str] = []
    for path in files:
        ymd = ymd_from_filename(path, prefix)
        if ymd is None:
            continue
        if not in_range(ymd, start_ymd, end_ymd):
            continue
        out.append(path)
    return out


def glob_source_files(scan_dir: str, pattern: str) -> List[str]:
    """Sorted glob of source CSV files. Returns [] if dir doesn't exist."""
    if not os.path.isdir(scan_dir):
        return []
    return sorted(glob.glob(os.path.join(scan_dir, pattern)))


# ============================================================================
# Build-script header / footer
# ============================================================================
def print_build_header(title: str, **fields) -> None:
    """Print a consistent build-script header.

    ``fields`` are rendered as ``key: value`` lines under the title bar.
    """
    print("=" * 78, flush=True)
    print(f"  {title}", flush=True)
    print("=" * 78, flush=True)
    for k, v in fields.items():
        print(f"  {k:<22s}: {v}", flush=True)


def print_wall_time(t0: float) -> None:
    """Print the elapsed wall time since ``t0`` (a time.time() value)."""
    import time as _time
    print(f"\n  Wall time: {int(_time.time() - t0)}s", flush=True)
    print("=" * 78, flush=True)


# ============================================================================
# Project paths — shared constants
# ============================================================================
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMP_DATA = os.path.join(PROJECT_ROOT, "temp_data")
TODAY_STR = datetime.datetime.now().strftime("%Y-%m-%d")
