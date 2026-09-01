"""Async DB fetchers for analyze.analysis_forecasts.

Loads, per sec_type, the joined long-format input frame:

  price   — the same price convention as the parent mov_ave_spread
            analysis (ETF = COALESCE(etf_adjustment.adj_close, close);
            index / stock = *_basic_stats.close),
  high/low — the day's intraday extremes, ETF-scaled by the SAME
            adj_close/close factor as price so the Bollinger-excess
            metrics stay in one consistent price space,
  ma_{W}  — from stats.{sec_type}_tech_stats (ma5/20/60/120/255),
  rsi_{W} — from analysis.mov_ave_rsi (Wilder RSI columns),
  std_{W} — from analysis.mov_ave_spreads_detail (Bollinger sigma).

Plus the compact market-hype EPISODES list
(analysis.mov_ave_market_hypes) used to build the per-(date, code)
hyped-date matrix.

All NUMERIC columns are cast to native float8 in SQL (Decimal objects
would poison the cudf.pandas fast path) and the date columns arrive as
epoch float8, materialized via epoch_col_to_dt64.
"""
from __future__ import annotations

from datetime import date
from typing import Set

import numpy as np
import pandas as pd

from _common.build_commons import (
    fetch_codes_with_recent_data_async,
    RECENT_TRADING_DAYS,
    recent_trading_day_cutoff,
    rec_cols,
)
from _common.df_utils import epoch_col_to_dt64, grouped_shift

from analyze.analysis_forecasts.config import (
    FORWARD_HORIZONS,
    MA_WINDOWS,
    RSI_WINDOWS,
    SEC_TYPE_IDENTITY_TABLE,
)

# Output column order (matches the SELECT list below).
_COLUMNS = (
    ["code", "date", "price", "high", "low"]
    + [f"ma_{w}days" for w in MA_WINDOWS]
    + [f"rsi_{w}days" for w in RSI_WINDOWS]
    + [f"std_{w}days" for w in MA_WINDOWS]
)

# Base table + price/high/low expressions per sec_type (same price
# convention as analyze.mov_ave_spread: ETF uses the adjusted close when
# available, and high/low carry the same adj factor so excess metrics
# stay consistent with the price the bands are computed on).
_PRICE_SOURCE = {
    "index": (
        "stats.index_basic_stats b",
        "b.close",
        "b.high",
        "b.low",
    ),
    "etf": (
        "stats.etf_basic_stats b "
        "LEFT JOIN stats.etf_adjustment a "
        "ON a.code = b.code AND a.date = b.date",
        "COALESCE(a.adj_close, b.close)",
        "(b.high * COALESCE(a.adj_close / NULLIF(b.close, 0), 1.0))",
        "(b.low * COALESCE(a.adj_close / NULLIF(b.close, 0), 1.0))",
    ),
    "stock": (
        "stats.stock_basic_stats b",
        "b.close",
        "b.high",
        "b.low",
    ),
}


async def fetch_active_codes(conn, sec_type: str) -> Set[str]:
    """Return codes with at least one identity-table row in the last
    RECENT_TRADING_DAYS trading days (delisted / suspended securities are
    excluded from the analysis universe entirely)."""
    identity_table = SEC_TYPE_IDENTITY_TABLE[sec_type]
    cutoff = recent_trading_day_cutoff(RECENT_TRADING_DAYS)
    codes = await fetch_codes_with_recent_data_async(
        conn, identity_table, n_trading_days=RECENT_TRADING_DAYS,
    )
    print(f"      pre-filter: {len(codes):,} {sec_type} codes have "
          f"data in the last {RECENT_TRADING_DAYS} trading days "
          f"(cutoff={cutoff.isoformat()})", flush=True)
    return codes


# True first-data-date source per sec_type (the base OHLCV table — the
# same rows fetch_analysis_inputs admits via b.close IS NOT NULL; kept
# join-free since min(b.date) needs no adjustment/tech columns).
_FIRST_DATE_SOURCE = {
    "index": "stats.index_basic_stats",
    "etf": "stats.etf_basic_stats",
    "stock": "stats.stock_basic_stats",
}


async def fetch_first_dates(
    conn,
    sec_type: str,
    codes: list[str],
) -> dict[str, date]:
    """Per-code TRUE first data date (min(date) on the base OHLCV table
    with the same close IS NOT NULL filter the input fetch uses).

    The fetched input frame is bounded to the earliest needed window
    start, so a long-history code's first row in the frame is the FETCH
    boundary, not its listing date — deriving first rows from the frame
    would wrongly treat 5-year-history codes as fresh. Returns {} for an
    empty code list; codes absent from the table are simply missing from
    the dict (they map to the int64 sentinel = never-live in
    wide.first_ords_from_dates).
    """
    if not codes:
        return {}
    rows = await conn.fetch(
        f"""
        SELECT b.code, min(b.date) AS first_date
        FROM {_FIRST_DATE_SOURCE[sec_type]} b
        WHERE b.code = ANY($1::text[])
          AND b.close IS NOT NULL
        GROUP BY b.code
        """,
        sorted(codes),
    )
    return {r["code"]: r["first_date"] for r in rows}


