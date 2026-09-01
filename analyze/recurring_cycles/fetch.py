"""Async DB fetchers for analyze.recurring_cycles.

Loads per-(code, date) close prices from the stats schema, scoped to the
active universe (codes with recent identity-table data).

For sec_type='index':
  - close from stats.index_basic_stats

For sec_type='etf':
  - close = COALESCE(stats.etf_adjustment.adj_close,
                     stats.etf_basic_stats.close)
    (adjusted close preferred — removes dividend/split jumps that would
     create spurious frequency components)

For sec_type='stock':
  - close from stats.stock_basic_stats
"""
from __future__ import annotations

from typing import Set

import pandas as pd

from _common.build_commons import (
    fetch_codes_with_recent_data_async,
    RECENT_TRADING_DAYS,
    recent_trading_day_cutoff,
    rec_cols,
)
from _common.df_utils import epoch_col_to_dt64

from analyze.recurring_cycles.config import SEC_TYPE_IDENTITY_TABLE


# ---------------------------------------------------------------------------
#  Active-universe pre-filter
# ---------------------------------------------------------------------------
async def fetch_active_codes(conn, sec_type: str) -> Set[str]:
    """Return codes with at least one identity-table row in the last
    RECENT_TRADING_DAYS trading days.

    Stale / delisted / suspended securities with no recent data are
    excluded from the analysis universe entirely.
    """
    identity_table = SEC_TYPE_IDENTITY_TABLE[sec_type]
    cutoff = recent_trading_day_cutoff(RECENT_TRADING_DAYS)
    codes = await fetch_codes_with_recent_data_async(
        conn, identity_table, n_trading_days=RECENT_TRADING_DAYS,
    )
    print(f"      pre-filter: {len(codes):,} {sec_type} codes have "
          f"data in the last {RECENT_TRADING_DAYS} trading days "
          f"(cutoff={cutoff.isoformat()})", flush=True)
    return codes


# ---------------------------------------------------------------------------
#  Close prices — per-(code, date) close for each sec_type
# ---------------------------------------------------------------------------
async def fetch_close_prices(
    conn,
    sec_type: str,
    codes: list[str],
) -> pd.DataFrame:
    """Fetch full per-(code, date) close price history for the given codes.

    FULL history is always fetched (windows need up to 1275 prior
    trading days). The incremental target_dates filter is applied AFTER
    computation in __main__, not here.

    Args:
        conn: asyncpg connection.
        sec_type: 'index', 'etf', or 'stock' — picks the source table.
        codes: list of code strings to fetch (the universe filter should
            already have been applied).

    Returns:
        DataFrame with columns: code, date, close. Sorted by (code, date).
    """
    if not codes:
        return pd.DataFrame(columns=["code", "date", "close"])

    if sec_type == "index":
        sql = """
            SELECT code, extract(epoch from date)::float8 AS date,
                   close::float8 AS close
            FROM stats.index_basic_stats
            WHERE code = ANY($1::text[])
              AND close IS NOT NULL
            ORDER BY code, date ASC
        """
    elif sec_type == "etf":
        # Adjusted close preferred — removes dividend/split jumps that
        # would create spurious frequency components.
        sql = """
            SELECT
                b.code,
                extract(epoch from b.date)::float8 AS date,
                COALESCE(a.adj_close, b.close)::float8 AS close
            FROM stats.etf_basic_stats b
            LEFT JOIN stats.etf_adjustment a
                ON a.date = b.date AND a.code = b.code
            WHERE b.code = ANY($1::text[])
              AND b.close IS NOT NULL
            ORDER BY b.code, b.date ASC
        """
    elif sec_type == "stock":
        sql = """
            SELECT code, extract(epoch from date)::float8 AS date,
                   close::float8 AS close
            FROM stats.stock_basic_stats
            WHERE code = ANY($1::text[])
              AND close IS NOT NULL
            ORDER BY code, date ASC
        """
    else:
        raise ValueError(f"Unknown sec_type: {sec_type!r}")

    rows = await conn.fetch(sql, sorted(codes))
    if not rows:
        return pd.DataFrame(columns=["code", "date", "close"])

    # rec_cols + columns construction (options-style). The date column
    # arrives as NATIVE float8 (extract(epoch) in SQL) and is materialized
    # as datetime64[us] via epoch_col_to_dt64 — object-dtype python dates
    # would poison every downstream GPU op (MixedTypeError fallback on
    # EVERY access). `close::float8` avoids Decimal128Dtype (asyncpg
    # returns NUMERIC as Decimal -> object dtype -> per-call to_numeric
    # fallback).
    df = pd.DataFrame(rec_cols(rows), columns=["code", "date", "close"])
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    # Drop any rows where close became NaN after coercion
    df = df.dropna(subset=["close"]).reset_index(drop=True)
    return df
