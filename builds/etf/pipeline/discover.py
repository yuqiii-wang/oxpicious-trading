"""Step 1 — source-file discovery (filenames only, nothing is read)."""
from __future__ import annotations

from datetime import date
from typing import Dict, List, Set

from _common.build_commons import glob_source_files, ymd_from_filename, ymd_to_date

from builds.etf.paths import (
    SZSE_ARCHIVE_DIR, SZSE_TREND_DIR, SSE_TREND_DIR,
    SZSE_MARGIN_DIR, SSE_MARGIN_DIR,
)

# {loader-name: (directory, filename-pattern)} for OHLCV + margin sources
OHLCV_SOURCES: Dict[str, tuple] = {
    "szse_archive": (SZSE_ARCHIVE_DIR, "szse_etf_*.csv"),
    "szse_trend":   (SZSE_TREND_DIR,   "szse_trend_etf_*.csv"),
    "sse_trend":    (SSE_TREND_DIR,    "sse_trend_etf_*.csv"),
}
MARGIN_SOURCES: Dict[str, tuple] = {
    "szse": (SZSE_MARGIN_DIR, "szse_margin_detail_*.csv"),
    "sse":  (SSE_MARGIN_DIR,  "sse_margin_detail_*.csv"),
}

# filename prefixes used by ymd_from_filename (must match the patterns above)
YMD_PREFIXES: Dict[str, str] = {
    "szse_archive": "szse_etf_",
    "szse_trend":   "szse_trend_etf_",
    "sse_trend":    "sse_trend_etf_",
}


def discover_source_files() -> tuple[Dict[str, List[str]], Set[date]]:
    """Glob all ETF OHLCV/margin source CSVs and collect available dates.

    Returns:
        (files, available_dates) where ``files`` maps each loader name to
        its sorted file list and ``available_dates`` is the set of OHLCV
        trading days extracted from filenames.
    """
    files: Dict[str, List[str]] = {}
    for name, (d, pat) in {**OHLCV_SOURCES, **MARGIN_SOURCES}.items():
        files[name] = glob_source_files(d, pat)

    available_dates: Set[date] = set()
    for name in ("szse_archive", "szse_trend", "sse_trend"):
        prefix = YMD_PREFIXES[name]
        for f in files[name]:
            ymd = ymd_from_filename(f, prefix)
            if ymd:
                d = ymd_to_date(ymd)
                if d:
                    available_dates.add(d)

    print(f"    → OHLCV: {len(files['szse_archive'])} szse_archive + "
          f"{len(files['szse_trend'])} szse_trend + {len(files['sse_trend'])} sse_trend files", flush=True)
    print(f"    → Margin: {len(files['szse'])} szse + {len(files['sse'])} sse files", flush=True)
    print(f"    → {len(available_dates)} unique OHLCV dates available in source files", flush=True)
    return files, available_dates


def restrict_files_to_dates(
    files: Dict[str, List[str]], read_ymd: Set[str]
) -> Dict[str, List[str]]:
    """Keep only whose files whose date key is in ``read_ymd`` (YYYYMMDD)."""
    return {
        name: [f for f in file_list
               if ymd_from_filename(f, YMD_PREFIXES.get(name, "")) in read_ymd]
        for name, file_list in files.items()
    }
