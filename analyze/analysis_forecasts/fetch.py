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
  gap_{W} — from analysis.mov_ave_rsi (N-day price-return columns),
  std_{W} — from analysis.mov_ave_spreads_detail (Bollinger sigma),
  trading_amount + rz_buy — the px_vol / margin_ratio family inputs
            (daily turnover + RONGZI margin buy; rz_buy is NULL for
            'index' — indices have no margin data, so the
            margin_ratio buckets never fire there).

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
from _common.df_utils import (
    epoch_col_to_dt64,
    grouped_rolling_agg,
    grouped_shift,
)

from analyze.analysis_forecasts.config import (
    FORWARD_HORIZONS,
    GAP_WINDOWS,
    MA_WINDOWS,
    MARGIN_RATIO_Z_MIN_PERIODS,
    MARGIN_RATIO_Z_WINDOW,
    OPP_PAIR_BENCHMARK,
    OPP_PAIR_POOL_SIZE,
    PX_VOL_K_SHARP,
    PX_VOL_K_SLOW_DN,
    PX_VOL_K_SLOW_UP,
    PX_VOL_LB_WINDOW,
    PX_VOL_SIGMA_FLOOR,
    PX_VOL_SIGMA_MIN_DAYS,
    PX_VOL_SIGMA_WINDOW,
    PX_VOL_Z_HEAVY,
    PX_VOL_Z_SHRINK,
    RSI_WINDOWS,
    SEC_TYPE_IDENTITY_TABLE,
)

