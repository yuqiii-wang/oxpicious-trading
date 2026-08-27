"""Download plan builders & execution loop.

Scan-or-query-DB plan construction for per-day / per-year / per-chunk
download loops, plus the generic RunStats + run_plan_with_sleep executor.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from _common._holidays_and_weekdays import (
    business_days,
    date_range_backward,
    date_range_forward,
)
from downloads._common.filescan import (
    MIN_VALID_BYTES,
    scan_present_chunk_keys,
    scan_present_day_keys,
    scan_present_year_keys,
)

# Empty markers newer than this many calendar days are retried on each run
# (a recent "no data" response is often just an export that wasn't
# published yet when first requested — retrying keeps per-security loaded
# dates aligned; old markers stay skipped as confirmed no-data dates).
EMPTY_MARKER_RETRY_DAYS: int = 3

# Shared default start date for all downloaders. Centralized here so the
# project's historical backfill horizon can be changed in one place.
DEFAULT_START_DATE = "2020-01-01"


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


def _add_stale_empty_markers(
    out_dir: Path,
    prefixes: List[str],
    present_by_prefix: Dict[str, Set[date]],
) -> None:
    """Treat only STALE empty-marker dates as present (already tried).

    Empty markers (0-byte csv/xlsx pairs written when a date returned no
    data) newer than EMPTY_MARKER_RETRY_DAYS are left out so the download
    plan retries them — recent "no data" responses usually just mean the
    exchange export was not published yet when first requested.
    """
    empty_marker_dates = scan_present_day_keys(
        out_dir, prefixes=prefixes, min_bytes=0, ext_glob="*.csv",
    )
    retry_cutoff = date.today() - timedelta(days=EMPTY_MARKER_RETRY_DAYS)
    for p in prefixes:
        markers = empty_marker_dates.get(p, set())
        stale_markers = {d for d in markers if d <= retry_cutoff}
        present_by_prefix[p] |= stale_markers


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
    db_exchange: Optional[str] = None,
    skip_empty_markers: bool = False,
) -> DayDownloadPlan:
    """Build a per-day download plan.

    When *db_table* is provided, missing trading days are computed via
    :func:`_common.pre_check_and_load.check_identity` (which skips holidays and weekends
    via ``_common._holidays_and_weekdays``) instead of scanning the local
    filesystem.  All types share the same DB-derived missing-date set —
    a date missing from the DB is queued for download for every type.

    *db_exchange* is forwarded to ``check_identity`` as the
    ``exchange`` filter, so multi-source identity tables (e.g.
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
            exchange=db_exchange,
            skip_holidays=weekdays_only,
        )
        present_set = set(all_dates) - missing_dates
        present_by_prefix: Dict[str, Set[date]] = {p: set(present_set) for p in prefixes}
        if skip_empty_markers:
            _add_stale_empty_markers(out_dir, prefixes, present_by_prefix)
    else:
        present_by_prefix = scan_present_day_keys(
            out_dir, prefixes=prefixes, min_bytes=min_bytes, ext_glob=ext_glob,
        )
        if skip_empty_markers:
            _add_stale_empty_markers(out_dir, prefixes, present_by_prefix)

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
    logger: Optional[Any] = None,
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
