"""Missing-data detection for build and analyze scripts.

Two tiers of missing-data detection:

1. **Build scripts** compare source CSV filenames (or date lists) against a
   single identity table to learn which (date, code) pairs are already in
   the DB:

     - find_missing_dates()     -- date-only PK tables (e.g. debt_identity)
     - find_missing_keys()       -- multi-column PK tables (e.g. (date, code))

2. **Analyze scripts** compare dates already materialized in their result
   table against the UNION of dates across one or more source identity
   tables:

     - find_missing_analysis_dates()           -- pre-computation check
     - filter_rows_to_missing_dates_async()    -- post-computation filter

3. **Active-universe filter** -- a security whose latest data point is older
   than the cutoff is excluded from analysis entirely:

     - fetch_codes_with_recent_data_async()

Migrated from _common/build_commons.py and _common/db_commons.py.
"""
from __future__ import annotations

import datetime
from typing import Iterable, Optional, Sequence, Set

from _common._holidays_and_weekdays import recent_trading_day_cutoff
from _common.db_commons import (
    _parse_table_name,
    get_existing_keys_async,
)


# ============================================================================
# Build-script missing-data detection
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
# Analysis missing-dates detection -- used by analyze_* scripts
# ============================================================================
async def find_missing_analysis_dates(
    conn,
    analysis_table: str,
    source_identity_tables: Sequence[str],
    *,
    date_column: str = "date",
    sec_type: Optional[str] = None,
) -> Set[datetime.date]:
    """Return the set of dates present in source identity tables but NOT
    yet in the analysis result table.

    This is the analysis-script counterpart of :func:`find_missing_dates`.
    Build scripts compare source CSV filenames against a single identity
    table; analysis scripts compare the dates already materialized in their
    result table against the UNION of dates across one or more source
    identity tables (e.g. stats.etf_identity + stats.index_identity for
    analyze.mov_ave_spread).

    Args:
        conn: asyncpg connection.
        analysis_table: analysis result table, optionally schema-qualified
            (e.g. "analysis.mov_ave_spreads_detail").
        source_identity_tables: list of stats schema identity tables whose
            UNION of ``date_column`` values forms the "expected" set.
        date_column: date column name (default "date").
        sec_type: optional value for the ``sec_type`` column to scope the
            existing-date query (analysis table only). Source identity tables
            are NOT filtered (each table already represents one sec_type).
            When omitted, all sec_types in the analysis table are combined
            (legacy global behavior). Use this for multi-sec_type analysis
            tables whose PK is (sec_type, code, date) -- without it, a date
            populated for one sec_type would mask the same date being
            missing for another sec_type.

    Returns:
        Set of ``datetime.date`` present in any source table but missing
        from the analysis table. Empty set if the analysis table already
        has every source date (DB is up to date) or if all sources are
        empty.
    """
    # Existing dates in the analysis table (optionally scoped to one
    # sec_type). When sec_type is None the query spans all sec_types --
    # which is a bug for multi-sec_type PK tables: a date populated for
    # one sec_type would mask the same date being missing for another.
    # The per-sec_type scope fixes this.
    if sec_type is not None:
        existing_rows = await conn.fetch(
            f'SELECT DISTINCT "{date_column}" FROM {analysis_table} '
            f"WHERE sec_type = $1",
            sec_type,
        )
    else:
        existing_rows = await conn.fetch(
            f'SELECT DISTINCT "{date_column}" FROM {analysis_table}'
        )
    existing_dates = {
        r[date_column] for r in existing_rows if r[date_column] is not None
    }

    # Source dates = UNION across all source identity tables. Source
    # tables are NOT filtered by sec_type -- each identity table already
    # represents exactly one sec_type (stats.etf_identity / index_identity /
    # stock_identity), so scoping would be redundant.
    source_dates: Set[datetime.date] = set()
    for tbl in source_identity_tables:
        rows = await conn.fetch(f'SELECT DISTINCT "{date_column}" FROM {tbl}')
        for r in rows:
            if r[date_column] is not None:
                source_dates.add(r[date_column])

    return source_dates - existing_dates


