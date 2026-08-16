"""Entry point for analyze.pe_and_dividends.

Run via ``python -m analyze.pe_and_dividends``.

Pipeline (default: incremental — skip dates already in the DB;
``--force``: truncate-then-recompute on every run).

Per sec_type (index / etf / stock):
  1. Fetch active-universe codes from the identity table.
  2. Fetch source data:
     - index: PE (index_valuation) + close (index_basic_stats) + latest
       composition (sec_composition) + constituent stock dividends
       (stock_dividends).
     - etf: close (etf_basic_stats) + implied_dividend_per_share
       (etf_adjustment).
     - stock: close (stock_basic_stats) + dividends (stock_dividends).
  3. Compute pe_ma20 (index-only) and dividend_yield (trailing-12m D/P)
     on FULL history (trailing-12m DPS + 5y rolling windows need it).
  4. Write daily rows to analysis.pe_and_dividends:
     - ``--force``: DELETE sec_type rows + COPY-insert.
     - default: upsert ONLY rows whose date is in the missing-dates set.
  5. Compute monthly 5y rolling stats. Write to
     analysis.pe_and_dividend_stats:
     - ``--force``: DELETE sec_type rows + COPY-insert.
     - default: if new month-end dates are missing, DELETE sec_type rows
       + recompute (is_active flag + 5y rolling windows require full
       recompute when a new month appears). If no missing month-end
       dates, skip stats entirely.
  6. Upsert analysis_identity.

Incremental mode rationale
  The pe_ma20 and dividend_yield for past dates don't change
  retroactively (PE and dividends are historical facts), so existing
  rows are valid. New dates get appended via upsert. The monthly stats
  table has an is_active flag that flips when a new month-end appears,
  so stats is recomputed per sec_type only when new month-end dates are
  detected (otherwise skipped).
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import os
import sys
import time
import re

# Ensure project root is on sys.path so ``_common`` is importable.
sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ),
)

from _common.build_commons import (  # noqa: E402
    setup_utf8_stdout,
    get_db_connection_async,
    get_db_pool_async,
    print_build_header,
    print_wall_time,
    add_force_arg,
    find_missing_analysis_dates,
)
from _common.db_commons import bulk_upsert_async, copy_insert_async  # noqa: E402

setup_utf8_stdout()

import pandas as pd  # noqa: E402

from analyze._common import upsert_analysis_identity  # noqa: E402
from analyze.pe_and_dividends.config import (  # noqa: E402
    ANALYSIS_NAME,
    DETAIL_TABLE,
    STATS_TABLE,
    DESCRIPTION,
    SEC_TYPES,
    SEC_TYPE_IDENTITY_TABLE,
)
from analyze.pe_and_dividends.fetch import (  # noqa: E402
    fetch_active_codes,
    fetch_index_pe_and_close,
    fetch_latest_index_composition,
    fetch_stock_dividends,
    fetch_etf_close_and_dividends,
    fetch_stock_close,
    fetch_trading_dates,
)
from analyze.pe_and_dividends.compute import (  # noqa: E402
    compute_pe_ma20,
    compute_trailing_12m_dps,
    compute_index_dividend_yield,
    compute_simple_dividend_yield,
    build_detail_rows,
    compute_monthly_stats,
    find_month_end_dates,
)


# Regex to strip exchange suffixes from stock codes for cross-table joins.
_SUFFIX_RE = re.compile(r"\.(SS|SZ|SH|BJ|HK)$")


def _strip_suffix(code: str) -> str:
    """Strip exchange suffix (.SS/.SZ/.SH/.BJ/.HK) from a code string."""
    if not code:
        return code
    return _SUFFIX_RE.sub("", code.upper())


def _normalize_stock_codes(df, col: str) -> None:
    """Normalize a stock-code column in-place by stripping exchange suffixes."""
    if col in df.columns:
        df[col] = df[col].apply(_strip_suffix)


async def _process_index(
    conn, pool, *, force: bool, target_dates_detail: set | None,
    target_dates_stats: set | None,
) -> int:
    """Process sec_type='index' end-to-end."""
    st = "index"
    print(f"\n  [{st}] Fetching active codes...", flush=True)
    codes = await fetch_active_codes(conn, st)
    code_list = sorted(codes)
    print(f"  [{st}]   {len(code_list):,} active codes", flush=True)
    if not code_list:
        print(f"  [{st}]   no active codes; skipping.", flush=True)
        return 0

    # ---- Fetch source data ----------------------------------------------
    print(f"  [{st}] Fetching PE + close from index_valuation + index_basic_stats...",
          flush=True)
    close_df = await fetch_index_pe_and_close(conn, code_list)
    print(f"  [{st}]   {len(close_df):,} (code, date) rows with close", flush=True)

    print(f"  [{st}] Fetching latest composition from sec_composition...", flush=True)
    comp_df = await fetch_latest_index_composition(conn, code_list)
    print(f"  [{st}]   {len(comp_df):,} (index_code, stock_code) composition rows",
          flush=True)

    # Get unique constituent stock codes (original format, before normalization)
    if not comp_df.empty:
        constituent_codes = sorted(comp_df["stock_code"].unique().tolist())
    else:
        constituent_codes = []
    print(f"  [{st}]   {len(constituent_codes):,} unique constituent stocks",
          flush=True)

    # Fetch ALL stock dividends (small table ~12K rows) — avoids code-format
    # mismatch issues between sec_composition.stock_code and
    # stock_dividends.code (mixed suffix conventions).
    print(f"  [{st}] Fetching ALL stock dividends...", flush=True)
    div_df = await fetch_stock_dividends(conn, stock_codes=None)
    print(f"  [{st}]   {len(div_df):,} dividend events total", flush=True)

    # Normalize stock codes for cross-table join: strip exchange suffixes
    # from both composition.stock_code and dividends.code so they match
    # regardless of suffix convention.
    _normalize_stock_codes(comp_df, "stock_code")
    _normalize_stock_codes(div_df, "code")

    print(f"  [{st}] Fetching trading dates...", flush=True)
    trading_dates = await fetch_trading_dates(conn, st)
    print(f"  [{st}]   {len(trading_dates):,} trading dates", flush=True)

    # ---- Compute pe_ma20 -------------------------------------------------
    print(f"  [{st}] Computing pe_ma20 (rolling {20}-day MA of PE per code)...",
          flush=True)
    pe_ma20 = compute_pe_ma20(close_df)
    print(f"  [{st}]   {pe_ma20.notna().sum():,} non-null pe_ma20 values",
          flush=True)

    # ---- Compute dividend_yield ------------------------------------------
    print(f"  [{st}] Computing trailing-12m DPS per constituent stock...",
          flush=True)
    stock_dps = compute_trailing_12m_dps(div_df, trading_dates)
    print(f"  [{st}]   {len(stock_dps):,} (stock, date) DPS rows", flush=True)

    print(f"  [{st}] Computing index dividend_yield (weighted constituent DPS / close)...",
          flush=True)
    dy_df = compute_index_dividend_yield(close_df, comp_df, stock_dps)
    print(f"  [{st}]   {dy_df['dividend_yield'].notna().sum():,} non-null dividend_yield values",
          flush=True)

    # ---- Build + insert detail rows --------------------------------------
    print(f"  [{st}] Building detail rows...", flush=True)
    detail_rows = build_detail_rows(close_df, pe_ma20, dy_df, st)
    print(f"  [{st}]   {len(detail_rows):,} detail rows", flush=True)

    n_detail = await _write_detail(
        conn, st, detail_rows, force=force, target_dates=target_dates_detail,
    )

    # ---- Compute + insert monthly stats ----------------------------------
    # Stats always needs full recompute when new month-end dates appear
    # (is_active flag flips). Skip entirely if no missing month-end dates.
    if force or (target_dates_stats is not None and len(target_dates_stats) > 0):
        print(f"  [{st}] Computing monthly 5y rolling stats...", flush=True)
        detail_df = pd.DataFrame(detail_rows)
        pe_df = close_df[["code", "date", "pe"]].copy() if "pe" in close_df.columns else None

        stats_rows = compute_monthly_stats(
            detail_df, pe_df, comp_df, div_df, trading_dates, st
        )
        print(f"  [{st}]   {len(stats_rows):,} monthly stats rows", flush=True)
        await _write_stats(conn, st, stats_rows, force=force)
    else:
        print(f"  [{st}] Monthly stats up to date; skipping stats step.",
              flush=True)

    return n_detail


async def _process_etf(
    conn, pool, *, force: bool, target_dates_detail: set | None,
    target_dates_stats: set | None,
) -> int:
    """Process sec_type='etf' end-to-end."""
    st = "etf"
    print(f"\n  [{st}] Fetching active codes...", flush=True)
    codes = await fetch_active_codes(conn, st)
    code_list = sorted(codes)
    print(f"  [{st}]   {len(code_list):,} active codes", flush=True)
    if not code_list:
        print(f"  [{st}]   no active codes; skipping.", flush=True)
        return 0

    print(f"  [{st}] Fetching close + pe + implied_dividend_per_share...", flush=True)
    etf_df = await fetch_etf_close_and_dividends(conn, code_list)
    print(f"  [{st}]   {len(etf_df):,} (code, date) rows", flush=True)

    print(f"  [{st}] Fetching trading dates...", flush=True)
    trading_dates = await fetch_trading_dates(conn, st)
    print(f"  [{st}]   {len(trading_dates):,} trading dates", flush=True)

    # Convert ETF adjustment data to dividend events
    div_events = etf_df[etf_df["implied_dividend_per_share"] > 0].copy()
    div_events = div_events.rename(columns={
        "date": "ex_dividend_date",
        "implied_dividend_per_share": "dividend_per_share_pre_tax",
    })[["code", "ex_dividend_date", "dividend_per_share_pre_tax"]]
    print(f"  [{st}]   {len(div_events):,} ETF dividend events", flush=True)

    close_df = etf_df[["code", "date", "close", "pe"]].copy()

    # Compute pe_ma20 (ETF PE is pre-computed by builds.etf via harmonic weighting)
    pe_df = close_df[["code", "date", "pe"]].copy()
    pe_ma20 = compute_pe_ma20(close_df)
    print(f"  [{st}]   {pe_ma20.notna().sum():,} non-null pe_ma20 values", flush=True)

    # Compute dividend_yield
    dy_df = compute_simple_dividend_yield(close_df, div_events, trading_dates)

    # Build + insert detail rows
    detail_rows = build_detail_rows(close_df, pe_ma20, dy_df, st)
    print(f"  [{st}]   {len(detail_rows):,} detail rows", flush=True)

    n_detail = await _write_detail(
        conn, st, detail_rows, force=force, target_dates=target_dates_detail,
    )

    # Monthly stats
    if force or (target_dates_stats is not None and len(target_dates_stats) > 0):
        print(f"  [{st}] Computing monthly 5y rolling stats...", flush=True)
        detail_df = pd.DataFrame(detail_rows)
        stats_rows = compute_monthly_stats(
            detail_df, pe_df, None, div_events, trading_dates, st
        )
        print(f"  [{st}]   {len(stats_rows):,} monthly stats rows", flush=True)
        await _write_stats(conn, st, stats_rows, force=force)
    else:
        print(f"  [{st}] Monthly stats up to date; skipping stats step.",
              flush=True)

    return n_detail


async def _process_stock(
    conn, pool, *, force: bool, target_dates_detail: set | None,
    target_dates_stats: set | None,
) -> int:
    """Process sec_type='stock' end-to-end."""
    st = "stock"
    print(f"\n  [{st}] Fetching active codes...", flush=True)
    codes = await fetch_active_codes(conn, st)
    code_list = sorted(codes)
    print(f"  [{st}]   {len(code_list):,} active codes", flush=True)
    if not code_list:
        print(f"  [{st}]   no active codes; skipping.", flush=True)
        return 0

    print(f"  [{st}] Fetching close + pe...", flush=True)
    close_df = await fetch_stock_close(conn, code_list)
    print(f"  [{st}]   {len(close_df):,} (code, date) rows", flush=True)

    print(f"  [{st}] Fetching dividends (all stock_dividends)...", flush=True)
    div_df = await fetch_stock_dividends(conn, stock_codes=None)
    print(f"  [{st}]   {len(div_df):,} dividend events total", flush=True)
    # NOTE: Do NOT strip exchange suffixes for stocks.
    # stats.stock_basic_stats.code and stats.stock_dividends.code are BOTH
    # suffixed (e.g. "600000.SS") and already match each other directly.
    # Stripping would make analysis.pe_and_dividends.code (stock) BARE,
    # breaking JOINs with stats.stock_basic_stats (chart SQL),
    # stats.stock_identity (codes SQL latest_name), and
    # stats.sec_classification (META_SQL) — all of which are suffixed.

    print(f"  [{st}] Fetching trading dates...", flush=True)
    trading_dates = await fetch_trading_dates(conn, st)
    print(f"  [{st}]   {len(trading_dates):,} trading dates", flush=True)

    # Compute pe_ma20 (stock PE from stock_basic_stats.pe)
    pe_df = close_df[["code", "date", "pe"]].copy()
    pe_ma20 = compute_pe_ma20(close_df)
    print(f"  [{st}]   {pe_ma20.notna().sum():,} non-null pe_ma20 values", flush=True)

    # Compute dividend_yield
    dy_df = compute_simple_dividend_yield(close_df, div_df, trading_dates)

    # Build + insert detail rows
    detail_rows = build_detail_rows(close_df, pe_ma20, dy_df, st)
    print(f"  [{st}]   {len(detail_rows):,} detail rows", flush=True)

    n_detail = await _write_detail(
        conn, st, detail_rows, force=force, target_dates=target_dates_detail,
    )

    # Monthly stats
    if force or (target_dates_stats is not None and len(target_dates_stats) > 0):
        print(f"  [{st}] Computing monthly 5y rolling stats...", flush=True)
        detail_df = pd.DataFrame(detail_rows)
        stats_rows = compute_monthly_stats(
            detail_df, pe_df, None, div_df, trading_dates, st
        )
        print(f"  [{st}]   {len(stats_rows):,} monthly stats rows", flush=True)
        await _write_stats(conn, st, stats_rows, force=force)
    else:
        print(f"  [{st}] Monthly stats up to date; skipping stats step.",
              flush=True)

    return n_detail


_PROCESSORS = {
    "index": _process_index,
    "etf": _process_etf,
    "stock": _process_stock,
}


# ---------------------------------------------------------------------------
#  Shared write helpers (force = DELETE + COPY; incremental = filter + upsert)
# ---------------------------------------------------------------------------

async def _write_detail(
    conn, sec_type: str, detail_rows: list[dict], *,
    force: bool, target_dates: set | None,
) -> int:
    """Write detail rows to analysis.pe_and_dividends.

    - force: DELETE sec_type rows + COPY-insert.
    - incremental: filter to target_dates rows + upsert on
      (sec_type, code, date). Skipped entirely when target_dates is empty.
    """
    if not detail_rows:
        print(f"  [{sec_type}]   no detail rows to write", flush=True)
        return 0

    if force:
        print(f"  [{sec_type}] Deleting existing {sec_type} rows from "
              f"{DETAIL_TABLE}...", flush=True)
        await conn.execute(
            f"DELETE FROM {DETAIL_TABLE} WHERE sec_type = $1", sec_type
        )
        print(f"  [{sec_type}] Inserting {len(detail_rows):,} detail rows "
              f"(COPY)...", flush=True)
        n = await copy_insert_async(conn, DETAIL_TABLE, detail_rows)
        print(f"  [{sec_type}]   inserted {n:,} detail rows", flush=True)
        return n

    # Incremental: filter to missing dates only.
    if target_dates is None:
        rows_to_write = detail_rows
    else:
        if len(target_dates) == 0:
            print(f"  [{sec_type}]   detail up to date; skipping insert.",
                  flush=True)
            return 0
        rows_to_write = [
            r for r in detail_rows if r["date"] in target_dates
        ]
        print(f"  [{sec_type}] Incremental filter: {len(rows_to_write):,} of "
              f"{len(detail_rows):,} detail rows are in target_dates",
              flush=True)

    if not rows_to_write:
        print(f"  [{sec_type}]   no new detail rows to upsert", flush=True)
        return 0

    print(f"  [{sec_type}] Upserting {len(rows_to_write):,} detail rows...",
          flush=True)
    n = await bulk_upsert_async(
        conn, DETAIL_TABLE, rows_to_write,
        key_columns=["sec_type", "code", "date"],
    )
    print(f"  [{sec_type}]   upserted {n:,} detail rows", flush=True)
    return n


async def _write_stats(
    conn, sec_type: str, stats_rows: list[dict], *, force: bool,
) -> int:
    """Write monthly stats rows to analysis.pe_and_dividend_stats.

    Always DELETE sec_type + COPY-insert. The is_active flag + 5y rolling
    windows require full recompute when a new month-end date appears, so
    there is no per-row upsert path for stats — the caller gates this call
    on whether new month-end dates are actually missing.
    """
    if not stats_rows:
        print(f"  [{sec_type}]   no stats rows to write", flush=True)
        return 0

    print(f"  [{sec_type}] Deleting existing {sec_type} rows from "
          f"{STATS_TABLE}...", flush=True)
    await conn.execute(
        f"DELETE FROM {STATS_TABLE} WHERE sec_type = $1", sec_type
    )
    print(f"  [{sec_type}] Inserting {len(stats_rows):,} stats rows "
          f"(COPY)...", flush=True)
    n = await copy_insert_async(conn, STATS_TABLE, stats_rows)
    print(f"  [{sec_type}]   inserted {n:,} stats rows", flush=True)
    return n


# ---------------------------------------------------------------------------
#  Missing-date detection (incremental mode)
# ---------------------------------------------------------------------------

async def _detect_missing_dates(
    conn, sec_types: list[str], force: bool,
) -> tuple[dict[str, set], dict[str, set]]:
    """Detect missing dates per sec_type for the detail and stats tables.

    Returns (target_dates_detail, target_dates_stats):
      - target_dates_detail[st]: dates present in the identity table but
        NOT in analysis.pe_and_dividends for that sec_type.
      - target_dates_stats[st]: MONTH-END trading dates present in the
        identity table but NOT in analysis.pe_and_dividend_stats for that
        sec_type. Used to gate the stats recompute (is_active flips when a
        new month-end appears).

    In force mode both dicts map to None (meaning "all dates").
    """
    if force:
        return (
            {st: None for st in sec_types},
            {st: None for st in sec_types},
        )

    target_dates_detail: dict[str, set] = {}
    target_dates_stats: dict[str, set] = {}
    for st in sec_types:
        identity_table = SEC_TYPE_IDENTITY_TABLE[st]

        # ---- Detail table: missing dates ----
        missing_detail = await find_missing_analysis_dates(
            conn, DETAIL_TABLE, [identity_table], sec_type=st,
        )
        target_dates_detail[st] = missing_detail
        print(f"    -> detail[{st}]: {len(missing_detail)} missing dates",
              flush=True)

        # ---- Stats table: missing MONTH-END dates ----
        # The stats table only has month-end rows, so we need to compare
        # month-end trading dates from the identity table against the
        # stats table's dates for this sec_type.
        all_identity_dates = await conn.fetch(
            f'SELECT DISTINCT date FROM {identity_table}'
        )
        identity_dates = sorted(
            {r["date"] for r in all_identity_dates if r["date"] is not None}
        )
        month_ends = set(find_month_end_dates(identity_dates))

        existing_stats_rows = await conn.fetch(
            f'SELECT DISTINCT date FROM {STATS_TABLE} WHERE sec_type = $1',
            st,
        )
        existing_stats_dates = {
            r["date"] for r in existing_stats_rows if r["date"] is not None
        }
        missing_stats = month_ends - existing_stats_dates
        target_dates_stats[st] = missing_stats
        print(f"    -> stats[{st}]: {len(missing_stats)} missing month-end "
              f"dates", flush=True)

    return target_dates_detail, target_dates_stats


async def main() -> None:
    ap = argparse.ArgumentParser(
        description="PE & Dividend Yield analysis (ETF + Index + Stock)."
    )
    add_force_arg(ap)
    ap.add_argument(
        "--sec-type", choices=("index", "etf", "stock"), default=None,
        help="Process only this sec_type (for testing). Default: all.",
    )
    args = ap.parse_args()
    force = args.force

    sec_types = (args.sec_type,) if args.sec_type else SEC_TYPES

    t0 = time.time()
    print_build_header(
        "ANALYZE PE & DIVIDENDS (ETF + INDEX + STOCK)",
        detail_table=DETAIL_TABLE,
        stats_table=STATS_TABLE,
        sec_types=", ".join(sec_types),
        mode="FORCE (full recompute per sec_type)" if force
             else "incremental (missing dates only)",
    )

    conn = await get_db_connection_async()
    pool = await get_db_pool_async(min_size=1, max_size=4)
    try:
        # ---- Detect missing dates (incremental mode) --------------------
        if not force:
            print("\n  Detecting missing dates per sec_type (incremental mode)...",
                  flush=True)
        target_dates_detail, target_dates_stats = await _detect_missing_dates(
            conn, list(sec_types), force,
        )

        # Early exit if everything is up to date (incremental mode only).
        if not force:
            total_missing = (
                sum(len(s) for s in target_dates_detail.values())
                + sum(len(s) for s in target_dates_stats.values())
            )
            if total_missing == 0:
                print("    -> DB is up to date; nothing to do.", flush=True)
                print_wall_time(t0)
                return

        total = 0
        for st in sec_types:
            td_detail = target_dates_detail.get(st)
            td_stats = target_dates_stats.get(st)
            # Skip sec_type entirely if both detail and stats are up to date.
            if (not force
                    and td_detail is not None and len(td_detail) == 0
                    and td_stats is not None and len(td_stats) == 0):
                print(f"\n  [{st}] up to date; skipping.", flush=True)
                continue
            processor = _PROCESSORS[st]
            n = await processor(
                conn, pool,
                force=force,
                target_dates_detail=td_detail,
                target_dates_stats=td_stats,
            )
            total += n

        # Upsert analysis_identity
        print(f"\n  -> Upserting analysis.analysis_identity registry...",
              flush=True)
        await upsert_analysis_identity(
            conn,
            name=ANALYSIS_NAME,
            detail_name="pe_and_dividends",
            description=DESCRIPTION,
        )

        print(f"\n  TOTAL: {total:,} detail rows inserted", flush=True)
        print_wall_time(t0)
    finally:
        try:
            await asyncio.wait_for(conn.close(), timeout=10)
        except (asyncio.TimeoutError, Exception):
            pass
        try:
            await asyncio.wait_for(pool.close(), timeout=10)
        except (asyncio.TimeoutError, Exception):
            pool.terminate()


if __name__ == "__main__":
    asyncio.run(main())
