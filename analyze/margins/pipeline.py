"""
pipeline.py — Pipeline helper functions for analyze.margins.

Moved from __main__.py to keep the entry point focused on orchestration.
"""
from __future__ import annotations

import datetime
from typing import Optional

import pandas as pd

from _common.build_commons import (
    copy_insert_async,
    truncate_table_async,
    find_missing_analysis_dates,
)
from _common.db_commons import copy_or_upsert_split_async
from analyze._common import sanitize_for_db_insert
from analyze.margins.config import (
    TABLE_TECH_STATS,
    TABLE_INDUSTRY_STATS,
    TABLE_INDUSTRY_CORRELATION,
    SRC_TABLE_ETF,
    SRC_TABLE_STOCK,
    UNIVERSE_RECENT_DAYS,
    SEC_TYPE_SOURCE_TABLES,
    TECH_STATS_NUMERIC_COLS,
    TECH_STATS_INSERT_COLUMNS,
    INDUSTRY_STATS_INSERT_COLUMNS,
    INDUSTRY_STATS_NUMERIC_COLS,
)
from analyze.margins.fetch import (
    fetch_active_rongzi_codes,
    fetch_margin_history,
    fetch_industry_mapping,
    fetch_index_margin_series,
)
from analyze.margins.compute import compute_tech_stats


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

async def fetch_latest_source_date(conn, sec_types: list[str]) -> datetime.date:
    """Return MAX(date) across the source tables for the given sec_types.

    Used as the ``ref_date`` for the universe filter so the filter tracks
    the latest available data instead of the wall-clock today (which may
    be ahead of the source by 1-2 days on weekends / holidays).
    """
    tables: list[str] = []
    if "etf" in sec_types:
        tables.append(SRC_TABLE_ETF)
    if "stock" in sec_types:
        tables.append(SRC_TABLE_STOCK)
    if not tables:
        return datetime.date.today()

    union = " UNION ALL ".join(
        f"SELECT MAX(date) AS d FROM {t}" for t in tables
    )
    row = await conn.fetchrow(f"SELECT MAX(d) AS latest FROM ({union}) sub")
    latest = row["latest"] if row else None
    return latest if latest is not None else datetime.date.today()


async def delete_tech_stats_for_sec_type(conn, sec_type: str) -> None:
    """Delete rows for a single sec_type from margin_tech_stats.

    Used when only one sec_type is selected (preserves the other sec_type's
    rows). When both sec_types are selected, the whole table is truncated
    instead (faster).
    """
    await conn.execute(
        f"DELETE FROM {TABLE_TECH_STATS} WHERE sec_type = $1", sec_type
    )


# ---------------------------------------------------------------------------
#  Missing-date detection
# ---------------------------------------------------------------------------

async def detect_missing_dates(
    conn,
    sec_types: list[str],
    run_index: bool,
    is_index_only: bool,
    force: bool,
) -> tuple[dict, set, set, set]:
    """Detect missing dates per table for incremental mode.

    Returns (target_dates_tech, target_dates_index, target_dates_industry,
    target_dates_corr). Each value is None in force mode (meaning "all
    dates"), or a set of missing dates in incremental mode.
    """
    if force:
        return (
            {st: None for st in sec_types},
            None if run_index else set(),
            None,
            None,
        )

    # ---- tech_stats per sec_type ----
    target_dates_tech: dict[str, set] = {}
    for st in sec_types:
        src_tables = SEC_TYPE_SOURCE_TABLES[st]
        missing = await find_missing_analysis_dates(
            conn, TABLE_TECH_STATS, src_tables, sec_type=st,
        )
        target_dates_tech[st] = missing
        print(f"    -> tech_stats[{st}]: {len(missing)} missing dates",
              flush=True)

    # ---- tech_stats[index] ----
    target_dates_index: set = set()
    if run_index:
        target_dates_index = await find_missing_analysis_dates(
            conn, TABLE_TECH_STATS,
            SEC_TYPE_SOURCE_TABLES["index"],
            sec_type="index",
        )
        print(f"    -> tech_stats[index]: {len(target_dates_index)} "
              f"missing dates", flush=True)

    # ---- industry_stats ----
    target_dates_industry: set = set()
    if not is_index_only:
        industry_source = [
            SEC_TYPE_SOURCE_TABLES[st][0]
            for st in sec_types
        ] or [SRC_TABLE_ETF, SRC_TABLE_STOCK]
        target_dates_industry = await find_missing_analysis_dates(
            conn, TABLE_INDUSTRY_STATS, industry_source,
        )
        print(f"    -> industry_stats: {len(target_dates_industry)} "
              f"missing dates", flush=True)

    # ---- correlations ----
    target_dates_corr: set = set()
    if not is_index_only:
        target_dates_corr = await find_missing_analysis_dates(
            conn, TABLE_INDUSTRY_CORRELATION,
            SEC_TYPE_SOURCE_TABLES["index"],
        )
        print(f"    -> correlations: {len(target_dates_corr)} missing dates",
              flush=True)

    return (
        target_dates_tech, target_dates_index,
        target_dates_industry, target_dates_corr,
    )


