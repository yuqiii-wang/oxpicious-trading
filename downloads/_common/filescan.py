"""Filesystem scanning & validity helpers for cached download files.

Output-dir resolution, file validity/freshness checks, and the filename
date-key scan family ({prefix}_{YYYYMMDD}, {prefix}_{YYYY},
{prefix}_{YYYYMMDD}_{YYYYMMDD} chunks, custom patterns) used by the
download-plan builders in ``plans.py``.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, Optional, Set, Tuple


MIN_VALID_BYTES = 1024
EMPTY_HTML_MAX_BYTES = 8192

# Project root = three levels up from this module
# (downloads/_common/filescan.py -> _common -> downloads -> <project root>).
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


def is_valid_file(path: Path, *, min_bytes: int = MIN_VALID_BYTES) -> bool:
    try:
        return path.exists() and path.is_file() and path.stat().st_size >= min_bytes
    except OSError:
        return False


def is_fresh_today(
    path: Path, *, min_bytes: int = MIN_VALID_BYTES, hour: int = 17,
) -> bool:
    """Check if a file exists, is valid, and was modified at or after *hour* on today's date."""
    if not is_valid_file(path, min_bytes=min_bytes):
        return False
    try:
        mtime = path.stat().st_mtime
        mtime_dt = datetime.fromtimestamp(mtime)
        today = datetime.now()
        if mtime_dt.date() != today.date():
            return False
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


RE_DATEKEY_YYYYMMDD = re.compile(r"_(\d{8})\.(xlsx|xls|csv|md|json)$")
RE_YEARKEY_YYYY = re.compile(r"_(\d{4})\.(xlsx|xls|csv|md|json)$")
RE_CHUNKKEY_RANGE = re.compile(r"_(\d{8})_(\d{8})\.(xlsx|xls|csv)$")

RE_DATEKEY_YYYYMMDD_DASH = re.compile(r"_(\d{4}-\d{2}-\d{2})_")


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
