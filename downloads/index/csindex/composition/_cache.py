"""CSV filename helpers and cache discovery for composition snapshots."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from downloads._common import MIN_VALID_BYTES, is_valid_file


def csv_filename_for(index_code: str, snapshot_date: str) -> str:
    """Build the output CSV filename: {code}_closeweight_{YYYYMMDD}.csv"""
    ymd = snapshot_date.replace("-", "")
    return f"{index_code}_closeweight_{ymd}.csv"


def find_cached_csv(out_dir: Path, index_code: str) -> Optional[Path]:
    """Find the most recent valid cached CSV for the given index code.

    Scans ``out_dir`` for files matching ``{code}_closeweight_*.csv``, sorted
    ascending, and returns the last one whose bytes pass ``is_valid_file``.
    Returns None if nothing usable exists.
    """
    pattern = f"{index_code}_closeweight_*.csv"
    files = sorted(out_dir.glob(pattern))
    for f in reversed(files):
        if is_valid_file(f, min_bytes=MIN_VALID_BYTES):
            return f
    return None