async def filter_rows_to_missing_dates_async(
    conn,
    table: str,
    rows: Sequence[dict],
    date_key: str = "date",
    *,
    sec_type: Optional[str] = None,
) -> list:
    """Filter ``rows`` to only those whose ``date_key`` value is NOT already
    present in ``table``.

    Post-computation safety filter: after computing a list of row dicts,
    query the target table for existing dates and return only the rows
    whose date is missing. Complements the pre-computation
    :func:`find_missing_analysis_dates` -- the pre-check determines which
    dates to *fetch/compute*; this filter determines which computed rows
    to *upsert*.

    In --force mode (table was just truncated), all dates are missing, so
    this is effectively a no-op (returns all rows). The overhead is one
    ``SELECT DISTINCT date`` query per call.

    Used by analyze.mov_ave_spread (peaks_and_floors + detail),
    analyze.industry_sentiments (main table), and
    analyze.sec_alloc_perf_attribution (build_and_insert) to skip
    already-populated dates before bulk upsert.

    Args:
        conn: asyncpg connection.
        table: target analysis table (schema-qualified, e.g.
            ``"analysis.mov_ave_peaks_and_floors"``).
        rows: list of row dicts to filter.
        date_key: dict key holding the date value (default ``"date"``).
        sec_type: optional value for the ``sec_type`` column to scope the
            existing-date query. Pass the same ``sec_type`` value that the
            rows carry so the missing-date check is per-sec_type (required
            for multi-sec_type PK tables -- see
            :func:`find_missing_analysis_dates`).

    Returns:
        New list containing only rows whose date is missing from the table.
        Returns an empty list if ``rows`` is empty.
    """
    if not rows:
        return []
    source_dates = {r[date_key] for r in rows}
    # Local scope to avoid pulling in the existing-key tuples when we
    # only care about date-level missingness. Scoping by sec_type ensures
    # that a date populated for one sec_type does not mask the same date
    # being missing for another sec_type (the global check bug).
    if sec_type is not None:
        existing_rows = await conn.fetch(
            f'SELECT DISTINCT "{date_key}" FROM {table} WHERE sec_type = $1',
            sec_type,
        )
        existing_dates = {
            r[date_key] for r in existing_rows if r[date_key] is not None
        }
        missing = source_dates - existing_dates
    else:
        missing = await find_missing_dates(conn, table, source_dates)
    return [r for r in rows if r[date_key] in missing]


# ============================================================================
# Recent-data pre-filter -- used by analyze_* scripts to skip stale securities
# ============================================================================
# A security (ETF / index / stock) whose latest data point is older than the
# cutoff date has had no market activity in the recent trading window and is
# excluded from the analysis universe entirely (all its historical rows are
# skipped). This drops delisted / suspended / never-traded codes so the
# analysis only recomputes for the active universe.
RECENT_TRADING_DAYS = 22


async def fetch_codes_with_recent_data_async(
    conn,
    table_name: str,
    *,
    n_trading_days: int = RECENT_TRADING_DAYS,
    ref_date: Optional[datetime.date] = None,
    date_column: str = "date",
    code_column: str = "code",
) -> "Set[str]":
    """Return the set of codes in ``table_name`` that have at least one row
    whose ``date_column`` value falls within the last ``n_trading_days``
    trading days (the window ends at the most recent trading day on or
    before ``ref_date``, default today).

    Used by analyze_* scripts as a pre-filter: a code whose MAX(date) is
    older than the cutoff (i.e. NO row in the recent window) is treated as
    stale (delisted / suspended / never-traded) and excluded from the
    analysis universe. The returned set is intersected with whatever
    subject/benchmark candidate list the caller already has.

    Args:
        conn: asyncpg connection.
        table_name: identity-style table, optionally schema-qualified
            (e.g. "stats.etf_identity", "stats.index_identity"). Must have
            a ``date_column`` (default "date") and a ``code_column``
            (default "code").
        n_trading_days: window width in trading days (default 22 ~ one
            trading month).
        ref_date: reference "today" for the cutoff (default: real today).
        date_column / code_column: column name overrides.

    Returns:
        Set of code strings (e.g. {"510050.SS", "159915.SZ"}) with at
        least one row in the recent window. Empty set if the table is empty
        or has no recent rows.
    """
    cutoff = recent_trading_day_cutoff(n_trading_days, ref_date)
    schema, table = _parse_table_name(table_name)
    from_clause = f'"{schema}"."{table}"' if schema else f'"{table}"'
    query = (
        f'SELECT "{code_column}" FROM {from_clause} '
        f'WHERE "{date_column}" >= $1 '
        f'GROUP BY "{code_column}"'
    )
    rows = await conn.fetch(query, cutoff)
    return {r[code_column] for r in rows if r[code_column] is not None}
