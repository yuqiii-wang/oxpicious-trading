"""Entry point for analyze.margins.

Run via ``python -m analyze.margins``.

Pipeline (default: incremental — skip dates already in the DB;
``--force``: truncate-then-recompute on every run).

  1. Determine ``ref_date`` = MAX(date) across the selected source
     tables. The universe filter (securities with non-zero rz_balance
     in the last 30 calendar days) is evaluated against this date, NOT
     ``datetime.date.today()`` — the source tables may lag by 1-2 days
     (weekend / holiday), so using today would wrongly exclude the
     most recent partial-day rows.

  2. For each sec_type in the selected set (etf / stock / both):
       a. fetch_active_rongzi_codes  — universe filter (rz_balance > 0
          in the last 30 calendar days on or before ref_date).
       b. fetch_margin_history       — full per-(code, date) rz_balance
          + rz_buy for the filtered codes. FULL history is always
          fetched (MA60 needs 60 prior days); the incremental filter
          is applied AFTER computation, not at fetch time.
       c. fetch_industry_mapping     — code -> industry_id.
       d. compute_tech_stats         — ma5/ma20/ma60 + slope per code
          (computed on FULL history for MA correctness + hypes episode
          detection).
       e. Write rows to margin_tech_stats:
          - ``--force``: DELETE old rows for this sec_type, then
            COPY-insert all rows.
          - default (incremental): upsert ONLY rows whose date is in
            the missing-dates set (existing dates are kept as-is).

  3. compute_industry_stats — per-(date, industry_id) SUM aggregation
     of rz_balance / rz_buy across stocks AND ETFs, with stock/etf
     components stored separately. The raw histories + industry maps
     from step 2 are reused (NOT the tech-stats output).
     - ``--force``: Truncate + COPY-insert.
     - default: upsert only missing-date rows.

  4. INTERNAL STEP: run_correlations — pairwise rolling Pearson
     correlation of securities' rongzi series within each industry ->
     margin_industry_correlation. 'index' attribution pairs ALL
     securities (indices + ETFs, via the margin_index_series VIEW);
     'etf' attribution pairs ETFs only. Reuses the same DB connection.
     - ``--force``: Truncate + COPY-insert.
     - default: upsert only missing-date rows.

  5. INTERNAL STEP: run_margin_changes — detect sustained UP/DOWN TREND
     episodes on the RONGZI margin-balance curve and populate
     margin_changes. Reuses the in-memory tech_stats + raw histories
     from step 1 (no DB round-trip for source data). Always truncates +
     recomputes when called (new dates can change trend boundaries).
     Skipped entirely when the DB is up to date (no missing dates
     detected in any table).

  6. Register the two non-internal analyses in
     analysis.analysis_identity (the correlation + changes identity rows
     are upserted by their own internal steps).

Incremental mode rationale
  The universe filter (active rongzi in last 30d) shifts daily, but
  existing rows for past dates remain valid — rz_balance is a STOCK
  (cumulative balance) that doesn't change retroactively. New dates
  simply get appended via upsert; stale codes drop off naturally for
  new dates (their rows for past dates are retained as historical
  record). The MA computation still uses FULL history (fetched
  unconditionally) so MA60 windows are correct even for the first
  newly-added date.

Testing
  ``python -m analyze.margins --sec-type etf`` runs the pipeline with
  ETF data only (smaller dataset, ~1K rows vs ~600K for stocks). The
  industry_stats table will have stock_count=0 / stock_margin_*=0 for
  every row. The correlation step still runs (it reads the
  margin_index_series VIEW, which aggregates stocks independently).
  ``python -m analyze.margins --sec-type index`` runs only the
  index-level aggregation from the margin_index_series VIEW + hypes
  detection (skips stock/etf/industry/correlation steps).
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import os
import sys
import time

# Ensure project root is on sys.path so ``_common`` is importable when run
# directly via ``python -m analyze.margins`` or as a script.
sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ),
)

from _common.build_commons import (  # noqa: E402
    setup_utf8_stdout,
    get_db_connection_async,
    copy_insert_async,
    truncate_table_async,
    print_build_header,
    print_wall_time,
    add_force_arg,
    find_missing_analysis_dates,
)
from _common.db_commons import bulk_upsert_async  # noqa: E402

setup_utf8_stdout()

import pandas as pd  # noqa: E402

from analyze._common import (  # noqa: E402
    sanitize_for_db_insert,
    upsert_analysis_identity,
)
from analyze.margins.config import (  # noqa: E402
    TABLE_TECH_STATS,
    TABLE_INDUSTRY_STATS,
    TABLE_INDUSTRY_CORRELATION,
    SRC_TABLE_ETF,
    SRC_TABLE_STOCK,
    SEC_TYPES,
    UNIVERSE_RECENT_DAYS,
    ANALYSIS_NAMES,
)
from analyze.margins.fetch import (  # noqa: E402
    fetch_active_rongzi_codes,
    fetch_margin_history,
    fetch_industry_mapping,
    fetch_index_margin_series,
)
from analyze.margins.compute import (  # noqa: E402
    compute_tech_stats,
    compute_industry_stats,
)
from analyze.margins.correlations import run_correlations  # noqa: E402
from analyze.margins.changes import run_margin_changes  # noqa: E402


# analysis_identity rows for the two non-correlation tables. Kept here
# (not in config.py) because they are only used by __main__ for the
# identity upsert — the compute / fetch modules don't need them.
_TECH_STATS_DESCRIPTION = (
    "Per-(sec_type, code, date) technical indicators on RONGZI (融资 / "
    "cash-borrow) margin flows. sec_type ∈ {etf, stock}. Source: "
    "stats.etf_liquidity_margin / stats.stock_liquidity_margin. RONQIN "
    "(融券 / sec borrow) EXCLUDED. Two series: margin_balance (rz_balance, "
    "yuan, STOCK), margin_buy (rz_buy, yuan, FLOW). For each: ma5/ma20/ma60 "
    "(pandas rolling(W, min_periods=1) per code — partial mean for first "
    "W-1 rows, NOT NULL) and slope ((X[t]-X[t-1])/X[t-1], NULL on first "
    "date or X[t-1] <= 0). Universe filter: only securities with non-zero "
    "rz_balance in last calendar month. Built by analyze.margins "
    "(truncate-then-recompute); all INSERTs in Python per project rule."
)

_INDUSTRY_STATS_DESCRIPTION = (
    "Per-(date, industry_id) SUM aggregation of stock AND ETF RONGZI (融资) "
    "margin flows. Stock->industry via sec_classification(type=stock, "
    "parent_index_is_primary=TRUE). ETF->industry via two-hop: "
    "etf.parent_index_code->index.industry_id. RONQIN (融券) EXCLUDED. "
    "Stock and ETF components stored SEPARATELY; total_margin_* columns "
    "GENERATED ALWAYS AS (stock + etf) STORED. *_margin_count + "
    "*_margin_count_share + *_margin_weight_share expose active-rongzi "
    "ratio per industry. margin_balance=SUM(rz_balance, yuan), "
    "margin_buy=SUM(rz_buy, yuan). Universe filter: only securities with "
    "non-zero rz_balance in last calendar month. Built by analyze.margins "
    "(truncate-then-recompute); all INSERTs in Python per project rule."
)


# Source tables for missing-date detection (incremental mode). Maps each
# sec_type to the list of tables whose DISTINCT date column forms the
# "expected" set. For etf/stock these are the raw margin tables; for
# index it's the margin_index_series VIEW (aggregates constituent stocks).
_SEC_TYPE_SOURCE_TABLES = {
    "etf": [SRC_TABLE_ETF],
    "stock": [SRC_TABLE_STOCK],
    "index": ["analysis.margin_index_series"],
}


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

async def _fetch_latest_source_date(conn, sec_types: list[str]) -> datetime.date:
    """Return MAX(date) across the source tables for the given sec_types.

    Used as the ``ref_date`` for the universe filter so the filter tracks
    the latest available data instead of the wall-clock today (which may
    be ahead of the source by 1-2 days on weekends / holidays).
    """
    tables = []
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


async def _delete_tech_stats_for_sec_type(conn, sec_type: str) -> None:
    """Delete rows for a single sec_type from margin_tech_stats.

    Used when only one sec_type is selected (preserves the other sec_type's
    rows). When both sec_types are selected, the whole table is truncated
    instead (faster).
    """
    n = await conn.fetchval(
        f"DELETE FROM {TABLE_TECH_STATS} WHERE sec_type = $1 RETURNING 1",
        sec_type,
    )
    # fetchval returns the first column of the first row, but for
    # DELETE ... RETURNING we want the count. Use execute + fetchval
    # pattern instead.
    await conn.execute(
        f"DELETE FROM {TABLE_TECH_STATS} WHERE sec_type = $1", sec_type
    )


# ---------------------------------------------------------------------------
#  Pipeline
# ---------------------------------------------------------------------------

async def _run_sec_type(
    conn,
    sec_type: str,
    ref_date: datetime.date,
    *,
    force: bool = True,
    target_dates: set | None = None,
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
        await _delete_tech_stats_for_sec_type(conn, sec_type)
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
            numeric_cols=_TECH_STATS_NUMERIC_COLS, round_to=6,
        )
        n = await copy_insert_async(
            conn, TABLE_TECH_STATS, rows,
            columns=_TECH_STATS_INSERT_COLUMNS,
        )
        print(f"        -> COPY-inserted {n:,} rows", flush=True)
    else:
        rows = sanitize_for_db_insert(
            rows_to_write[_TECH_STATS_INSERT_COLUMNS],
            numeric_cols=_TECH_STATS_NUMERIC_COLS, round_to=6,
        )
        n = await bulk_upsert_async(
            conn, TABLE_TECH_STATS, rows,
            key_columns=["sec_type", "code", "date"],
        )
        print(f"        -> upserted {n:,} rows", flush=True)

    return history, industry_map, tech_stats


# ---------------------------------------------------------------------------
#  Index-level tech stats (aggregated from the margin_index_series VIEW)
# ---------------------------------------------------------------------------

# Numeric columns for sanitize_for_db_insert — shared by the index path
# and _run_sec_type (identical schema). Defined once to avoid drift.
_TECH_STATS_NUMERIC_COLS = [
    "margin_balance_ma5", "margin_balance_ma20",
    "margin_balance_ma60", "margin_balance_slope",
    "margin_balance_slope_ma5", "margin_balance_slope_ma20",
    "margin_balance_slope_ma255",
    "margin_balance_slope_std20", "margin_balance_slope_zscore_20d",
    "margin_buy_ma5", "margin_buy_ma20", "margin_buy_ma60",
    "margin_buy_slope",
    "margin_buy_slope_ma5", "margin_buy_slope_ma20",
    "margin_buy_slope_std20", "margin_buy_slope_zscore_20d",
]

# Column order for COPY-insert into margin_tech_stats (matches the table
# schema). Shared by the index path and _run_sec_type.
_TECH_STATS_INSERT_COLUMNS = [
    "sec_type", "code", "date",
    "margin_balance_ma5", "margin_balance_ma20",
    "margin_balance_ma60", "margin_balance_slope",
    "margin_balance_slope_ma5", "margin_balance_slope_ma20",
    "margin_balance_slope_ma255",
    "margin_balance_slope_std20",
    "margin_balance_slope_zscore_20d",
    "margin_buy_ma5", "margin_buy_ma20",
    "margin_buy_ma60", "margin_buy_slope",
    "margin_buy_slope_ma5", "margin_buy_slope_ma20",
    "margin_buy_slope_std20", "margin_buy_slope_zscore_20d",
]


async def _run_index_tech_stats(
    conn,
    *,
    force: bool = True,
    target_dates: set | None = None,
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
        await _delete_tech_stats_for_sec_type(conn, "index")
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
            numeric_cols=_TECH_STATS_NUMERIC_COLS,
            round_to=6,
        )
        n = await copy_insert_async(
            conn, TABLE_TECH_STATS, rows,
            columns=_TECH_STATS_INSERT_COLUMNS,
        )
        print(f"        -> COPY-inserted {n:,} rows", flush=True)
    else:
        rows = sanitize_for_db_insert(
            rows_to_write[_TECH_STATS_INSERT_COLUMNS],
            numeric_cols=_TECH_STATS_NUMERIC_COLS, round_to=6,
        )
        n = await bulk_upsert_async(
            conn, TABLE_TECH_STATS, rows,
            key_columns=["sec_type", "code", "date"],
        )
        print(f"        -> upserted {n:,} rows", flush=True)

    return history, tech_stats


# Industry stats insert columns (excludes GENERATED columns — those are
# computed by the DB: total_margin_*, *_margin_count_share).
_INDUSTRY_STATS_INSERT_COLUMNS = [
    "date", "industry_id", "industry_label",
    "stock_count", "stock_margin_count",
    "stock_margin_weight_share",
    "etf_count", "etf_margin_count",
    "stock_margin_balance", "etf_margin_balance",
    "stock_margin_buy", "etf_margin_buy",
]
_INDUSTRY_STATS_NUMERIC_COLS = [
    "stock_count", "stock_margin_count",
    "stock_margin_weight_share",
    "etf_count", "etf_margin_count",
    "stock_margin_balance", "etf_margin_balance",
    "stock_margin_buy", "etf_margin_buy",
]


async def _detect_missing_dates(
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
        src_tables = _SEC_TYPE_SOURCE_TABLES[st]
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
            _SEC_TYPE_SOURCE_TABLES["index"],
            sec_type="index",
        )
        print(f"    -> tech_stats[index]: {len(target_dates_index)} "
              f"missing dates", flush=True)

    # ---- industry_stats ----
    target_dates_industry: set = set()
    if not is_index_only:
        industry_source = [
            _SEC_TYPE_SOURCE_TABLES[st][0]
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
            _SEC_TYPE_SOURCE_TABLES["index"],
        )
        print(f"    -> correlations: {len(target_dates_corr)} missing dates",
              flush=True)

    return (
        target_dates_tech, target_dates_index,
        target_dates_industry, target_dates_corr,
    )


async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Margin analysis: per-code tech stats + per-industry "
                    "SUM aggregation + within-industry security-pair "
                    "correlation. RONGZI (融资) only — RONQIN (融券) excluded."
    )
    ap.add_argument(
        "--sec-type",
        choices=["etf", "stock", "index", "both"],
        default="both",
        help="Which sec_type to process. 'both' (default) runs the full "
             "pipeline. 'etf' or 'stock' runs only that sec_type (useful "
             "for testing with a smaller dataset). 'index' runs only the "
             "index-level aggregation from the margin_index_series VIEW "
             "+ hypes detection (skips stock/etf/industry/correlation steps; "
             "useful for testing VIEW/MA/hype changes in isolation).",
    )
    add_force_arg(ap)
    args = ap.parse_args()
    force = args.force

    sec_types = SEC_TYPES if args.sec_type == "both" else (
        [] if args.sec_type == "index" else [args.sec_type]
    )
    run_index = args.sec_type in ("both", "index")
    is_index_only = args.sec_type == "index"

    t0 = time.time()
    print_build_header(
        "ANALYZE MARGINS (rongzi-only tech stats + industry SUM + corr)",
        index_table=TABLE_TECH_STATS,
        sec_type=args.sec_type,
        mode="FORCE (full recompute)" if force
             else "incremental (missing dates only)",
    )

    conn = await get_db_connection_async()
    try:
        # ---- Step 0: determine ref_date + missing dates ---------------
        print("\n[0/6] Determining ref_date (MAX date across source tables)...",
              flush=True)
        ref_date = await _fetch_latest_source_date(conn, sec_types)
        print(f"    -> ref_date = {ref_date}", flush=True)

        if not force:
            print("\n    Detecting missing dates per table (incremental mode)...",
                  flush=True)
        (
            target_dates_tech, target_dates_index,
            target_dates_industry, target_dates_corr,
        ) = await _detect_missing_dates(
            conn, sec_types, run_index, is_index_only, force,
        )

        # Early exit if everything is up to date (incremental mode only).
        if not force:
            total_missing = (
                sum(len(s) for s in target_dates_tech.values())
                + len(target_dates_index)
                + len(target_dates_industry)
                + len(target_dates_corr)
            )
            if total_missing == 0:
                print("    -> DB is up to date; nothing to do.", flush=True)
                print_wall_time(t0)
                return

        # ---- Step 1: per-sec-type tech stats ----------------------------
        print(f"\n[1/6] Per-sec-type tech stats (sec_types={sec_types})...",
              flush=True)
        # Force mode: truncate the whole tech_stats table up front when
        # processing both sec_types (faster than 2 separate DELETEs).
        if force and args.sec_type == "both":
            print("    Truncating margin_tech_stats (all sec_types)...",
                  flush=True)
            await truncate_table_async(conn, TABLE_TECH_STATS)

        histories: dict[str, pd.DataFrame] = {}
        maps: dict[str, pd.DataFrame] = {}
        tech_stats_by_sec_type: dict[str, pd.DataFrame] = {}
        for st in sec_types:
            td = target_dates_tech.get(st)
            if td is not None and len(td) == 0 and not force:
                print(f"\n  [{st}] up to date; skipping.", flush=True)
                continue
            hist, imap, tech = await _run_sec_type(
                conn, st, ref_date, force=force, target_dates=td,
            )
            histories[st] = hist
            maps[st] = imap
            tech_stats_by_sec_type[st] = tech

        # ---- Step 1b: index-level tech stats (aggregated from VIEW) ----
        if run_index:
            td_idx = target_dates_index
            if td_idx is not None and len(td_idx) == 0 and not force:
                print("\n  [index] up to date; skipping.", flush=True)
            else:
                idx_hist, idx_tech = await _run_index_tech_stats(
                    conn, force=force, target_dates=td_idx,
                )
                histories["index"] = idx_hist
                tech_stats_by_sec_type["index"] = idx_tech

        # ---- Step 2: industry SUM aggregation ---------------------------
        # Skipped for index-only test runs (no stock/etf histories).
        if is_index_only:
            print("\n[2/6] Per-(date, industry_id) SUM aggregation "
                  "-- SKIPPED (index-only test run)", flush=True)
        else:
            print("\n[2/6] Per-(date, industry_id) SUM aggregation "
                  "(stock + etf)...", flush=True)
        etf_hist = histories.get("etf", pd.DataFrame(
            columns=["code", "date", "rz_balance", "rz_buy"]
        ))
        stock_hist = histories.get("stock", pd.DataFrame(
            columns=["code", "date", "rz_balance", "rz_buy"]
        ))
        etf_map = maps.get("etf", pd.DataFrame(
            columns=["code", "industry_id", "industry_label",
                     "parent_index_weight"]
        ))
        stock_map = maps.get("stock", pd.DataFrame(
            columns=["code", "industry_id", "industry_label",
                     "parent_index_weight"]
        ))

        if is_index_only:
            # Index-only test run: skip industry aggregation + insert +
            # correlation steps (no stock/etf histories to aggregate).
            industry_stats = pd.DataFrame()
        else:
            industry_stats = compute_industry_stats(
                etf_tech=etf_hist,
                stock_tech=stock_hist,
                etf_industry_map=etf_map,
                stock_industry_map=stock_map,
            )
            print(f"    -> {len(industry_stats):,} rows across "
                  f"{industry_stats['industry_id'].nunique() if not industry_stats.empty else 0} "
                  f"industries", flush=True)

            # ---- Step 3: insert industry_stats --------------------------
            if force:
                print(f"\n[3/6] Truncating {TABLE_INDUSTRY_STATS} and "
                      f"inserting...", flush=True)
                await truncate_table_async(conn, TABLE_INDUSTRY_STATS)
                rows_to_write = industry_stats
            else:
                if target_dates_industry:
                    n_before = len(industry_stats)
                    rows_to_write = industry_stats[
                        industry_stats["date"].isin(target_dates_industry)
                    ].reset_index(drop=True)
                    print(f"\n[3/6] Incremental filter: {len(rows_to_write):,} "
                          f"of {n_before:,} industry_stats rows are in "
                          f"target_dates", flush=True)
                else:
                    rows_to_write = industry_stats

            if rows_to_write.empty:
                print("    -> no rows to insert" if force else
                      "    -> no new rows to upsert", flush=True)
            elif force:
                rows = sanitize_for_db_insert(
                    rows_to_write,
                    numeric_cols=_INDUSTRY_STATS_NUMERIC_COLS, round_to=4,
                )
                n = await copy_insert_async(
                    conn, TABLE_INDUSTRY_STATS, rows,
                    columns=_INDUSTRY_STATS_INSERT_COLUMNS,
                )
                print(f"    -> COPY-inserted {n:,} rows", flush=True)
            else:
                rows = sanitize_for_db_insert(
                    rows_to_write[_INDUSTRY_STATS_INSERT_COLUMNS],
                    numeric_cols=_INDUSTRY_STATS_NUMERIC_COLS, round_to=4,
                )
                n = await bulk_upsert_async(
                    conn, TABLE_INDUSTRY_STATS, rows,
                    key_columns=["date", "industry_id"],
                )
                print(f"    -> upserted {n:,} rows", flush=True)

            # Sanity summary (only in force mode — incremental may have
            # partial data for the summary to be meaningful).
            if force and not industry_stats.empty:
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

            # ---- Step 4: within-industry security-pair correlations -----
            # run_correlations upserts its OWN analysis_identity row
            # (margin_industry_correlation) internally, so __main__ only
            # upserts the other two identity rows in step 6.
            print("\n[4/6] Within-industry security-pair correlations "
                  "(internal step)...", flush=True)
            await run_correlations(
                conn, force=force, target_dates=target_dates_corr,
            )

        # ---- Step 5: margin changes detection ---------------------------
        # run_margin_changes upserts its OWN analysis_identity row
        # (margin_changes) internally, reusing the in-memory tech_stats +
        # raw histories collected in step 1 (no DB round-trip for source
        # data). Always truncates + recomputes when called — new dates
        # can change trend boundaries. Skipped when DB is up to date
        # (early exit above).
        print("\n[5/6] Margin changes detection (internal step)...",
              flush=True)
        await run_margin_changes(
            conn,
            histories=histories,
            tech_stats_by_sec_type=tech_stats_by_sec_type,
            force=True,
        )

        # ---- Step 6: register in analysis_identity ----------------------
        # (correlation + changes identity rows are upserted by their own
        # internal steps above)
        print("\n[6/6] Registering in analysis.analysis_identity...",
              flush=True)
        await upsert_analysis_identity(
            conn,
            name="margin_tech_stats",
            detail_name="margin_tech_stats",
            description=_TECH_STATS_DESCRIPTION,
        )
        if not is_index_only:
            # industry_stats identity row only upserted when the industry
            # aggregation step ran (skipped for index-only test runs).
            await upsert_analysis_identity(
                conn,
                name="margin_industry_stats",
                detail_name="margin_industry_stats",
                description=_INDUSTRY_STATS_DESCRIPTION,
            )
            print("    -> upserted 2 identity rows (+1 from correlations, "
                  "+1 from changes step)", flush=True)
        else:
            print("    -> upserted 1 identity row (+1 from changes step)", flush=True)

        print_wall_time(t0)
    finally:
        # Close with a timeout — after heavy bulk inserts the PostgreSQL
        # server can be saturated with WAL checkpoint I/O, making
        # conn.close() stall on the Terminate message + TCP teardown.
        try:
            await asyncio.wait_for(conn.close(), timeout=10)
        except (asyncio.TimeoutError, Exception):
            pass


if __name__ == "__main__":
    asyncio.run(main())
