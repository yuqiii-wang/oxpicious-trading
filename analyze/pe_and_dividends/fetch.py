"""Async DB fetch primitives for analyze.pe_and_dividends.

Loads per-(code, date) PE + close (index), ETF adjustment data, stock
dividends, and index composition from the stats schema, scoped to the
active universe (codes with recent identity-table data).

For sec_type='index':
  - PE from stats.index_valuation
  - close from stats.index_basic_stats
  - composition from stats.sec_composition (latest snapshot, source_type='index')
  - constituent dividends from stats.stock_dividends

For sec_type='etf':
  - close from stats.etf_basic_stats
  - implied_dividend_per_share from stats.etf_adjustment

For sec_type='stock':
  - close from stats.stock_basic_stats
  - dividends from stats.stock_dividends
"""
from __future__ import annotations

import datetime
from typing import Optional, Set

import pandas as pd

from _common.build_commons import (
    fetch_codes_with_recent_data_async,
    RECENT_TRADING_DAYS,
    recent_trading_day_cutoff,
)

from analyze.pe_and_dividends.config import SEC_TYPE_IDENTITY_TABLE


# ---------------------------------------------------------------------------
#  Active-universe pre-filter
# ---------------------------------------------------------------------------
async def fetch_active_codes(conn, sec_type: str) -> Set[str]:
    """Return codes with at least one identity-table row in the last
    RECENT_TRADING_DAYS trading days."""
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
#  Index: PE + close
# ---------------------------------------------------------------------------
async def fetch_index_pe_and_close(
    conn, codes: list[str]
) -> pd.DataFrame:
    """Fetch per-(code, date) PE + close for the given index codes.

    Returns DataFrame with columns: code, date, close, pe
    """
    if not codes:
        return pd.DataFrame(columns=["code", "date", "close", "pe"])
    rows = await conn.fetch(
        """
        SELECT
            b.code,
            b.date,
            b.close,
            v.pe
        FROM stats.index_basic_stats b
        LEFT JOIN stats.index_valuation v
            ON v.date = b.date AND v.code = b.code
        WHERE b.code = ANY($1::text[])
          AND b.close IS NOT NULL
        ORDER BY b.code, b.date ASC
        """,
        sorted(codes),
    )
    if not rows:
        return pd.DataFrame(columns=["code", "date", "close", "pe"])
    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    for col in ("close", "pe"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


# ---------------------------------------------------------------------------
#  Index: latest composition snapshot
# ---------------------------------------------------------------------------
async def fetch_latest_index_composition(
    conn, index_codes: list[str]
) -> pd.DataFrame:
    """Fetch the LATEST composition snapshot for the given index codes
    from stats.sec_composition (source_type='index').

    Temporal extrapolation: uses the latest snapshot for ALL dates (same
    pattern as industry_sentiments).

    Returns DataFrame with columns: index_code, stock_code, weight_pct
    """
    if not index_codes:
        return pd.DataFrame(columns=["index_code", "stock_code", "weight_pct"])
    rows = await conn.fetch(
        """
        WITH latest AS (
            SELECT code, MAX(snapshot_date) AS max_date
            FROM stats.sec_composition
            WHERE source_type = 'index'
              AND stock_code IS NOT NULL
              AND code = ANY($1::text[])
            GROUP BY code
        )
        SELECT
            sc.code AS index_code,
            sc.stock_code,
            sc.weight_pct
        FROM stats.sec_composition sc
        JOIN latest ld
            ON sc.code = ld.code AND sc.snapshot_date = ld.max_date
        WHERE sc.source_type = 'index'
          AND sc.stock_code IS NOT NULL
          AND sc.weight_pct > 0
        """,
        sorted(index_codes),
    )
    if not rows:
        return pd.DataFrame(columns=["index_code", "stock_code", "weight_pct"])
    df = pd.DataFrame([dict(r) for r in rows])
    df["weight_pct"] = pd.to_numeric(df["weight_pct"], errors="coerce")
    # Drop rows with null stock_code or weight_pct
    df = df.dropna(subset=["stock_code", "weight_pct"])
    return df


# ---------------------------------------------------------------------------
#  Stock dividends (for index constituent aggregation)
# ---------------------------------------------------------------------------
async def fetch_stock_dividends(
    conn, stock_codes: list[str] | None = None
) -> pd.DataFrame:
    """Fetch per-(code, ex_dividend_date) dividend_per_share_pre_tax.

    When ``stock_codes`` is supplied, filters to those codes using
    REGEXP_REPLACE matching (strips exchange suffixes from both sides)
    because sec_composition.stock_code and stock_dividends.code may use
    different suffix conventions. When None, fetches ALL dividends
    (only ~12K rows total — small enough to load in full).

    Returns DataFrame with columns: code, ex_dividend_date, dividend_per_share_pre_tax
    """
    if stock_codes is not None and not stock_codes:
        return pd.DataFrame(
            columns=["code", "ex_dividend_date", "dividend_per_share_pre_tax"]
        )

    if stock_codes is None:
        # Fetch all — small table (~12K rows)
        rows = await conn.fetch(
            """
            SELECT code, ex_dividend_date, dividend_per_share_pre_tax
            FROM stats.stock_dividends
            WHERE dividend_per_share_pre_tax IS NOT NULL
              AND dividend_per_share_pre_tax > 0
            ORDER BY code, ex_dividend_date ASC
            """
        )
    else:
        # Filter by stripped code match (handles mixed suffix conventions)
        rows = await conn.fetch(
            """
            SELECT code, ex_dividend_date, dividend_per_share_pre_tax
            FROM stats.stock_dividends
            WHERE dividend_per_share_pre_tax IS NOT NULL
              AND dividend_per_share_pre_tax > 0
              AND REGEXP_REPLACE(code, '\\.(SS|SZ|SH|BJ|HK)$', '') = ANY($1::text[])
            ORDER BY code, ex_dividend_date ASC
            """,
            sorted(stock_codes),
        )
    if not rows:
        return pd.DataFrame(
            columns=["code", "ex_dividend_date", "dividend_per_share_pre_tax"]
        )
    df = pd.DataFrame([dict(r) for r in rows])
    df["ex_dividend_date"] = pd.to_datetime(df["ex_dividend_date"]).dt.date
    df["dividend_per_share_pre_tax"] = pd.to_numeric(
        df["dividend_per_share_pre_tax"], errors="coerce"
    )
    return df


# ---------------------------------------------------------------------------
#  ETF: close + implied_dividend_per_share
# ---------------------------------------------------------------------------
async def fetch_etf_close_and_dividends(
    conn, codes: list[str]
) -> pd.DataFrame:
    """Fetch per-(code, date) close + pe + implied_dividend_per_share for ETFs.

    PE is pre-computed by builds.etf via harmonic-weighted constituent PE.

    Returns DataFrame with columns: code, date, close, pe, implied_dividend_per_share
    """
    if not codes:
        return pd.DataFrame(
            columns=["code", "date", "close", "pe", "implied_dividend_per_share"]
        )
    rows = await conn.fetch(
        """
        SELECT
            b.code,
            b.date,
            b.close,
            b.pe,
            COALESCE(a.implied_dividend_per_share, 0) AS implied_dividend_per_share
        FROM stats.etf_basic_stats b
        LEFT JOIN stats.etf_adjustment a
            ON a.date = b.date AND a.code = b.code
        WHERE b.code = ANY($1::text[])
          AND b.close IS NOT NULL
        ORDER BY b.code, b.date ASC
        """,
        sorted(codes),
    )
    if not rows:
        return pd.DataFrame(
            columns=["code", "date", "close", "pe", "implied_dividend_per_share"]
        )
    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    for col in ("close", "pe", "implied_dividend_per_share"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


# ---------------------------------------------------------------------------
#  Stock: close + dividends
# ---------------------------------------------------------------------------
async def fetch_stock_close(
    conn, codes: list[str]
) -> pd.DataFrame:
    """Fetch per-(code, date) close + pe for stocks.

    Returns DataFrame with columns: code, date, close, pe
    """
    if not codes:
        return pd.DataFrame(columns=["code", "date", "close", "pe"])
    rows = await conn.fetch(
        """
        SELECT code, date, close, pe
        FROM stats.stock_basic_stats
        WHERE code = ANY($1::text[])
          AND close IS NOT NULL
        ORDER BY code, date ASC
        """,
        sorted(codes),
    )
    if not rows:
        return pd.DataFrame(columns=["code", "date", "close", "pe"])
    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["pe"] = pd.to_numeric(df["pe"], errors="coerce")
    return df


# ---------------------------------------------------------------------------
#  Trading dates (distinct dates from identity table)
# ---------------------------------------------------------------------------
async def fetch_trading_dates(conn, sec_type: str) -> list[datetime.date]:
    """Fetch distinct trading dates from the identity table for the given
    sec_type. Used as the date axis for trailing-12m DPS computation."""
    identity_table = SEC_TYPE_IDENTITY_TABLE[sec_type]
    rows = await conn.fetch(
        f"SELECT DISTINCT date FROM {identity_table} ORDER BY date ASC"
    )
    return [r["date"] for r in rows if r["date"] is not None]