# ---------------------------------------------------------------------------
#  Per-sec-type tech stats pipeline
# ---------------------------------------------------------------------------

async def run_sec_type(
    conn,
    sec_type: str,
    ref_date: datetime.date,
    *,
    force: bool = True,
    target_dates: Optional[set] = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Run the per-sec_type pipeline (universe filter -> history ->
    industry map -> tech_stats -> insert).

    Returns (raw_history, industry_map, tech_stats) for use by the
    industry_stats aggregation step + the margin_changes detection step.
    The FULL tech_stats (all dates) is returned regardless of mode —
    trend episode detection needs the full curve. Only the DB WRITE
    is affected by ``force`` / ``target_dates``.

    Args:
        conn: asyncpg connection.
        sec_type: 'etf' or 'stock'.
        ref_date: reference date for the universe filter.
        force: when True, DELETE all sec_type rows then COPY-insert
            (full recompute). When False, upsert only rows whose date
            is in ``target_dates`` (incremental).
        target_dates: set of missing dates to write (incremental mode).
            Ignored when ``force`` is True. None means write all rows.
    """
    print(f"\n  --- sec_type = {sec_type} ---", flush=True)

    # ---- Universe filter ---------------------------------------------
    print(f"    [a] Universe filter: codes with rz_balance > 0 in the "
          f"last {UNIVERSE_RECENT_DAYS} calendar days on or before "
          f"{ref_date}...", flush=True)
    active_codes = await fetch_active_rongzi_codes(
        conn, sec_type, ref_date=ref_date
    )
    print(f"        -> {len(active_codes):,} active {sec_type} codes",
          flush=True)

    # ---- Margin history ----------------------------------------------
    # Always fetch FULL history (MA60 needs 60 prior days). The
    # incremental filter is applied AFTER computation, not here.
    print(f"    [b] Fetching full rz_balance + rz_buy history for "
          f"{len(active_codes):,} codes...", flush=True)
    history = await fetch_margin_history(conn, sec_type, active_codes)
    print(f"        -> {len(history):,} rows", flush=True)

    # ---- Industry mapping --------------------------------------------
    print(f"    [c] Fetching industry mapping for {sec_type}...",
          flush=True)
    industry_map = await fetch_industry_mapping(conn, sec_type)
    n_mapped = history["code"].isin(industry_map["code"]).nunique() \
        if not history.empty else 0
    print(f"        -> {len(industry_map):,} mapped codes "
          f"({n_mapped} of {history['code'].nunique() if not history.empty else 0} "
          f"history codes have an industry)", flush=True)

    # ---- Tech stats --------------------------------------------------
    # Always compute on FULL history (MA windows + hypes need it).
    print(f"    [d] Computing ma5/ma20/ma60 + slope per code...",
          flush=True)
    tech_stats = compute_tech_stats(history, sec_type)
    print(f"        -> {len(tech_stats):,} tech-stats rows", flush=True)

    # ---- Insert ------------------------------------------------------
    # Force: DELETE sec_type rows + COPY-insert (no conflicts).
    # Incremental: upsert only target_dates rows (ON CONFLICT DO UPDATE).
    if force:
        print(f"    [e] Deleting old {sec_type} rows from "
              f"{TABLE_TECH_STATS}...", flush=True)
        await delete_tech_stats_for_sec_type(conn, sec_type)
        rows_to_write = tech_stats
    else:
        if target_dates:
            n_before = len(tech_stats)
            rows_to_write = tech_stats[
                tech_stats["date"].isin(target_dates)
            ].reset_index(drop=True)
            print(f"    [e] Incremental filter: {len(rows_to_write):,} of "
                  f"{n_before:,} rows are in target_dates", flush=True)
        else:
            rows_to_write = tech_stats

    if rows_to_write.empty:
        print("        -> no rows to insert" if force else
              "        -> no new rows to upsert", flush=True)
    elif force:
        rows = sanitize_for_db_insert(
            rows_to_write,
            numeric_cols=TECH_STATS_NUMERIC_COLS, round_to=6,
        )
        n = await copy_insert_async(
            conn, TABLE_TECH_STATS, rows,
            columns=TECH_STATS_INSERT_COLUMNS,
        )
        print(f"        -> COPY-inserted {n:,} rows", flush=True)
    else:
        rows = sanitize_for_db_insert(
            rows_to_write[TECH_STATS_INSERT_COLUMNS],
            numeric_cols=TECH_STATS_NUMERIC_COLS, round_to=6,
        )
        n_copied, n_upserted = await copy_or_upsert_split_async(
            conn, TABLE_TECH_STATS, rows,
            key_columns=["sec_type", "code", "date"],
        )
        n = n_copied + n_upserted
        via = "COPY" if n_copied > 0 and n_upserted == 0 else \
              f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else \
              "upsert"
        print(f"        -> inserted {n:,} rows via {via}", flush=True)

    return history, industry_map, tech_stats


# ---------------------------------------------------------------------------
#  Index-level tech stats (aggregated from the margin_index_series VIEW)
# ---------------------------------------------------------------------------

async def run_index_tech_stats(
    conn,
    *,
    force: bool = True,
    target_dates: Optional[set] = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compute + insert sec_type='index' tech_stats from the
    analysis.margin_index_series VIEW.

    The VIEW aggregates constituent stocks' rz_balance / rz_buy by
    parent_index_weight (weighted-AVERAGE). compute_tech_stats then
    derives the regime-detection cols (slope_ma5 / zscore_20d) on this
    AGGREGATED series — the mathematically correct order
    (aggregate-then-compute; per-stock slopes are non-additive).

    Returns (history, tech_stats):
      history     — DataFrame[code, date, rz_balance, rz_buy] (the raw
                   aggregated index-level series from the VIEW; needed
                   by the margin_changes step for rz_balance / rz_buy
                   lookup).
      tech_stats  — DataFrame with the full margin_tech_stats column set
                   (sec_type='index'); also stored in
                   tech_stats_by_sec_type['index'] by the caller. FULL
                   set (all dates) is returned regardless of mode.

    Args:
        conn: asyncpg connection.
        force: when True, DELETE + COPY-insert (full recompute). When
            False, upsert only target_dates rows (incremental).
        target_dates: set of missing dates to write (incremental mode).
    """
    print("\n  --- sec_type = index (aggregated from "
          "margin_index_series VIEW) ---", flush=True)

    print("    [a] Fetching weighted-avg index margin series from "
          "margin_index_series VIEW...", flush=True)
    history = await fetch_index_margin_series(conn)
    n_codes = history["code"].nunique() if not history.empty else 0
    print(f"        -> {len(history):,} rows, {n_codes:,} index codes",
          flush=True)

    print("    [b] Computing ma5/ma20/ma60 + slope + regime cols "
          "(on the aggregated series)...", flush=True)
    tech_stats = compute_tech_stats(history, "index")
    print(f"        -> {len(tech_stats):,} tech-stats rows", flush=True)

    # ---- Insert ------------------------------------------------------
    if force:
        print(f"    [c] Deleting old index rows from "
              f"{TABLE_TECH_STATS}...", flush=True)
        await delete_tech_stats_for_sec_type(conn, "index")
        rows_to_write = tech_stats
    else:
        if target_dates:
            n_before = len(tech_stats)
            rows_to_write = tech_stats[
                tech_stats["date"].isin(target_dates)
            ].reset_index(drop=True)
            print(f"    [c] Incremental filter: {len(rows_to_write):,} of "
                  f"{n_before:,} rows are in target_dates", flush=True)
        else:
            rows_to_write = tech_stats

    if rows_to_write.empty:
        print("        -> no rows to insert" if force else
              "        -> no new rows to upsert", flush=True)
    elif force:
        rows = sanitize_for_db_insert(
            rows_to_write,
            numeric_cols=TECH_STATS_NUMERIC_COLS,
            round_to=6,
        )
        n = await copy_insert_async(
            conn, TABLE_TECH_STATS, rows,
            columns=TECH_STATS_INSERT_COLUMNS,
        )
        print(f"        -> COPY-inserted {n:,} rows", flush=True)
    else:
        rows = sanitize_for_db_insert(
            rows_to_write[TECH_STATS_INSERT_COLUMNS],
            numeric_cols=TECH_STATS_NUMERIC_COLS, round_to=6,
        )
        n_copied, n_upserted = await copy_or_upsert_split_async(
            conn, TABLE_TECH_STATS, rows,
            key_columns=["sec_type", "code", "date"],
        )
        n = n_copied + n_upserted
        via = "COPY" if n_copied > 0 and n_upserted == 0 else \
              f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else \
              "upsert"
        print(f"        -> inserted {n:,} rows via {via}", flush=True)

    return history, tech_stats


# ---------------------------------------------------------------------------
#  Industry stats insert
# ---------------------------------------------------------------------------

async def insert_industry_stats(
    conn,
    industry_stats: pd.DataFrame,
    *,
    force: bool = True,
    target_dates: Optional[set] = None,
) -> None:
    """Insert pre-computed industry stats into margin_industry_stats.

    Args:
        conn: asyncpg connection.
        industry_stats: DataFrame from compute_industry_stats.
        force: when True, truncate + COPY-insert (full recompute).
            When False, upsert only target_dates rows (incremental).
        target_dates: set of missing dates to write (incremental mode).
    """

    # ---- Insert ------------------------------------------------------
    if force:
        print(f"    Truncating {TABLE_INDUSTRY_STATS} and inserting...",
              flush=True)
        await truncate_table_async(conn, TABLE_INDUSTRY_STATS)
        rows_to_write = industry_stats
    else:
        if target_dates:
            n_before = len(industry_stats)
            rows_to_write = industry_stats[
                industry_stats["date"].isin(target_dates)
            ].reset_index(drop=True)
            print(f"    Incremental filter: {len(rows_to_write):,} of "
                  f"{n_before:,} industry_stats rows are in target_dates",
                  flush=True)
        else:
            rows_to_write = industry_stats

    if rows_to_write.empty:
        print("    -> no rows to insert" if force else
              "    -> no new rows to upsert", flush=True)
    elif force:
        rows = sanitize_for_db_insert(
            rows_to_write,
            numeric_cols=INDUSTRY_STATS_NUMERIC_COLS, round_to=4,
        )
        n = await copy_insert_async(
            conn, TABLE_INDUSTRY_STATS, rows,
            columns=INDUSTRY_STATS_INSERT_COLUMNS,
        )
        print(f"    -> COPY-inserted {n:,} rows", flush=True)
    else:
        rows = sanitize_for_db_insert(
            rows_to_write[INDUSTRY_STATS_INSERT_COLUMNS],
            numeric_cols=INDUSTRY_STATS_NUMERIC_COLS, round_to=4,
        )
        n_copied, n_upserted = await copy_or_upsert_split_async(
            conn, TABLE_INDUSTRY_STATS, rows,
            key_columns=["date", "industry_id"],
        )
        n = n_copied + n_upserted
        via = "COPY" if n_copied > 0 and n_upserted == 0 else \
              f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else \
              "upsert"
        print(f"    -> inserted {n:,} rows via {via}", flush=True)

    # Sanity summary (only in force mode)
    if force and not industry_stats.empty:
        await _print_industry_filter_summary(conn)


async def _print_industry_filter_summary(conn) -> None:
    """Print a sanity summary of the industry_stats table after a force insert."""
    summary = await conn.fetch("""
        SELECT
            MAX(stock_count)           AS max_stock_count,
            MAX(stock_margin_count)     AS max_stock_margin_count,
            MAX(etf_count)              AS max_etf_count,
            MAX(etf_margin_count)       AS max_etf_margin_count,
            SUM(stock_count)            AS tot_stock_count,
            SUM(stock_margin_count)     AS tot_stock_margin_count,
            SUM(etf_count)              AS tot_etf_count,
            SUM(etf_margin_count)       AS tot_etf_margin_count
        FROM analysis.margin_industry_stats
    """)
    r = summary[0]
    print(f"    [filter summary] max per (date, industry): "
          f"stock {r['max_stock_margin_count']}/{r['max_stock_count']} "
          f"active, etf {r['max_etf_margin_count']}/{r['max_etf_count']} "
          f"active", flush=True)
    print(f"    [filter summary] total (sum across all rows): "
          f"stock {r['tot_stock_margin_count']}/{r['tot_stock_count']} "
          f"active, etf {r['tot_etf_margin_count']}/{r['tot_etf_count']} "
          f"active", flush=True)