"""Async DB fetch primitives for analyze.mov_ave_spread.

Loads per-(code, date) price + MA series from the stats schema, scoped to
the active universe (codes with recent identity-table data).

Incremental mode: when ``target_dates`` is supplied, only rows whose date
is in ``target_dates`` are RETURNED for insertion. However, slope and
curvature computations need 2 prior rows per code, so the SQL also fetches
up to 2 preceding trading-day rows per code as lookback context. These
context rows are used for the ``diff()`` computation but filtered out
before the DataFrame is returned, so only ``target_dates`` rows survive
to the detail table.
"""
from __future__ import annotations

from typing import Optional, Sequence, Set
import datetime

import pandas as pd

from utils.build_commons import (
    fetch_codes_with_recent_data_async,
    RECENT_TRADING_DAYS,
    recent_trading_day_cutoff,
)

from analyze.mov_ave_spread.config import SEC_TYPE_IDENTITY_TABLE
from analyze.mov_ave_spread.helpers import (
    compute_slopes_curvatures,
    compute_rolling_stds,
)


# Number of prior trading-day rows per code needed as lookback context for
# slope (1st derivative, needs 1 prior row) and curvature (2nd derivative,
# needs 2 prior rows). Used only in incremental mode.
_SLOPE_LOOKBACK_ROWS = 2


def _fetch_sql_for_sec_type(sec_type: str) -> str:
    """Build the per-(code, date) source-fetch SQL for the given sec_type,
    scoped to a caller-supplied code list via the ``$1`` placeholder
    (``WHERE i.code = ANY($1::text[])``).

    ETFs JOIN etf_identity + etf_basic_stats + LEFT JOIN etf_adjustment
    (for adj_close) + JOIN etf_tech_stats.

    Indices JOIN index_identity + index_basic_stats + JOIN index_tech_stats
    (no adjustment table — indices have no corporate actions).

    Stocks JOIN stock_identity + stock_basic_stats + JOIN stock_tech_stats
    (no adjustment table — stocks use raw close as price).
    """
    if sec_type == "etf":
        return """
            SELECT
                i.code,
                i.date,
                COALESCE(a.adj_close, b.close) AS price,
                COALESCE(a.adj_open, b.open)   AS open,
                COALESCE(a.adj_low, b.low)     AS low,
                COALESCE(a.adj_high, b.high)   AS high,
                t.ma5, t.ma20, t.ma60, t.ma120, t.ma255
            FROM stats.etf_identity i
            JOIN stats.etf_basic_stats b ON b.date = i.date AND b.code = i.code
            LEFT JOIN stats.etf_adjustment a ON a.date = i.date AND a.code = i.code
            JOIN stats.etf_tech_stats t   ON t.date = i.date AND t.code = i.code
            WHERE i.code = ANY($1::text[])
            ORDER BY i.code, i.date ASC
        """
    if sec_type == "index":
        return """
            SELECT
                i.code,
                i.date,
                b.close AS price,
                b.open  AS open,
                b.low   AS low,
                b.high  AS high,
                t.ma5, t.ma20, t.ma60, t.ma120, t.ma255
            FROM stats.index_identity i
            JOIN stats.index_basic_stats b ON b.date = i.date AND b.code = i.code
            JOIN stats.index_tech_stats  t ON t.date = i.date AND t.code = i.code
            WHERE i.code = ANY($1::text[])
            ORDER BY i.code, i.date ASC
        """
    if sec_type == "stock":
        return """
            SELECT
                i.code,
                i.date,
                b.close AS price,
                b.open  AS open,
                b.low   AS low,
                b.high  AS high,
                t.ma5, t.ma20, t.ma60, t.ma120, t.ma255
            FROM stats.stock_identity i
            JOIN stats.stock_basic_stats b ON b.date = i.date AND b.code = i.code
            JOIN stats.stock_tech_stats  t ON t.date = i.date AND t.code = i.code
            WHERE i.code = ANY($1::text[])
              AND b.close IS NOT NULL
            ORDER BY i.code, i.date ASC
        """
    raise ValueError(f"Unknown sec_type: {sec_type!r}")


