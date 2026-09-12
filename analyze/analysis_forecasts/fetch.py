"""Async DB fetchers for analyze.analysis_forecasts.

Loads, per sec_type, the joined long-format input frame:

  price   — the same price convention as the parent mov_ave_spread
            analysis (ETF = COALESCE(etf_adjustment.adj_close, close);
            index / stock = *_basic_stats.close),
  ma_{W}  — from stats.{sec_type}_tech_stats (ma5/20/60/120/255),
  rsi_{W} — from analysis.mov_ave_rsi (Wilder RSI columns),
  gap_{W} — from analysis.mov_ave_rsi (N-day price-return columns),
  std_{W} — from analysis.mov_ave_spreads_detail (Bollinger sigma),
  pair_{W} — from analysis.mov_ave_spreads_detail (the EXISTING
            ma5_vs_ma{W} relative-MA spreads, W ∈ MOV_PAIRS_WINDOWS —
            the mov_pairs cross buckets' input; no new MA computation),
  ema_pair_{W} — from analysis.mov_ave_spreads_detail_ema (the
            EXISTING ema6_vs_ema{W} relative-EMA spreads, W ∈
            MOV_PAIRS_EMA_WINDOWS — the mov_pairs_ema buckets' input),
  trading_amount + rz_buy — the px_vol / margin_ratio family inputs
            (daily turnover + RONGZI margin buy; rz_buy is NULL for
            'index' — indices have no margin data, so the
            margin_ratio buckets never fire there),
  pe + dividend_yield — the pe_state / dividend_state family inputs
            (the two analysis.pe / analysis.dividends valuation tables;
            a NULL join means "no bucket" — invalid-PE days have no pe
            row and non-payers no dividend_yield row).

Plus the compact market-hype EPISODES list
(stats.mov_ave_market_hypes) used to build the per-(date, code)
hyped-date matrix.

All NUMERIC columns are cast to native float8 in SQL (Decimal objects
would poison the cudf.pandas fast path) and the date columns arrive as
epoch float8, materialized via epoch_col_to_dt64.
"""
from __future__ import annotations

import json
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
    IDENTITY_BUCKET_TABLES,
    MA_WINDOWS,
    MARGIN_RATIO_Z_MIN_PERIODS,
    MARGIN_RATIO_Z_WINDOW,
    MM_HORIZONS,
    MOV_PAIRS_EMA_WINDOWS,
    MOV_PAIRS_WINDOWS,
    OPP_PAIR_BENCHMARK,
    OPP_PAIR_POOL_SIZE,
    VAL_Z_MIN_PERIODS,
    VAL_Z_WINDOW,
    PX_VOL_AMT_METRIC,
    PX_VOL_K_SHARP,
    PX_VOL_K_SLOW_DN,
    PX_VOL_K_SLOW_UP,
    PX_VOL_LB_WINDOW,
    PX_VOL_SIGMA_FLOOR,
    PX_VOL_SIGMA_MIN_DAYS,
    PX_VOL_SIGMA_WINDOW,
    PX_VOL_SPEEDS,
    PX_VOL_VOL_STATES,
    PX_VOL_Z_HEAVY,
    PX_VOL_Z_SHRINK,
    RSI_WINDOWS,
    SEC_TYPE_IDENTITY_TABLE,
    TABLE_IDENTITIES,
)

import logging
logger = logging.getLogger(__name__)

# Output column order (matches the SELECT list below).
_COLUMNS = (
    ["code", "date", "price"]
    + [f"ma_{w}days" for w in MA_WINDOWS]
    + [f"rsi_{w}days" for w in RSI_WINDOWS]
    + [f"gap_{w}days" for w in GAP_WINDOWS]
    + [f"std_{w}days" for w in MA_WINDOWS]
    + [f"pair_{w}" for w in MOV_PAIRS_WINDOWS]
    + [f"ema_pair_{w}" for w in MOV_PAIRS_EMA_WINDOWS]
    + ["trading_amount", "rz_buy"]
    + ["pe", "dividend_yield"]
)