async def fetch_analysis_inputs(
    conn,
    sec_type: str,
    codes: list[str],
    since: date,
) -> pd.DataFrame:
    """Fetch the joined long-format input frame for the given codes.

    One row per (code, date) with columns ``_COLUMNS``. Rows are bounded
    to date >= ``since`` (the earliest trailing-window start across the
    target stat months); there is NO upper date bound — forward changes
    for the last bucket days of the newest month need post-month-end
    prices.

    Args:
        conn: asyncpg connection.
        sec_type: 'index', 'etf', or 'stock'.
        codes: active-code universe (pre-filtered).
        since: inclusive lower date bound.

    Returns:
        DataFrame sorted by (code, date) with columns ``_COLUMNS``.
    """
    if not codes:
        return pd.DataFrame(columns=_COLUMNS)

    base, price_expr, high_expr, low_expr = _PRICE_SOURCE[sec_type]
    ma_cols = ",\n       ".join(
        f"t.ma{w}::float8 AS ma_{w}days" for w in MA_WINDOWS
    )
    rsi_cols = ",\n       ".join(
        f"r.rsi_{w}days::float8 AS rsi_{w}days" for w in RSI_WINDOWS
    )
    std_cols = ",\n       ".join(
        f"d.std_{w}days::float8 AS std_{w}days" for w in MA_WINDOWS
    )
    sql = f"""
        SELECT b.code,
               extract(epoch from b.date)::float8 AS date,
               {price_expr}::float8 AS price,
               {high_expr}::float8 AS high,
               {low_expr}::float8 AS low,
               {ma_cols},
               {rsi_cols},
               {std_cols}
        FROM {base}
        LEFT JOIN stats.{sec_type}_tech_stats t
               ON t.code = b.code AND t.date = b.date
        LEFT JOIN analysis.mov_ave_rsi r
               ON r.sec_type = '{sec_type}'
              AND r.code = b.code AND r.date = b.date
        LEFT JOIN analysis.mov_ave_spreads_detail d
               ON d.sec_type = '{sec_type}'
              AND d.code = b.code AND d.date = b.date
        WHERE b.code = ANY($1::text[])
          AND b.close IS NOT NULL
          AND b.date >= $2
        ORDER BY b.code, b.date ASC
    """

    rows = await conn.fetch(sql, sorted(codes), since)
    if not rows:
        return pd.DataFrame(columns=_COLUMNS)

    # Column-materialized ctor + epoch→datetime64[us] (object python dates
    # would poison every downstream op; ::float8 casts avoid Decimal).
    df = pd.DataFrame(rec_cols(rows), columns=_COLUMNS)
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    return df


def add_forward_changes(df: pd.DataFrame) -> pd.DataFrame:
    """Add next_change_{n}d columns for n in FORWARD_HORIZONS.

    next_change_{n}d = (price[t+n] - price[t]) / price[t], per code on its
    OWN trading-day sequence (grouped_shift — cuDF-accelerated; calendar
    gaps are not rows). NULL when the forward price is missing, the base
    price is ~0, or the ratio is non-finite.
    """
    for n in FORWARD_HORIZONS:
        col = f"_next_price_{n}"
        grouped_shift(df, ["code"], "price", out_names=col,
                      periods=-n, sort=False)
        prev = df["price"]
        nxt = df[col]
        out = (nxt - prev) / prev
        mask = nxt.isna() | (prev.abs() < 1e-12) | ~np.isfinite(out)
        df[f"next_change_{n}d"] = out.where(~mask)
        df = df.drop(columns=[col])
    return df


_HYPER_COLUMNS = ["code", "start_date", "end_date"]


async def fetch_hyped_episodes(
    conn,
    sec_type: str,
    since: date,
) -> pd.DataFrame:
    """Fetch the sec_type's market-hype EPISODES (compact — one row per
    episode, not per date) bounded to end_date >= ``since``.

    An episode is a concatenated hype span (start_date..end_date
    inclusive, any min_checkin_period) from analysis.mov_ave_market_hypes.
    Dates inside an episode are the "market-hyped dates"; expansion to
    per-(code, date) flags happens host-side in wide.build_hype_matrix
    (expanding in SQL to calendar rows would blow up row counts for the
    stock universe).

    Returns a DataFrame with columns ``_HYPER_COLUMNS`` (dates as
    datetime64[us]).
    """
    rows = await conn.fetch(
        """
        SELECT m.code,
               extract(epoch from m.start_date)::float8 AS start_date,
               extract(epoch from m.end_date)::float8   AS end_date
        FROM analysis.mov_ave_market_hypes m
        WHERE m.sec_type = $1
          AND m.end_date >= $2
        ORDER BY m.code, m.start_date ASC
        """,
        sec_type,
        since,
    )
    if not rows:
        return pd.DataFrame(columns=_HYPER_COLUMNS)
    df = pd.DataFrame(rec_cols(rows), columns=_HYPER_COLUMNS)
    df["start_date"] = epoch_col_to_dt64(df["start_date"], index=df.index)
    df["end_date"] = epoch_col_to_dt64(df["end_date"], index=df.index)
    return df