# Output column order (matches the SELECT list below).
_COLUMNS = (
    ["code", "date", "price", "high", "low"]
    + [f"ma_{w}days" for w in MA_WINDOWS]
    + [f"rsi_{w}days" for w in RSI_WINDOWS]
    + [f"gap_{w}days" for w in GAP_WINDOWS]
    + [f"std_{w}days" for w in MA_WINDOWS]
    + ["trading_amount", "rz_buy"]
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

# Trading-amount + margin-buy source + estimated-close filter per
# sec_type (the px_vol / margin_ratio families' inputs; the mov_*
# engines ignore the extra columns). Index turnover lives on the base
# table and indices have NO margin data (NULL rz_buy — the
# margin_ratio family never fires for 'index'); ETF / stock turnover
# and rz_buy live on their *_liquidity_margin tables (LEFT JOIN — a
# missing margin row NULLs trading_amount / rz_buy, which simply keeps
# that day out of the px_vol / margin_ratio buckets). Estimated closes
# (synthetic flat closes on non-traded days) would pollute ret_1d /
# σ_ret — etf/index carry the is_close_estimated flag and those rows
# are filtered in SQL; stock basic stats have no such flag.
_AMT_SOURCE = {
    "index": (
        "",
        "b.trading_amount",
        "NULL::float8",
        "AND COALESCE(b.is_close_estimated, FALSE) = FALSE",
    ),
    "etf": (
        "LEFT JOIN stats.etf_liquidity_margin m "
        "ON m.code = b.code AND m.date = b.date",
        "m.trading_amount",
        "m.rz_buy",
        "AND COALESCE(b.is_close_estimated, FALSE) = FALSE",
    ),
    "stock": (
        "LEFT JOIN stats.stock_liquidity_margin m "
        "ON m.code = b.code AND m.date = b.date",
        "m.trading_amount",
        "m.rz_buy",
        "",
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
    amt_join, amt_expr, rz_expr, est_filter = _AMT_SOURCE[sec_type]
    ma_cols = ",\n       ".join(
        f"t.ma{w}::float8 AS ma_{w}days" for w in MA_WINDOWS
    )
    rsi_cols = ",\n       ".join(
        f"r.rsi_{w}days::float8 AS rsi_{w}days" for w in RSI_WINDOWS
    )
    gap_cols = ",\n       ".join(
        f"r.gap_{w}days::float8 AS gap_{w}days" for w in GAP_WINDOWS
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
               {gap_cols},
               {std_cols},
               {amt_expr}::float8 AS trading_amount,
               {rz_expr}::float8 AS rz_buy
        FROM {base}
        {amt_join}
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
          {est_filter}
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


def add_px_vol_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add the px_vol family's per-day state inputs (no look-ahead).

    Columns added:
        ret_1d    — 1-row fractional price change per code.
        px_sigma  — σ_ret: rolling PX_VOL_SIGMA_WINDOW-row (min
                    PX_VOL_SIGMA_MIN_DAYS) sample std of ret_1d,
                    SHIFTED 1 row (yesterday's σ is today's bar).
        px_t      — t = ret_1d / px_sigma, NULL where px_sigma is
                    NaN/<=0 or below PX_VOL_SIGMA_FLOOR (bond-like
                    codes never join a bucket).
        px_z      — z-scored 量比: liangbi = trading_amount /
                    mean(trading_amount, t-PX_VOL_LB_WINDOW..t-1);
                    z = (liangbi - μ_lb[t-1]) / σ_lb[t-1] with the
                    rolling PX_VOL_SIGMA_WINDOW-row moments shifted
                    1 row. NULL where any input is missing.

    The caller must have sorted df by (code, date) (the SQL ORDER BY).
    All ops are grouped pandas (cudf.pandas-accelerated): grouped_shift
    for the lags + grouped_rolling_agg for the rolling moments.
    """
    # --- price speed -----------------------------------------------------
    grouped_shift(df, ["code"], "price", out_names="_prev_price",
                  periods=1, sort=False)
    prev = df["_prev_price"]
    ret = df["price"] / prev - 1.0
    df["ret_1d"] = ret.where(prev.notna() & (prev.abs() > 1e-12)
                             & np.isfinite(ret))
    df = df.drop(columns=["_prev_price"])

    sigma = grouped_rolling_agg(
        df, "code", "ret_1d", PX_VOL_SIGMA_WINDOW,
        min_periods=PX_VOL_SIGMA_MIN_DAYS, agg="std", ddof=1, sort=False,
    )
    df["_sigma"] = sigma
    grouped_shift(df, ["code"], "_sigma", out_names="px_sigma",
                  periods=1, sort=False)
    df = df.drop(columns=["_sigma"])

    ok_sigma = (df["px_sigma"].notna() & (df["px_sigma"] > 0)
                & (df["px_sigma"] >= PX_VOL_SIGMA_FLOOR))
    df["px_t"] = (df["ret_1d"] / df["px_sigma"]).where(ok_sigma)

    # --- 量比 z-score -----------------------------------------------------
    grouped_shift(df, ["code"], "trading_amount", out_names="_ta_prev",
                  periods=1, sort=False)
    df["_lb_base"] = grouped_rolling_agg(
        df, "code", "_ta_prev", PX_VOL_LB_WINDOW,
        min_periods=PX_VOL_LB_WINDOW, agg="mean", sort=False,
    )
    ta = df["trading_amount"]
    base = df["_lb_base"]
    df["_liangbi"] = (ta / base).where(
        ta.notna() & base.notna() & (base > 1e-12))

    mu = grouped_rolling_agg(
        df, "code", "_liangbi", PX_VOL_SIGMA_WINDOW,
        min_periods=PX_VOL_SIGMA_MIN_DAYS, agg="mean", sort=False,
    )
    sig = grouped_rolling_agg(
        df, "code", "_liangbi", PX_VOL_SIGMA_WINDOW,
        min_periods=PX_VOL_SIGMA_MIN_DAYS, agg="std", ddof=1, sort=False,
    )
    df["_lb_mu"] = mu
    df["_lb_sig"] = sig
    grouped_shift(df, ["code"], "_lb_mu", out_names="_lb_mu_lag",
                  periods=1, sort=False)
    grouped_shift(df, ["code"], "_lb_sig", out_names="_lb_sig_lag",
                  periods=1, sort=False)
    lb = df["_liangbi"]
    mu_l = df["_lb_mu_lag"]
    sig_l = df["_lb_sig_lag"]
    df["px_z"] = ((lb - mu_l) / sig_l).where(
        lb.notna() & mu_l.notna() & sig_l.notna() & (sig_l > 1e-12))

    return df.drop(columns=[
        "_lb_base", "_liangbi", "_lb_mu", "_lb_sig",
        "_lb_mu_lag", "_lb_sig_lag",
    ])


def add_margin_ratio_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add the margin_ratio family's per-day state inputs (no look-ahead).

    Columns added:
        ratio — the daily 融资买入额/成交额 intensity ratio
                rz_buy / trading_amount, defined only on margin-buy days
                (rz_buy > 0, trading_amount > 0); NULL otherwise (a
                NULL rz_buy — index codes have no margin row — is also
                NULL ratio).
        nb    — no-margin-buy flag: rz_buy == 0 with trading_amount > 0
                (margin traders absent that day; the margin_ratio
                family's "inactive" state). rz_buy NULL → False.
        ratio_z — z = (ratio - μ) / σ with μ/σ = the code's rolling
                MARGIN_RATIO_Z_WINDOW-row sample moments of ratio
                (min_periods MARGIN_RATIO_Z_MIN_PERIODS non-NULL
                observations), SHIFTED 1 row (yesterday's moments are
                today's bars — px_vol convention). NULL where ratio is
                NULL or the history is short.

    The caller must have sorted df by (code, date) (the SQL ORDER BY).
    All ops are grouped pandas (cudf.pandas-accelerated).
    """
    ta = df["trading_amount"]
    rb = df["rz_buy"]
    ta_ok = ta.notna() & (ta > 0)
    df["ratio"] = (rb / ta).where(rb.notna() & (rb > 0) & ta_ok)
    df["nb"] = (rb == 0) & ta_ok

    mu = grouped_rolling_agg(
        df, "code", "ratio", MARGIN_RATIO_Z_WINDOW,
        min_periods=MARGIN_RATIO_Z_MIN_PERIODS, agg="mean", sort=False,
    )
    sig = grouped_rolling_agg(
        df, "code", "ratio", MARGIN_RATIO_Z_WINDOW,
        min_periods=MARGIN_RATIO_Z_MIN_PERIODS, agg="std", ddof=1,
        sort=False,
    )
    df["_mu"], df["_sig"] = mu, sig
    grouped_shift(df, "code", ["_mu", "_sig"], ["_mu_l", "_sig_l"],
                  periods=1, sort=False)
    df["ratio_z"] = ((df["ratio"] - df["_mu_l"]) / df["_sig_l"]).where(
        df["_sig_l"] > 0)

    return df.drop(columns=["_mu", "_sig", "_mu_l", "_sig_l"])


_HYPER_COLUMNS = ["code", "start_date", "end_date"]


# ---------------------------------------------------------------------------
#  opp_pair family inputs (industry composite + offset-benchmark trends)
# ---------------------------------------------------------------------------

_INDUSTRY_CLOSE_COLUMNS = ["code", "date", "close"]


async def fetch_opp_pair_industries(conn) -> list[str]:
    """The opp_pair industry universe — every industry_id appearing in
    EITHER endpoint of the offsets-table pair set (pool / benchmark per
    the opp_pair config). Sorted."""
    rows = await conn.fetch(
        f"""
        SELECT ind FROM (
            SELECT industry_id AS ind
            FROM analysis_composites.industry_corr_benchmark_offsets
            WHERE pool_size = '{OPP_PAIR_POOL_SIZE}'
              AND benchmark_code = '{OPP_PAIR_BENCHMARK}'
            UNION
            SELECT benchmark_industry_id AS ind
            FROM analysis_composites.industry_corr_benchmark_offsets
            WHERE pool_size = '{OPP_PAIR_POOL_SIZE}'
              AND benchmark_code = '{OPP_PAIR_BENCHMARK}'
        ) s ORDER BY ind
        """
    )
    return [r["ind"] for r in rows]


async def fetch_opp_pair_pairs(conn) -> pd.DataFrame:
    """The opp_pair PAIR SET — one row per unordered pair (industry_id <
    pair_industry_id, the offsets table's storage order) with the pair's
    LATEST offsets-table context: the opposite score + offset_sub_corr of
    its most recent full 60d window (motivation context recorded on every
    bucket row; the latest score is a snapshot, not a look-ahead guard —
    the triggers/targets use only window-internal data)."""
    rows = await conn.fetch(
        f"""
        SELECT DISTINCT ON (industry_id, benchmark_industry_id)
               industry_id,
               benchmark_industry_id AS pair_industry_id,
               opposite_score_ma60_60d::float8 AS pair_score,
               offset_sub_corr_ma60_60d::float8 AS pair_corr,
               start_date AS score_date
        FROM analysis_composites.industry_corr_benchmark_offsets
        WHERE pool_size = '{OPP_PAIR_POOL_SIZE}'
          AND benchmark_code = '{OPP_PAIR_BENCHMARK}'
          AND opposite_score_ma60_60d IS NOT NULL
        ORDER BY industry_id, benchmark_industry_id, start_date DESC
        """
    )
    df = pd.DataFrame(
        rec_cols(rows),
        columns=[
            "industry_id", "pair_industry_id",
            "pair_score", "pair_corr", "score_date",
        ],
    )
    return df


async def fetch_industry_closes(
    conn, industries: list[str], since: date,
) -> pd.DataFrame:
    """Industry composite closes (mean_close, pool slice per the opp_pair
    config) as a long (code = industry_id, date, close) frame bounded to
    date >= ``since``, sorted by (code, date) — the build_grid input
    convention."""
    rows = await conn.fetch(
        f"""
        SELECT industry_id AS code,
               extract(epoch from date)::float8 AS date,
               mean_close::float8 AS close
        FROM stats.industry_basic_stats
        WHERE pool_size = '{OPP_PAIR_POOL_SIZE}'
          AND mean_close IS NOT NULL
          AND industry_id = ANY($1::text[])
          AND date >= $2
        ORDER BY industry_id, date ASC
        """,
        sorted(industries), since,
    )
    if not rows:
        return pd.DataFrame(columns=_INDUSTRY_CLOSE_COLUMNS)
    df = pd.DataFrame(rec_cols(rows), columns=_INDUSTRY_CLOSE_COLUMNS)
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    return df


async def fetch_industry_first_dates(
    conn, industries: list[str],
) -> dict[str, date]:
    """Per-industry TRUE first composite-close date (min(date) on the
    same rows fetch_industry_closes admits) — the full-window gate's
    first_ord input."""
    rows = await conn.fetch(
        f"""
        SELECT industry_id AS code, min(date) AS first_date
        FROM stats.industry_basic_stats
        WHERE pool_size = '{OPP_PAIR_POOL_SIZE}'
          AND mean_close IS NOT NULL
          AND industry_id = ANY($1::text[])
        GROUP BY industry_id
        """,
        sorted(industries),
    )
    return {r["code"]: r["first_date"] for r in rows}


async def fetch_benchmark_closes(
    conn, code: str, since: date,
) -> pd.Series:
    """The offset benchmark's close Series (date-indexed, ascending,
    bounded to date >= ``since``)."""
    rows = await conn.fetch(
        """
        SELECT extract(epoch from date)::float8 AS date,
               close::float8 AS close
        FROM stats.index_basic_stats
        WHERE code = $1 AND close IS NOT NULL AND date >= $2
        ORDER BY date ASC
        """,
        code, since,
    )
    if not rows:
        return pd.Series(dtype="float64")
    df = pd.DataFrame(rec_cols(rows), columns=["date", "close"])
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    return df.set_index("date")["close"].sort_index()


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
