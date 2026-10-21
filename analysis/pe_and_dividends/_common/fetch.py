"""Async DB fetch primitives for analysis.pe_and_dividends.

Loads per-(code, date) PE + close (index), ETF adjustment data, stock
dividends, and index composition from the stats schema, scoped to the
active universe (codes with recent identity-table data).

For sec_type='index':
  - PE from stats.index_valuation
  - close from stats.index_basic_stats
  - composition from stats.sec_composition (latest snapshot, source_type='index')
  - constituent dividends from stats.stock_dividends
  - constituent closes from stats.stock_basic_stats (cap-weighted
    constituent-yield aggregation)

For sec_type='etf':
  - close from stats.etf_basic_stats
  - implied_dividend_per_share from stats.etf_adjustment

For sec_type='stock':
  - close from stats.stock_basic_stats
  - dividends from stats.stock_dividends

For sec_type='industry':
  - close from stats.industry_basic_stats.mean_close (pool 'all' — the
    rebased-to-100 composite close)
  - PE from stats.industry_basic_stats.mean_pe (the build's
    cross-sectional mean of member-index raw PE)
  - dividend_yield is aggregated from the member indices' OWN
    analysis.dividends rows (see fetch_industry_member_yields)
"""
from __future__ import annotations

import datetime
import re
from typing import Optional, Set

import pandas as pd

from _common.build_commons import (
    fetch_codes_with_recent_data_async,
    rec_cols,
    RECENT_TRADING_DAYS,
    recent_trading_day_cutoff,
)
from _common.df_utils import epoch_col_to_dt64

from analysis.pe_and_dividends._common.config import SEC_TYPE_IDENTITY_TABLE

import logging
logger = logging.getLogger(__name__)


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
    logger.info(f"      pre-filter: {len(codes):,} {sec_type} codes have "
          f"data in the last {RECENT_TRADING_DAYS} trading days "
          f"(cutoff={cutoff.isoformat()})")
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
            extract(epoch from b.date)::float8 AS date,
            b.close::float8 AS close,
            v.pe::float8 AS pe
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
    # Whole-column positional unpack (rec_cols) — no per-row dict access.
    # The date column arrives as NATIVE float8 (extract(epoch) in SQL) and
    # is materialized as datetime64[us] BEFORE the ctor (epoch_col_to_dt64)
    # — the frame is cuDF-representable from construction, never
    # object-poisoned, with an explicit unit.
    cols = rec_cols(rows)
    cols["date"] = epoch_col_to_dt64(cols["date"])
    return pd.DataFrame(cols, columns=["code", "date", "close", "pe"])


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
            sc.weight_pct::float8 AS weight_pct
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
    # weight_pct arrives as float8 (::float8 cast) — no to_numeric needed.
    df = pd.DataFrame(rec_cols(rows))
    # Drop rows with null stock_code or weight_pct
    df = df.dropna(subset=["stock_code", "weight_pct"])
    return df


