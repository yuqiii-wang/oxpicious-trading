"""Source-file discovery: OHLCV CSVs, margin CSVs, and loadable dates."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from _common.build_commons import (
    glob_source_files,
    ymd_from_filename,
    ymd_to_date,
)
from builds._commons.paths import (
    BSE_TREND_DIR,
    SSE_TREND_DIR,
    SZSE_ARCHIVE_DIR,
    SZSE_MARGIN_DIR,
    SZSE_TREND_DIR,
    SSE_MARGIN_DIR,
)
from builds.stock._helpers import (
    SOURCE_FILE_SETS,
    _file_has_data,
    discover_source_files,
)


def file_date_from_path(path: str) -> date | None:
    """Date parsed from a source CSV filename (first matching known
    prefix), or None."""
    for _dir, _pat, prefix, _mkt, _sfx in SOURCE_FILE_SETS:
        ymd = ymd_from_filename(path, prefix)
        if ymd:
            d = ymd_to_date(ymd)
            if d:
                return d
    return None


@dataclass
class SourceDiscovery:
    """All discovered source files plus their loadable (non-holiday) dates."""
    all_files: list[tuple[str, str, str]]
    available_dates: set[date]
    loadable_dates: set[date]
    szse_margin_files: list[str]
    sse_margin_files: list[str]


def discover_sources(
    start_date: str | None,
    end_date: str | None,
    limit: int | None = None,
    code_suffix: str | None = None,
) -> SourceDiscovery:
    """Glob all OHLCV + margin source CSVs and compute loadable dates.

    Loadable dates are filename dates whose source file has at least one
    real data row — holiday exports are header-only/"没有找到" placeholder
    files that can never produce DB rows, so their dates must stay out of
    missing-date detection (otherwise the gap check never converges).

    `code_suffix` (".SZ" / ".SS" / ".BJ") applies the exchange-dir rule at
    discovery time: OHLCV file lists keep only that exchange's files, so
    the per-file non-empty peek below never touches cross-exchange dirs.
    Margin file lists stay unpruned here (cheap name lists); the caller
    applies its own exchange rule on them.
    """
    all_files = discover_source_files(
        SZSE_ARCHIVE_DIR, SZSE_TREND_DIR, SSE_TREND_DIR, BSE_TREND_DIR,
        start_date, end_date,
    )
    if code_suffix:
        all_files = [t for t in all_files if t[2] == code_suffix]
    if limit:
        all_files = all_files[:limit]

    available_dates: set[date] = set()
    loadable_dates: set[date] = set()
    for path, _market, _suffix in all_files:
        d = file_date_from_path(path)
        if d is None:
            continue
        available_dates.add(d)
        if _file_has_data(path):
            loadable_dates.add(d)

    szse_margin_files = glob_source_files(SZSE_MARGIN_DIR, "szse_margin_detail_*.csv")
    sse_margin_files = glob_source_files(SSE_MARGIN_DIR, "sse_margin_detail_*.csv")

    return SourceDiscovery(
        all_files=all_files,
        available_dates=available_dates,
        loadable_dates=loadable_dates,
        szse_margin_files=szse_margin_files,
        sse_margin_files=sse_margin_files,
    )


def margin_loadable_dates(margin_files: list[str]) -> set[date]:
    """Dates of margin CSVs that contain at least one data row."""
    out: set[date] = set()
    for f in margin_files:
        if not _file_has_data(f):
            continue
        for prefix in ("szse_margin_detail_", "sse_margin_detail_"):
            ymd = ymd_from_filename(f, prefix)
            if ymd:
                d = ymd_to_date(ymd)
                if d:
                    out.add(d)
                    break
    return out
