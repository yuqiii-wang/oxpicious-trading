"""downloads.options.cffex.trend.paths — Directory paths for CFFEX options trend data.

Trend data is stored under temps/cffex_options_trend/ with the same structure
as the archive: YYYYMM/YYYYMMDD_options.csv.

Also provides helpers for scanning shared CSV directories (archive and
futures trend) for backfill opportunities.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Optional

from downloads._common import resolve_out_dir
from downloads.options.cffex.trend.config import TREND_DIRNAME, ARCHIVE_DIRNAME, FUTURES_TREND_DIRNAME


def _project_root() -> Path:
    """Get the project root directory."""
    return Path(__file__).resolve().parents[4]


def get_trend_dir(out_root: Optional[str] = None) -> Path:
    """Get (or create) the options trend data root directory.

    Returns:
        Path to the trend directory (e.g. temps/cffex_options_trend/).
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


def trend_options_csv_path(d: date, out_root: Optional[str] = None) -> Path:
    """Get the path for a specific date's options CSV file.

    Args:
        d: Trading date.
        out_root: Optional override for the output root.

    Returns:
        Path like temps/cffex_options_trend/202608/20260814_options.csv
    """
    ym = d.strftime("%Y%m")
    ymd = d.strftime("%Y%m%d")
    return get_trend_month_dir(ym, out_root) / f"{ymd}_options.csv"


def list_trend_dates(out_root: Optional[str] = None) -> set[date]:
    """List all dates that have options trend CSV files.

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
        for csv_file in month_dir.glob("*_options.csv"):
            stem = csv_file.stem.replace("_options", "")
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
    """Get the most recent date with options trend data.

    Returns:
        Latest date with trend data, or None if no data exists.
    """
    dates = list_trend_dates(out_root)
    return max(dates) if dates else None


# ---------------------------------------------------------------------------
# Shared CSV scanning (archive + futures trend)
# ---------------------------------------------------------------------------

def get_archive_dir(out_root: Optional[str] = None) -> Path:
    """Get the archive data root directory.

    Returns:
        Path to temps/cffex_archive/ (shared with futures archive).
    """
    return resolve_out_dir(
        str(Path(__file__).resolve()), ARCHIVE_DIRNAME, out_root,
    )


def get_futures_trend_dir(out_root: Optional[str] = None) -> Path:
    """Get the futures trend data root directory.

    Returns:
        Path to temps/cffex_trend/ (shared with futures trend).
    """
    return resolve_out_dir(
        str(Path(__file__).resolve()), FUTURES_TREND_DIRNAME, out_root,
    )


def list_shared_options_dates(out_root: Optional[str] = None) -> set[date]:
    """List all dates that have _options.csv files in shared directories.

    Scans:
      1. temps/cffex_archive/ (archive CSVs)
      2. temps/cffex_trend/ (futures trend CSVs)
      3. temps/cffex_options_trend/ (our own output)

    Returns:
        Set of date objects for which _options.csv files exist.
    """
    all_dates: set[date] = set()

    # Scan archive directory
    for base_dir in [get_archive_dir(out_root), get_futures_trend_dir(out_root), get_trend_dir(out_root)]:
        if not base_dir.exists():
            continue
        for month_dir in base_dir.iterdir():
            if not month_dir.is_dir():
                continue
            for csv_file in month_dir.glob("*_options.csv"):
                stem = csv_file.stem.replace("_options", "")
                if not stem:
                    stem = csv_file.stem.replace("_1", "")
                try:
                    d = date(
                        int(stem[:4]),
                        int(stem[4:6]),
                        int(stem[6:8]),
                    )
                    all_dates.add(d)
                except ValueError:
                    continue

    return all_dates


def shared_options_csv_paths_for_date(d: date, out_root: Optional[str] = None) -> list[Path]:
    """Get all _options.csv file paths for a specific date from shared dirs.

    Checks archive, futures trend, and options trend directories.

    Returns:
        List of paths that exist for this date (may be empty).
    """
    ym = d.strftime("%Y%m")
    ymd = d.strftime("%Y%m%d")

    candidates = [
        get_archive_dir(out_root) / ym / f"{ymd}_options.csv",
        get_futures_trend_dir(out_root) / ym / f"{ymd}_options.csv",
        get_trend_dir(out_root) / ym / f"{ymd}_options.csv",
        get_archive_dir(out_root) / ym / f"{ymd}_1.csv",  # combined CSV (fallback)
        get_futures_trend_dir(out_root) / ym / f"{ymd}_1.csv",  # combined CSV (fallback)
    ]

    return [p for p in candidates if p.exists()]


def get_trend_file_size(path: Path) -> int:
    """Get file size in bytes, or 0 if file doesn't exist."""
    try:
        return path.stat().st_size
    except OSError:
        return 0