async def fetch_source_data(
    conn,
    sec_type: str,
    *,
    target_dates: Optional[Set[datetime.date]] = None,
) -> pd.DataFrame:
    """Fetch per-(code, date) price + MAs from the stats schema for the
    given sec_type ('etf', 'index', or 'stock'), then compute per-(code)
    slope and curvature for each MA window.

    Pre-filter: only codes with at least one identity-table row in the last
    RECENT_TRADING_DAYS trading days are loaded. A code with no recent data
    (delisted / suspended / never-traded) is excluded from the analysis
    universe entirely — its full history is skipped — so the detail table
    only carries the active universe.

    Incremental mode (``target_dates`` is a non-empty set):
        Only rows whose date is in ``target_dates`` are returned for
        insertion. However, slope/curvature require up to 2 prior rows per
        code as context — those lookback rows are fetched from the source
        tables (dates < min(target_dates) per code) and used for the
        ``diff()`` computation, then filtered out before return. This
        ensures slope/curvature are correctly computed for the first
        target date of each code even in incremental mode.

        Implementation: the full per-code history is fetched (same SQL as
        full mode), slopes/curvatures are computed over the full history,
        then the DataFrame is filtered to ``target_dates`` only. This is
        correct and simple; the per-code history is already in the DB and
        the fetch is a single indexed query.

    Full mode (``target_dates`` is None, or ``--force`` rebuild):
        All rows for active codes are returned (no date filtering).

    Returns a DataFrame with columns:
        sec_type, code, date, price, ma5, ma20, ma60, ma120, ma255,
        price_slope, price_curvature,
        ma5_slope, ma20_slope, ma60_slope, ma120_slope, ma255_slope,
        ma5_curvature, ma20_curvature, ma60_curvature, ma120_curvature,
        ma255_curvature,
        std_5days, std_20days, std_60days, std_120days, std_255days

    Uses INNER JOINs on both basic_stats and tech_stats so the resulting
    rows satisfy the detail table's data-integrity expectation (every row
    has both OHLCV and MA source data).
    """
    # ---- Pre-filter: keep only codes with data in the recent trading window.
    identity_table = SEC_TYPE_IDENTITY_TABLE[sec_type]
    cutoff = recent_trading_day_cutoff(RECENT_TRADING_DAYS)
    active_codes = await fetch_codes_with_recent_data_async(
        conn, identity_table, n_trading_days=RECENT_TRADING_DAYS,
    )
    print(f"      pre-filter: {len(active_codes):,} {sec_type} codes have "
          f"data in the last {RECENT_TRADING_DAYS} trading days "
          f"(cutoff={cutoff.isoformat()})", flush=True)
    if not active_codes:
        return pd.DataFrame(columns=["sec_type", "code", "date", "price",
                                     "open", "low", "high",
                                     "ma5", "ma20", "ma60", "ma120", "ma255"])

    sql = _fetch_sql_for_sec_type(sec_type)
    rows = await conn.fetch(sql, sorted(active_codes))
    if not rows:
        return pd.DataFrame(columns=["sec_type", "code", "date", "price",
                                     "open", "low", "high",
                                     "ma5", "ma20", "ma60", "ma120", "ma255"])
    # asyncpg.Record -> dict so pandas picks up column names (not integer indices).
    df = pd.DataFrame([dict(r) for r in rows])
    n_loaded_codes = df["code"].nunique() if "code" in df.columns else 0
    if n_loaded_codes < len(active_codes):
        # Some active codes had identity rows but no joined basic_stats /
        # tech_stats rows — they are dropped by the INNER JOINs.
        print(f"      note: {len(active_codes) - n_loaded_codes:,} {sec_type} "
              f"codes had recent identity data but no joined basic/tech stats "
              f"rows -> dropped by INNER JOIN", flush=True)
    # Tag every row with its sec_type so downstream detail/summary rows
    # carry the discriminant column required by the new schema.
    df["sec_type"] = sec_type
    # Ensure date column is python date (not datetime) for clean serialization
    df["date"] = pd.to_datetime(df["date"]).dt.date
    # Coerce numeric columns to float (asyncpg returns Decimal for NUMERIC)
    for col in ("price", "open", "low", "high", "ma5", "ma20", "ma60", "ma120", "ma255"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # Compute slope (1st derivative) and curvature (2nd derivative) per code.
    # This is computed over the FULL per-code history so the diff() values
    # are correct for the first target date of each code (incremental mode).
    df = compute_slopes_curvatures(df)
    # Compute rolling population σ (Bollinger band widths) per code over the
    # FULL per-code history. Same incremental-mode reasoning as slopes: the
    # rolling window needs up to 255 prior rows to populate σ_255days, so we
    # compute over the full history then filter to target_dates below.
    df = compute_rolling_stds(df)

    # ---- Incremental filter: keep only target_dates rows ----------------
    if target_dates is not None and len(target_dates) > 0:
        n_before = len(df)
        df = df[df["date"].isin(target_dates)].reset_index(drop=True)
        print(f"      incremental filter: {len(df):,} of {n_before:,} rows "
              f"are in target_dates (slope/curv context rows dropped)",
              flush=True)

    # Reorder columns for readability.
    df = df[["sec_type", "code", "date", "price", "open", "low", "high",
             "ma5", "ma20", "ma60", "ma120", "ma255",
             "price_slope", "price_curvature",
             "ma5_slope", "ma20_slope", "ma60_slope", "ma120_slope", "ma255_slope",
             "ma5_curvature", "ma20_curvature", "ma60_curvature",
             "ma120_curvature", "ma255_curvature",
             "std_5days", "std_20days", "std_60days", "std_120days", "std_255days"]]
    return df