# ---------------------------------------------------------------------------
#  Constituent closes (for the index cap-weighted constituent yield)
# ---------------------------------------------------------------------------
async def fetch_constituent_closes(
    conn, stock_codes: list[str]
) -> pd.DataFrame:
    """Fetch per-(code, date) close for the given constituent stocks.

    Codes are SUFFIX-STRIPPED on both sides (REGEXP_REPLACE) so they join
    with the normalized sec_composition.stock_code / stock_dividends.code
    keys used by the index dividend-yield aggregation. The input list may
    carry either convention — it is stripped here before the query.

    Returns DataFrame with columns: code, date, close
    """
    if not stock_codes:
        return pd.DataFrame(columns=["code", "date", "close"])
    stripped_codes = sorted({
        re.sub(r"\.(SS|SZ|SH|BJ|HK)$", "", str(c).upper())
        for c in stock_codes
    })
    if not stripped_codes:
        return pd.DataFrame(columns=["code", "date", "close"])
    rows = await conn.fetch(
        """
        SELECT REGEXP_REPLACE(code, '\\.(SS|SZ|SH|BJ|HK)$', '') AS code,
               extract(epoch from date)::float8 AS date,
               close::float8 AS close
        FROM stats.stock_basic_stats
        WHERE close IS NOT NULL
          AND REGEXP_REPLACE(code, '\\.(SS|SZ|SH|BJ|HK)$', '') = ANY($1::text[])
        ORDER BY code, date ASC
        """,
        stripped_codes,
    )
    if not rows:
        return pd.DataFrame(columns=["code", "date", "close"])
    cols = rec_cols(rows)
    cols["date"] = epoch_col_to_dt64(cols["date"])
    return pd.DataFrame(cols, columns=["code", "date", "close"])


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
            SELECT code,
                   extract(epoch from ex_dividend_date)::float8 AS ex_dividend_date,
                   dividend_per_share_pre_tax::float8 AS dividend_per_share_pre_tax
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
            SELECT code,
                   extract(epoch from ex_dividend_date)::float8 AS ex_dividend_date,
                   dividend_per_share_pre_tax::float8 AS dividend_per_share_pre_tax
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
    # Whole-column positional unpack; date column pre-converted to
    # datetime64[us] via epoch_col_to_dt64 (never object-date in the ctor).
    cols = rec_cols(rows)
    cols["ex_dividend_date"] = epoch_col_to_dt64(cols["ex_dividend_date"])
    return pd.DataFrame(
        cols,
        columns=["code", "ex_dividend_date", "dividend_per_share_pre_tax"],
    )