# Base table + price expression per sec_type (same price convention as
# analyze.mov_ave_spread: ETF uses the adjusted close when available).
_PRICE_SOURCE = {
    "index": (
        "stats.index_basic_stats b",
        "b.close",
    ),
    "etf": (
        "stats.etf_basic_stats b "
        "LEFT JOIN stats.etf_adjustment a "
        "ON a.code = b.code AND a.date = b.date",
        "COALESCE(a.adj_close, b.close)",
    ),
    "stock": (
        "stats.stock_basic_stats b",
        "b.close",
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
    logger.info(f"      pre-filter: {len(codes):,} {sec_type} codes have "
          f"data in the last {RECENT_TRADING_DAYS} trading days "
          f"(cutoff={cutoff.isoformat()})")
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

    base, price_expr = _PRICE_SOURCE[sec_type]
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
    pair_cols = ",\n       ".join(
        f"d.ma5_vs_ma{w}::float8 AS pair_{w}" for w in MOV_PAIRS_WINDOWS
    )
    ema_pair_cols = ",\n       ".join(
        f"e.ema6_vs_ema{w}::float8 AS ema_pair_{w}"
        for w in MOV_PAIRS_EMA_WINDOWS
    )
    sql = f"""
        SELECT b.code,
               extract(epoch from b.date)::float8 AS date,
               {price_expr}::float8 AS price,
               {ma_cols},
               {rsi_cols},
               {gap_cols},
               {std_cols},
               {pair_cols},
               {ema_pair_cols},
               {amt_expr}::float8 AS trading_amount,
               {rz_expr}::float8 AS rz_buy,
               pdv.pe::float8 AS pe,
               dyv.dividend_yield::float8 AS dividend_yield
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
        LEFT JOIN analysis.mov_ave_spreads_detail_ema e
               ON e.sec_type = '{sec_type}'
              AND e.code = b.code AND e.date = b.date
        LEFT JOIN analysis.pe pdv
               ON pdv.sec_type = '{sec_type}'
              AND pdv.code = b.code AND pdv.date = b.date
        LEFT JOIN analysis.dividends dyv
               ON dyv.sec_type = '{sec_type}'
              AND dyv.code = b.code AND dyv.date = b.date
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


def _forward_extreme(df: pd.DataFrame, n: int, *, high: bool) -> pd.Series:
    """Per-code extreme of ``price`` over the NEXT n rows (t, t+n] —
    max when ``high`` else min, NaN-skipping, NaN where the code has no
    forward rows left.

    Doubling levels F_1, F_2, F_4, ... (F_{2m} = F_{m} ⊕ shift(F_{m},
    -m), ⊕ = NaN-skipping max/min) with the exact window assembled from
    n's binary decomposition (each bit: one shift of the level column +
    one combine). Rows covering fewer than n forward rows (the code's
    tail) may hold a partial-window extreme — the caller masks them out
    via the endpoint-change validity, which is exactly "n forward rows
    exist".
    """
    neutral = -np.inf if high else np.inf
    combine = np.maximum if high else np.minimum

    def shift_col(col: str, periods: int) -> pd.Series:
        name = f"_pe_s{periods}"
        grouped_shift(df, ["code"], col, out_names=name, periods=periods,
                      sort=False)
        s = df[name].fillna(neutral)
        df.drop(columns=[name], inplace=True)
        return s

    # Doubling levels: F_1 = the next row, F_{2m} = F_m ⊕ F_m(t+m).
    # Levels live as (±inf-filled) df columns so they can be shifted;
    # shifting a filled column is safe — a neutral tail value shifted
    # into an earlier row is exactly the "block has no rows" semantics.
    levels: dict[int, pd.Series] = {}
    grouped_shift(df, ["code"], "price", out_names="_pe_lvl1", periods=-1,
                  sort=False)
    levels[1] = df["_pe_lvl1"].fillna(neutral)
    step = 1
    while step * 2 <= n:
        step *= 2
        levels[step] = combine(levels[step // 2], shift_col(
            f"_pe_lvl{step // 2}", -(step // 2)))
        df[f"_pe_lvl{step}"] = levels[step]

    # Assemble exactly n rows from the binary decomposition of n.
    res: pd.Series | None = None
    covered = 0
    for level in sorted(levels.keys(), reverse=True):
        if covered + level <= n:
            block = levels[level] if covered == 0 else shift_col(
                f"_pe_lvl{level}", -covered)
            res = block if res is None else combine(res, block)
            covered += level
    assert res is not None and covered == n
    df.drop(columns=[c for c in df.columns if c.startswith("_pe_lvl")],
            inplace=True)
    return res


def add_path_extremes(df: pd.DataFrame) -> pd.DataFrame:
    """Add path_high_{n}d / path_low_{n}d columns for n in MM_HORIZONS.

    The SIGNED extrema of the close within the n-row forward window
    (t, t+n], relative to the signal-day close: path_high_{n}d =
    max(price[t+1..t+n]) / price[t] - 1 (the window's highest close),
    path_low_{n}d = min(...) (the lowest close) — the within-period
    SWING that the endpoint next_change_{n}d (one point of that path)
    does not capture. Consumed by the swing-aware reversal event and
    the max_low_change_ratio swing ratio (wide.aggregate_horizons_sparse).

    Per code on its OWN trading-day sequence, so the window is exactly
    n rows whenever next_change_{n}d is finite — these columns are NULL
    on exactly the rows the endpoint change is (the short forward tail;
    the base SQL already drops price-less rows mid-sequence).
    """
    for n in MM_HORIZONS:
        fin = df[f"next_change_{n}d"].notna()
        for name, high in (("path_high", True), ("path_low", False)):
            ext = _forward_extreme(df, n, high=high)
            df[f"{name}_{n}d"] = ((ext / df["price"]) - 1.0).where(fin)
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
        px_z      — z-scored log trading-amount LEVEL: the vol state
                    is what the heavy/shrink labels claim — the day's
                    amount vs the code's OWN trailing-year amount
                    distribution —
                    z = (log(ta[t]) - μ[t-1]) / σ[t-1] with μ/σ the
                    rolling PX_VOL_SIGMA_WINDOW-row moments of
                    log(trading_amount) (min
                    PX_VOL_SIGMA_MIN_DAYS, ddof=1) SHIFTED 1 row.
                    NULL where any input is missing.
        amt_ratio — the classic 量比 (trading_amount /
                    mean(trading_amount, t-PX_VOL_LB_WINDOW..t-1)),
                    recorded as registry EVIDENCE only — the vol
                    state does NOT consume it (the 5-day-baseline
                    ratio z fired "heavy" on drought bounces: in a
                    declining-volume regime the base collapses, so a
                    below-level amount day scored ratio ≈ 1.7 →
                    z > 2).

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

    # --- 量能水平 z (trading-amount LEVEL z-score) -------------------------
    df["_log_ta"] = np.log(df["trading_amount"]).where(
        df["trading_amount"] > 0)
    mu = grouped_rolling_agg(
        df, "code", "_log_ta", PX_VOL_SIGMA_WINDOW,
        min_periods=PX_VOL_SIGMA_MIN_DAYS, agg="mean", sort=False,
    )
    sig = grouped_rolling_agg(
        df, "code", "_log_ta", PX_VOL_SIGMA_WINDOW,
        min_periods=PX_VOL_SIGMA_MIN_DAYS, agg="std", ddof=1, sort=False,
    )
    df["_lvl_mu"] = mu
    df["_lvl_sig"] = sig
    grouped_shift(df, ["code"], ["_lvl_mu", "_lvl_sig"],
                  out_names=["_lvl_mu_lag", "_lvl_sig_lag"],
                  periods=1, sort=False)
    lvl = df["_log_ta"]
    mu_l = df["_lvl_mu_lag"]
    sig_l = df["_lvl_sig_lag"]
    df["px_z"] = ((lvl - mu_l) / sig_l).where(
        lvl.notna() & mu_l.notna() & sig_l.notna() & (sig_l > 1e-12))

    # --- classic 量比 (evidence only — see the docstring) ------------------
    grouped_shift(df, ["code"], "trading_amount", out_names="_ta_prev",
                  periods=1, sort=False)
    df["_lb_base"] = grouped_rolling_agg(
        df, "code", "_ta_prev", PX_VOL_LB_WINDOW,
        min_periods=PX_VOL_LB_WINDOW, agg="mean", sort=False,
    )
    ta = df["trading_amount"]
    base = df["_lb_base"]
    df["amt_ratio"] = (ta / base).where(
        ta.notna() & base.notna() & (base > 1e-12))

    return df.drop(columns=[
        "_log_ta", "_lvl_mu", "_lvl_sig", "_lvl_mu_lag", "_lvl_sig_lag",
        "_ta_prev", "_lb_base",
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


def add_valuation_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add the pe_state / dividend_state families' per-day state inputs
    (no look-ahead).

    Columns added — one z per valuation series, both vs the code's OWN
    trailing moments (the margin_ratio convention; a slow valuation
    series needs ~1y of observations before its z is trusted):
        pe_z      — z = (pe[t] - μ[t-1]) / σ[t-1] with μ/σ the
                    rolling VAL_Z_WINDOW-row sample moments of
                    pe (min_periods VAL_Z_MIN_PERIODS non-NULL
                    observations) SHIFTED 1 row. NULL on invalid-PE
                    days (no-earnings / non-positive PE) — those days
                    form no PE bucket.
        div_z     — the same z on dividend_yield (all sec_types; NULL
                    for non-payers — only paying codes form dividend
                    buckets).
    NULL where the series is NULL or the history is short → the compute
    engines' NaN-compares-False masks never bucket those days.

    The caller must have sorted df by (code, date) (the SQL ORDER BY).
    All ops are grouped pandas (cudf.pandas-accelerated).
    """
    for src, out in (("pe", "pe_z"), ("dividend_yield", "div_z")):
        mu = grouped_rolling_agg(
            df, "code", src, VAL_Z_WINDOW,
            min_periods=VAL_Z_MIN_PERIODS, agg="mean", sort=False,
        )
        sig = grouped_rolling_agg(
            df, "code", src, VAL_Z_WINDOW,
            min_periods=VAL_Z_MIN_PERIODS, agg="std", ddof=1,
            sort=False,
        )
        df["_mu"], df["_sig"] = mu, sig
        grouped_shift(df, "code", ["_mu", "_sig"], ["_mu_l", "_sig_l"],
                      periods=1, sort=False)
        df[out] = ((df[src] - df["_mu_l"]) / df["_sig_l"]).where(
            df["_sig_l"] > 0)
        df = df.drop(columns=["_mu", "_sig", "_mu_l", "_sig_l"])

    return df


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
    inclusive, any min_checkin_period) from stats.mov_ave_market_hypes.
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
        FROM stats.mov_ave_market_hypes m
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


# ---------------------------------------------------------------------------
#  px_vol state registry (analysis.mov_ave_price_vs_amt) — the px_vol
#  family's DATE-LEVEL source of truth. The forecast / signal engines
#  consume the registry's categories instead of re-deriving them from
#  raw features, so every bucket audits against the recorded states.
# ---------------------------------------------------------------------------

_PVA_STATE_COLUMNS = [
    "code", "date", "px_speed", "vol_state", "px_t", "px_z",
]


async def fetch_price_vs_amt_states(
    conn,
    sec_type: str,
    codes: list[str],
    since: date,
) -> pd.DataFrame:
    """Fetch the sec_type's per-(code, date) Price × Amt state rows
    from analysis.mov_ave_price_vs_amt, bounded to date >= ``since``.

    One row per state-valid day (the registry stores EVERY day whose
    σ_ret and amount-level z legs are valid — the 5×3 bands are
    exhaustive).
    Returns a DataFrame with columns ``_PVA_STATE_COLUMNS`` (dates as
    datetime64[us]; px_speed / vol_state as the recorded names).
    """
    if not codes:
        return pd.DataFrame(columns=_PVA_STATE_COLUMNS)
    rows = await conn.fetch(
        """
        SELECT m.code,
               extract(epoch from m.date)::float8 AS date,
               m.px_speed, m.vol_state,
               m.px_t::float8, m.px_z::float8
        FROM analysis.mov_ave_price_vs_amt m
        WHERE m.sec_type = $1
          AND m.code = ANY($2::text[])
          AND m.date >= $3
        ORDER BY m.code, m.date ASC
        """,
        sec_type,
        sorted(codes),
        since,
    )
    if not rows:
        return pd.DataFrame(columns=_PVA_STATE_COLUMNS)
    df = pd.DataFrame(rec_cols(rows), columns=_PVA_STATE_COLUMNS)
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    return df


async def fetch_price_vs_amt_source(
    conn,
    sec_type: str,
    codes: list[str],
) -> pd.DataFrame:
    """Fetch the REGISTRY build's source frame — price + trading_amount
    per (code, date) with the SAME conventions as fetch_analysis_inputs
    (ETF price = COALESCE(adj_close, close); estimated closes excluded
    for etf/index; trading_amount from the sec_type's own source). The
    registry (analysis.mov_ave_price_vs_amt, built by
    analyze.mov_ave_spread.price_vs_amt) must classify on exactly the
    price series the forecast engine consumes, or the buckets would not
    audit 1:1 against the recorded states.

    Returns an unbounded FULL-history frame (the trailing σ/z windows
    need the code's whole past) with columns [sec_type, code, date,
    price, trading_amount], sorted by (code, date).
    """
    base, price_expr = _PRICE_SOURCE[sec_type]
    amt_join, amt_expr, _, est_filter = _AMT_SOURCE[sec_type]
    sql = f"""
        SELECT b.code,
               extract(epoch from b.date)::float8 AS date,
               {price_expr}::float8 AS price,
               {amt_expr}::float8 AS trading_amount
        FROM {base}
        {amt_join}
        WHERE b.code = ANY($1::text[])
          AND b.close IS NOT NULL
          {est_filter}
        ORDER BY b.code, b.date ASC
    """
    rows = await conn.fetch(sql, sorted(codes))
    columns = ["sec_type", "code", "date", "price", "trading_amount"]
    if not rows:
        return pd.DataFrame(columns=columns)
    df = pd.DataFrame(rec_cols(rows), columns=columns[1:])
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    df.insert(0, "sec_type", sec_type)
    return df


# ---------------------------------------------------------------------------
#  high_low_streaks family inputs (analysis.mov_ave_high_low_pct_streaks —
#  the mov_ave_spread analysis's own band-break excursion streak table,
#  the family's source of truth; the streaks are read directly, no
#  recomputation — run python -m analyze.mov_ave_spread first).
# ---------------------------------------------------------------------------

_STREAK_SOURCE_COLUMNS = [
    "code", "period", "pct_type", "start_date", "end_date",
    "day_count", "side", "band_high", "band_low",
]

# End-date price source per sec_type — the SAME price convention as the
# input frame (_PRICE_SOURCE re-aliased to the end-date join), because
# the streak side is read off the UNROUNDED close on end_date vs the
# stored (2dp) band: the exact test the streaks step used. Comparing the
# streak row's stored 2dp-rounded close instead is ambiguous for ~36%
# of ETF rows (small adjusted prices round into ties with the band).
_END_PRICE_SOURCE = {
    "index": ("stats.index_basic_stats eb", "eb.close"),
    "etf": (
        "stats.etf_basic_stats eb "
        "LEFT JOIN stats.etf_adjustment ea "
        "ON ea.code = eb.code AND ea.date = eb.date",
        "COALESCE(ea.adj_close, eb.close)",
    ),
    "stock": ("stats.stock_basic_stats eb", "eb.close"),
}


async def fetch_high_low_streaks(
    conn,
    sec_type: str,
    codes: list[str],
    since: date,
) -> pd.DataFrame:
    """Fetch the sec_type's band-break excursion streaks from
    analysis.mov_ave_high_low_pct_streaks, bounded to end_date >= ``since``
    (a streak whose end precedes the earliest window start can never
    anchor inside it), with each streak's SIDE derived in SQL.

    The streaks table has no side column; the END date is out-of-band by
    construction, so the side is read off the UNROUNDED close on
    end_date (the end-date price join) vs the end date's OWN month band
    in analysis.mov_ave_high_low_pct: 'top' when close > high_val (an
    above-band excursion), 'bottom' when close < low_val; a tie falls to
    the NEARER band. Streaks whose band row or end-date price row is
    missing (none in practice — both derive from the same base rows the
    streaks step joined) drop out of the inner joins.

    Returns a DataFrame with columns ``_STREAK_SOURCE_COLUMNS``
    (band_high / band_low = the end month band's high_val / low_val —
    the streak side's threshold in PRICE space, consumed by the
    signals family as signal_threshold; dates as datetime64[us]),
    sorted by (code, start_date, period, pct_type).
    """
    if not codes:
        return pd.DataFrame(columns=_STREAK_SOURCE_COLUMNS)
    end_base, end_price = _END_PRICE_SOURCE[sec_type]
    rows = await conn.fetch(
        f"""
        SELECT s.code,
               s.period, s.pct_type, s.day_count,
               extract(epoch from s.start_date)::float8 AS start_date,
               extract(epoch from s.end_date)::float8   AS end_date,
               b.high_val::float8 AS band_high,
               b.low_val::float8  AS band_low,
               CASE
                   WHEN ep.price > b.high_val THEN 'top'
                   WHEN ep.price < b.low_val  THEN 'bottom'
                   WHEN abs(ep.price - b.high_val)
                        <= abs(ep.price - b.low_val) THEN 'top'
                   ELSE 'bottom'
               END AS side
        FROM analysis.mov_ave_high_low_pct_streaks s
        JOIN analysis.mov_ave_high_low_pct b
             ON b.sec_type = s.sec_type AND b.code = s.code
            AND b.date_year_month = date_trunc('month', s.end_date)::date
            AND b.period = s.period AND b.pct_type = s.pct_type
        JOIN LATERAL (
            SELECT {end_price}::float8 AS price
            FROM {end_base}
            WHERE eb.code = s.code AND eb.date = s.end_date
              AND eb.close IS NOT NULL
            LIMIT 1
        ) ep ON TRUE
        WHERE s.sec_type = $1
          AND s.code = ANY($2::text[])
          AND s.end_date >= $3
        ORDER BY s.code, s.start_date, s.period, s.pct_type
        """,
        sec_type,
        sorted(codes),
        since,
    )
    if not rows:
        return pd.DataFrame(columns=_STREAK_SOURCE_COLUMNS)
    df = pd.DataFrame(rec_cols(rows), columns=_STREAK_SOURCE_COLUMNS)
    df["start_date"] = epoch_col_to_dt64(df["start_date"], index=df.index)
    df["end_date"] = epoch_col_to_dt64(df["end_date"], index=df.index)
    return df


async def assert_price_vs_amt_params(conn, sec_type: str) -> None:
    """Audit guard: the registry's recorded build parameters must match
    the engine's PX_VOL_* constants exactly — a mismatch means the
    registry was built with a different calibration than the
    consumption-side thresholds (rebuild the registry, or realign the
    constants, before trusting the buckets). ``amt_metric`` guards the
    vol leg's DEFINITION (a level-z registry mixed with ratio-z
    consumers would misclassify every bucket silently)."""
    rows = await conn.fetch(
        """
        SELECT DISTINCT sigma_window, lb_window, k_slow_up, k_slow_dn,
               k_sharp, z_heavy, z_shrink, sigma_floor, amt_metric
        FROM analysis.mov_ave_price_vs_amt
        WHERE sec_type = $1
        """,
        sec_type,
    )
    if not rows:
        return  # empty registry — the caller skips the px_vol stage
    expected = {
        "sigma_window": PX_VOL_SIGMA_WINDOW,
        "lb_window": PX_VOL_LB_WINDOW,
        "k_slow_up": float(PX_VOL_K_SLOW_UP),
        "k_slow_dn": float(PX_VOL_K_SLOW_DN),
        "k_sharp": float(PX_VOL_K_SHARP),
        "z_heavy": float(PX_VOL_Z_HEAVY),
        "z_shrink": float(PX_VOL_Z_SHRINK),
        "sigma_floor": float(PX_VOL_SIGMA_FLOOR),
        "amt_metric": PX_VOL_AMT_METRIC,
    }
    for r in rows:
        got = {k: float(r[k]) for k in expected if k != "amt_metric"}
        got["amt_metric"] = r["amt_metric"]
        if got != expected:
            raise ValueError(
                f"[{sec_type}] analysis.mov_ave_price_vs_amt recorded "
                f"build parameters {got} differ from the engine "
                f"constants {expected} — rebuild the registry "
                f"(python -m analyze.mov_ave_spread) or realign the "
                f"px_vol constants before running the forecast engines."
            )


# ---------------------------------------------------------------------------
#  Search by forecast_id (analysis_forecasts.forecast_identities — the
#  shared-PK registry: one row per bucket holding the identity columns
#  every motivation table repeats in its leading PK)
# ---------------------------------------------------------------------------

async def fetch_forecast_identity(
    conn,
    forecast_id: int,
) -> dict | None:
    """Resolve one forecast_id against the identities registry.

    Returns the identity row (forecast_id, sec_type, code, stat_month,
    bucket, streak_signal_days, lookback_period — code = the forecast
    subject: the security ticker, or the DROPPING industry_id for
    opp_pair rows) plus, when the bucket's motivation table
    is one of the known families, the full motivation row joined from it
    under the ``motivation`` key (dates arrive as ISO strings via the
    to_jsonb cast). None when the id is not registered.

    Backs ``python -m analyze.analysis_forecasts --search-forecast-id``
    (the bucket table name is validated against the config's known set
    before it is ever interpolated into SQL).
    """
    row = await conn.fetchrow(
        f"""
        SELECT forecast_id, sec_type, code, stat_month, bucket,
               streak_signal_days, lookback_period
        FROM {TABLE_IDENTITIES}
        WHERE forecast_id = $1
        """,
        forecast_id,
    )
    if row is None:
        return None
    ident = dict(row)
    table = IDENTITY_BUCKET_TABLES.get(ident["bucket"])
    if table is not None:
        motivation = await conn.fetchrow(
            f"""
            SELECT to_jsonb(m) AS motivation
            FROM {table} m
            WHERE m.forecast_id = $1
            """,
            forecast_id,
        )
        # asyncpg hands JSONB back as a str — decode to a dict.
        ident["motivation"] = (
            json.loads(motivation["motivation"])
            if motivation is not None else None
        )
    return ident
