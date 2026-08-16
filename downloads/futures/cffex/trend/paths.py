"""downloads.futures.cffex.trend.paths — Directory paths for CFFEX trend data.

Trend data is stored under temps/cffex_trend/ with the same structure
as the archive: YYYYMM/YYYYMMDD_futures.csv and YYYYMMDD_options.csv.

Unlike archive data, trend data is fetched day-by-day from the CFFEX
website using Playwright browser automation.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Optional

from downloads._common.core import resolve_out_dir
from downloads.futures.cffex.trend.config import TREND_DIRNAME


def get_trend_dir(out_root: Optional[str] = None) -> Path:
    """Get (or create) the trend data root directory.

    Returns:
        Path to the trend directory (e.g. temps/cffex_trend/).
    """
    return resolve_out_dir(
        str(Path(__file__).resolve()), TREND_DIRNAME, out_root,
    )


def get_trend_month_dir(ym: str, out_root: Optional[str] = None) -> Path:
    """Get the directory for a specific month (YYYYMM).

    Args:
        ym: Year-month string like "202608".
        out_root: Optional override for the output root.

    Returns:
        Path to the month directory.
    """
    return get_trend_dir(out_root) / ym


def trend_futures_csv_path(d: date, out_root: Optional[str] = None) -> Path:
    """Get the path for a specific date's futures CSV file.

    Args:
        d: Trading date.
        out_root: Optional override for the output root.

    Returns:
        Path like temps/cffex_trend/202608/20260814_futures.csv
    """
    ym = d.strftime("%Y%m")
    ymd = d.strftime("%Y%m%d")
    return get_trend_month_dir(ym, out_root) / f"{ymd}_futures.csv"


def trend_options_csv_path(d: date, out_root: Optional[str] = None) -> Path:
    """Get the path for a specific date's options CSV file."""
    ym = d.strftime("%Y%m")
    ymd = d.strftime("%Y%m%d")
    return get_trend_month_dir(ym, out_root) / f"{ymd}_options.csv"


def trend_combined_csv_path(d: date, out_root: Optional[str] = None) -> Path:
    """Get the path for a specific date's combined CSV file."""
    ym = d.strftime("%Y%m")
    ymd = d.strftime("%Y%m%d")
    return get_trend_month_dir(ym, out_root) / f"{ymd}_1.csv"


def list_trend_dates(out_root: Optional[str] = None) -> set[date]:
    """List all dates that have trend CSV files (futures or options).

    Returns:
        Set of date objects for which CSV files exist.
    """
    trend_dir = get_trend_dir(out_root)
    dates: set[date] = set()

    if not trend_dir.exists():
        return dates

    for month_dir in trend_dir.iterdir():
        if not month_dir.is_dir():
            continue
        for csv_file in month_dir.glob("*_futures.csv"):
            stem = csv_file.stem.replace("_futures", "")
            try:
                d = date(
                    int(stem[:4]),
                    int(stem[4:6]),
                    int(stem[6:8]),
                )
                dates.add(d)
            except ValueError:
                continue

    return dates


def get_latest_trend_date(out_root: Optional[str] = None) -> Optional[date]:
    """Get the most recent date with trend data.

    Returns:
        Latest date with trend data, or None if no data exists.
    """
    dates = list_trend_dates(out_root)
    return max(dates) if dates else None


def get_trend_file_size(path: Path) -> int:
    """Get file size in bytes, or 0 if file doesn't exist."""
    try:
        return path.stat().st_size
    except OSError:
        return 0