"""builds._commons.skip_logic — Unified missing-data detection + file filtering.

Every build script (stock, etf, index, bond, options) follows the same
DB-first pattern:

  1. Glob source CSV files (filenames only — no reading yet)
  2. Extract available dates from filenames
  3. Query DB for existing dates in the target table
  4. Compute missing_dates = available - existing
  5. Filter source files to only those whose date is in missing_dates
  6. Read only the filtered files

This module provides a single high-level function that encapsulates steps
1–5 so build scripts don't repeat the same boilerplate.
"""
from __future__ import annotations

import asyncio
import datetime
import os
from typing import Callable, List, Optional, Sequence, Set, Tuple

from _common.build_commons import (
    glob_source_files,
    ymd_from_filename,
    ymd_to_date,
    find_missing_dates,
    get_existing_keys_async,
    iso_to_ymd,
    in_range,
)


def discover_available_dates(
    dirs_patterns_prefixes: List[Tuple[str, str, str]],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Set[datetime.date]:
    """Discover available dates from source CSV filenames.

    Args:
        dirs_patterns_prefixes: list of (dir, glob_pattern, filename_prefix)
                                triples. Each directory is globbed with its
                                pattern, and the prefix is used for YMD extraction.
        start_date: optional 'YYYY-MM-DD' lower bound
        end_date:   optional 'YYYY-MM-DD' upper bound

    Returns:
        Set of datetime.date available in source files.
    """
    available = set()
    for scan_dir, pattern, prefix in dirs_patterns_prefixes:
        files = glob_source_files(scan_dir, pattern)
        for f in files:
            ymd = ymd_from_filename(f, prefix)
            if not ymd:
                continue
            if start_date and not in_range(ymd, iso_to_ymd(start_date), None):
                continue
            if end_date and not in_range(ymd, None, iso_to_ymd(end_date)):
                continue
            d = ymd_to_date(ymd)
            if d:
                available.add(d)
    return available


def filter_files_by_dates(
    files: Sequence[str],
    target_dates: Set[datetime.date],
    prefix: str = "",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[str]:
    """Filter source files to those whose YMD is in target_dates.

    Args:
        files: list of file paths
        target_dates: set of dates to keep
        prefix: filename prefix for YMD extraction
        start_date / end_date: optional range filter

    Returns:
        Filtered list of file paths.
    """
    if not target_dates:
        return []
    target_ymd = {d.strftime("%Y%m%d") for d in target_dates}
    start_ymd = iso_to_ymd(start_date) if start_date else None
    end_ymd = iso_to_ymd(end_date) if end_date else None
    out: List[str] = []
    for path in files:
        ymd = ymd_from_filename(path, prefix)
        if not ymd:
            continue
        if ymd not in target_ymd:
            continue
        if not in_range(ymd, start_ymd, end_ymd):
            continue
        out.append(path)
    return out


async def get_files_to_read(
    conn,
    table: str,
    dirs_patterns_prefixes: List[Tuple[str, str, str]],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    force: bool = False,
    verbose: bool = True,
) -> Tuple[List[str], Set[datetime.date], Set[datetime.date]]:
    """High-level missing-data detection + file filtering.

    Encapsulates the discover → DB-check → filter pipeline used by
    every date-driven build script.

    Args:
        conn: asyncpg connection
        table: DB table to check for existing dates (e.g. 'stats.stock_identity')
        dirs_patterns_prefixes: list of (dir, glob_pattern, filename_prefix)
        start_date: optional 'YYYY-MM-DD' lower bound
        end_date:   optional 'YYYY-MM-DD' upper bound
        force:      if True, treat ALL available dates as missing
        verbose:    print progress messages

    Returns:
        (files_to_read, available_dates, missing_dates)
    """
    available_dates = discover_available_dates(
        dirs_patterns_prefixes, start_date, end_date
    )
    if verbose:
        print(f"    → {len(available_dates)} unique dates available in source files", flush=True)

    if force:
        missing_dates = available_dates
        if verbose:
            print(f"    [DB] Force mode: ALL {len(missing_dates)} dates treated as missing", flush=True)
    else:
        missing_dates = await find_missing_dates(conn, table, available_dates)
        if verbose:
            print(f"    [DB] {len(missing_dates)} dates missing from {table} "
                  f"(out of {len(available_dates)} available)", flush=True)

    # Build full file list across all dirs
    all_files: List[str] = []
    for scan_dir, pattern, prefix in dirs_patterns_prefixes:
        files = glob_source_files(scan_dir, pattern)
        filtered = filter_files_by_dates(files, missing_dates, prefix, start_date, end_date)
        all_files.extend(filtered)

    if verbose:
        print(f"    → {len(all_files)} source CSV files to read", flush=True)

    return all_files, available_dates, missing_dates