# ---------------------------------------------------------------------------
#  ETF: close + implied_dividend_per_share
# ---------------------------------------------------------------------------
async def fetch_etf_close_and_dividends(
    conn, codes: list[str]
) -> pd.DataFrame:
    """Fetch per-(code, date) close + pe + eps + implied_dividend_per_share
    for ETFs.

    PE is pre-computed by builds.etf via harmonic-weighted constituent PE;
    eps = close/pe (the deliveries stage's per-event EPS audit).

    Returns DataFrame with columns: code, date, close, pe, eps, implied_dividend_per_share
    """
    if not codes:
        return pd.DataFrame(
            columns=["code", "date", "close", "pe", "eps", "implied_dividend_per_share"]
        )
    rows = await conn.fetch(
        """
        SELECT
            b.code,
            extract(epoch from b.date)::float8 AS date,
            b.close::float8 AS close,
            b.pe::float8 AS pe,
            -- COALESCE to NaN: an all-NULL column (no-PE codes) would
            -- otherwise build an object-dtype all-None column in the
            -- frame — NaN keeps it float64 end to end.
            COALESCE(b.eps::float8, 'NaN'::float8) AS eps,
            COALESCE(a.implied_dividend_per_share, 0)::float8 AS implied_dividend_per_share
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
            columns=["code", "date", "close", "pe", "eps", "implied_dividend_per_share"]
        )
    cols = rec_cols(rows)
    cols["date"] = epoch_col_to_dt64(cols["date"])
    return pd.DataFrame(
        cols,
        columns=["code", "date", "close", "pe", "eps", "implied_dividend_per_share"],
    )


# ---------------------------------------------------------------------------
#  Stock: close + dividends
# ---------------------------------------------------------------------------
async def fetch_stock_close(
    conn, codes: list[str]
) -> pd.DataFrame:
    """Fetch per-(code, date) close + pe + eps for stocks.

    Returns DataFrame with columns: code, date, close, pe, eps
    """
    if not codes:
        return pd.DataFrame(columns=["code", "date", "close", "pe", "eps"])
    rows = await conn.fetch(
        """
        SELECT code,
               extract(epoch from date)::float8 AS date,
               close::float8 AS close, pe::float8 AS pe,
               -- COALESCE to NaN: an all-NULL column (no-PE codes) would
               -- otherwise build an object-dtype all-None column in the
               -- frame — NaN keeps it float64 end to end.
               COALESCE(eps::float8, 'NaN'::float8) AS eps
        FROM stats.stock_basic_stats
        WHERE code = ANY($1::text[])
          AND close IS NOT NULL
        ORDER BY code, date ASC
        """,
        sorted(codes),
    )
    if not rows:
        return pd.DataFrame(columns=["code", "date", "close", "pe", "eps"])
    cols = rec_cols(rows)
    cols["date"] = epoch_col_to_dt64(cols["date"])
    return pd.DataFrame(cols, columns=["code", "date", "close", "pe", "eps"])


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


# ---------------------------------------------------------------------------
#  Industry: composite PE + close (pool 'all' slice)
# ---------------------------------------------------------------------------
async def fetch_industry_pe_and_close(
    conn, codes: list[str]
) -> pd.DataFrame:
    """Fetch per-(industry_id, date) composite PE + close for the given
    industry codes (sec_type='industry').

    Source: stats.industry_basic_stats pool-'all' (3) slice — mean_pe is
    the build's cross-sectional AVG of member-index raw PE (a ratio, so
    NO rebasing), mean_close the rebased-to-100 composite close. Both are
    pre-computed by builds.industry; this pipeline does not re-aggregate.

    Returns DataFrame with columns: code, date, close, pe
    """
    if not codes:
        return pd.DataFrame(columns=["code", "date", "close", "pe"])
    rows = await conn.fetch(
        """
        SELECT
            b.industry_id AS code,
            extract(epoch from b.date)::float8 AS date,
            b.mean_close::float8 AS close,
            b.mean_pe::float8 AS pe
        FROM stats.industry_basic_stats b
        WHERE b.industry_id = ANY($1::text[])
          AND b.pool_size = 3
          AND b.mean_close IS NOT NULL
        ORDER BY b.industry_id, b.date ASC
        """,
        sorted(codes),
    )
    if not rows:
        return pd.DataFrame(columns=["code", "date", "close", "pe"])
    # Whole-column positional unpack + epoch->datetime64[us] at the frame
    # boundary (the fetch convention — never an object-date column).
    cols = rec_cols(rows)
    cols["date"] = epoch_col_to_dt64(cols["date"])
    return pd.DataFrame(cols, columns=["code", "date", "close", "pe"])


# ---------------------------------------------------------------------------
#  Industry: member-index dividend yields (for the cross-sectional mean)
# ---------------------------------------------------------------------------
async def fetch_industry_member_yields(conn) -> pd.DataFrame:
    """Fetch per-(industry_id, date, index_code, dividend_yield) rows — the
    index-side yields this pipeline itself wrote, joined to their industry
    membership.

    Members = stats.sec_classification rows (type='index', is_active,
    industry_id non-empty) — the SAME membership the industry build
    aggregates over. Yields come from analysis.dividends (sec_type=1
    index) as-is: each member's value is already a trailing-12m yield, so
    the industry stage only cross-sectional-means them (compute.
    compute_industry_dividend_yield) — the same AVG-across-members
    convention as stats.industry_basic_stats.mean_pe.

    MUST run AFTER the index processor has written its rows (the
    industry sec_type is ordered last in SEC_TYPES for exactly that).

    Returns DataFrame with columns: code (industry_id), date
    (datetime64[us]), dividend_yield (one row per member index with a
    yield on that date).
    """
    rows = await conn.fetch(
        """
        SELECT
            sc.industry_id AS code,
            extract(epoch from d.date)::float8 AS date,
            d.dividend_yield::float8 AS dividend_yield
        FROM stats.sec_classification sc
        JOIN analysis.dividends d
          ON d.code = sc.code AND d.sec_type = 1
        WHERE sc.type = 'index'
          AND sc.is_active = TRUE
          AND sc.industry_id IS NOT NULL
          AND sc.industry_id <> ''
        """
    )
    if not rows:
        return pd.DataFrame(columns=["code", "date", "dividend_yield"])
    cols = rec_cols(rows)
    cols["date"] = epoch_col_to_dt64(cols["date"])
    return pd.DataFrame(cols, columns=["code", "date", "dividend_yield"])
