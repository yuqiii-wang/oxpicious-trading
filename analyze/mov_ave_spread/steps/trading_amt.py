"""Internal trading-amount step for analyze.mov_ave_spread.

Trading-amount metrics (5 trading-amount MA columns + 5 trading-amount
Bollinger band σ columns + 5 market-share MA columns + 5 MA slope columns
+ 1 raw slope column + 5 market-share-vs-MA gap columns) for ETF + Index
+ Stock. One row per (sec_type, code, date) in analysis.mov_ave_trading_amt.

Source: the same source DataFrame already loaded by the parent
mov_ave_spread.fetch_source_data — reuses the same DataFrame, no second
DB round-trip. The trading_amt_ma{*}, trading_amt_market_share_ma{*},
trading_amt_ma{*}_slope, and trading_amt_market_share_vs_ma{*} columns
are already pre-computed by the parent's helper functions and carried in
the source DataFrame.

This module computes NEW columns on top of those:
  - Rolling population σ (ddof=0) of trading_amt_maW over W days
    (Bollinger band width for each MA window)
  - trading_amt_slope — fractional daily change of raw trading_amount

The liquidity-impact RATIO columns (trading_amt_vs_price_slope_ratio
etc.) are computed by the companion internal step trading_amt_ratios.py
and written to analysis.mov_ave_trading_amt_ratios.

This module is an INTERNAL step of analyze.mov_ave_spread — it is invoked
from __main__.py after the detail table has been
repopulated, reusing the same DB connection + source DataFrame.

Incremental mode (``force=False``):
  Only dates present in source identity tables but NOT yet in
  analysis.mov_ave_trading_amt are (re)computed and upserted.
  The missing-date check is PER-sec_type.

Force mode (``force=True``):
  Truncate analysis.mov_ave_trading_amt, then recompute and
  insert all rows for the active universe.

GPU acceleration: the rolling std computations use the shared
``grouped_rolling_agg`` helper, which routes to cuDF when the row count
exceeds the rolling_std breakeven (~100K rows conservative).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import grouped_rolling_agg
from analyze._common import sanitize_for_db_insert
from analyze.mov_ave_spread._common import (
    null_if_overflow_counted,
    run_table_step,
)
from analyze.mov_ave_spread.config import (
    NUMERIC_WIDE_MAX_ABS,
    TRADING_AMT_ANALYSIS_NAME,
    TRADING_AMT_COLUMNS,
    TRADING_AMT_DESCRIPTION,
    TRADING_AMT_MA_COLUMNS,
    TRADING_AMT_MA_SLOPE_COLUMNS,
    TRADING_AMT_MARKET_SHARE_MA_COLUMNS,
    TRADING_AMT_MARKET_SHARE_VS_MA_COLUMNS,
    TRADING_AMT_RAW_SLOPE_COLUMN,
    TRADING_AMT_STD_COLUMNS,
    TRADING_AMT_TABLE,
)
from analyze.mov_ave_spread._common import null_if_overflow_counted

import logging
logger = logging.getLogger(__name__)

TRADING_AMT_STD_WINDOWS = (5, 20, 60, 120, 255)


# ---------------------------------------------------------------------------
#  Compute helpers (pure pandas / cuDF)
# ---------------------------------------------------------------------------

def compute_trading_amt_stds(df: pd.DataFrame) -> pd.DataFrame:
    """Add rolling population σ (ddof=0) of trading_amt_maW over W days.

    For each window W in TRADING_AMT_STD_WINDOWS:
      - trading_amt_stdW = rolling population σ of trading_amt_maW
        over the past W days (min_periods=W so NULL until full window).

    Uses the shared ``grouped_rolling_agg`` helper for GPU acceleration.
    The σ columns are used for Bollinger-style envelopes (MA ± k×σ)
    around each trading-amount MA line.
    """
    if df.empty:
        for col in TRADING_AMT_STD_COLUMNS:
            df[col] = pd.Series(dtype="float64")
        return df

    df = df.sort_values(["sec_type", "code", "date"]).reset_index(drop=True)
    grp_keys = ["sec_type", "code"]

    for i, w in enumerate(TRADING_AMT_STD_WINDOWS):
        std_col = TRADING_AMT_STD_COLUMNS[i]
        ma_col = TRADING_AMT_MA_COLUMNS[i]

        df[std_col] = grouped_rolling_agg(
            df, grp_keys, ma_col, window=w,
            min_periods=w, agg="std", ddof=0, sort=False,
        )

    return df


def compute_trading_amt_slope(df: pd.DataFrame) -> pd.DataFrame:
    """Add trading_amt_slope — fractional daily change of raw trading_amount.

    trading_amt_slope[t] = (ta[t] - ta[t-1]) / ta[t-1]

    Same formula as trading_amt_ma{W}_slope but on the raw value.
    NUMERIC(10,4) — typical |slope| < 0.5 for broad indices.

    NULL on first date of each code or when ta[t]/ta[t-1] is NULL or
    ta[t-1] <= 0.
    """
    if df.empty:
        df[TRADING_AMT_RAW_SLOPE_COLUMN] = pd.Series(dtype="float64")
        return df

    df = df.sort_values(["sec_type", "code", "date"]).reset_index(drop=True)
    grp_keys = ["sec_type", "code"]

    cur = pd.to_numeric(df["trading_amount"], errors="coerce")
    prev = df.groupby(grp_keys, sort=False)["trading_amount"].shift(1)
    prev = pd.to_numeric(prev, errors="coerce")
    slope = (cur - prev) / prev
    bad = cur.isna() | prev.isna() | (prev <= 0) | ~np.isfinite(slope)
    df[TRADING_AMT_RAW_SLOPE_COLUMN] = slope.where(~bad)

    return df


def sanitize_trading_amt_rows(df: pd.DataFrame) -> list[dict]:
    """Select the mov_ave_trading_amt columns, apply overflow guards,
    and sanitize for asyncpg bulk upsert (NaN/inf -> None + to_dict).

    Wide columns (NUMERIC(24,4)): trading_amt_ma* and trading_amt_std*
    columns (yuan values up to 10^20).
    Narrow columns (NUMERIC(10,4)): market-share MAs, slopes, and gaps
    (ratios, typical |value| < 10).
    """
    if df.empty:
        return []

    out_cols = list(TRADING_AMT_COLUMNS)
    out = df[out_cols].copy()

    non_numeric = ("sec_type", "code", "date")
    numeric_cols = [c for c in out_cols if c not in non_numeric]

    wide_cols = set(TRADING_AMT_MA_COLUMNS) | set(TRADING_AMT_STD_COLUMNS)

    nulled = {}
    for c in numeric_cols:
        if c in wide_cols:
            clean, n = null_if_overflow_counted(
                out[c], max_abs=NUMERIC_WIDE_MAX_ABS, scale=4,
            )
        else:
            clean, n = null_if_overflow_counted(out[c], scale=4)
        out[c] = clean
        if n > 0:
            nulled[c] = n
    if nulled:
        total = sum(nulled.values())
        per = ", ".join(f"{c}={n}" for c, n in nulled.items())
        logger.info(f"    -> overflow-guard nulled {total:,} value(s) across "
              f"{len(nulled)} column(s): {per}")

    return sanitize_for_db_insert(out, numeric_cols=numeric_cols)


# ---------------------------------------------------------------------------
#  Pipeline (internal step — invoked from mov_ave_spread.__main__; the
#  skeleton lives in _common.steps.run_table_step)
# ---------------------------------------------------------------------------

def _compute_trading_amt_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Both compute phases in order: Bollinger σ columns, then the raw
    trading_amt_slope."""
    df = compute_trading_amt_stds(df)
    return compute_trading_amt_slope(df)


async def run_trading_amt(
    conn,
    df: pd.DataFrame,
    *,
    force: bool = False,
    pool=None,
    max_concurrent: int = 20,
    sec_type: str | None = None,
    code_filter: str | None = None,
) -> None:
    """Run the trading-amount pipeline against the source data already
    loaded by the parent mov_ave_spread.

    Reuses the caller's DB connection and source DataFrame (the
    ``trading_amount``, ``trading_amt_ma{*}``,
    ``trading_amt_market_share_ma{*}``, ``trading_amt_ma{*}_slope``, and
    ``trading_amt_market_share_vs_ma{*}`` columns are reused — no second
    DB fetch). The DataFrame must contain the FULL per-code history so
    rolling computations have enough lookback rows (up to 255).

    Pipeline (see _common.steps.run_table_step)
      0. Determine target dates (per-sec_type) by checking missing dates
         in analysis.mov_ave_trading_amt against source identity tables.
         In force mode, purge the table scope instead.
      1. Compute rolling population σ (Bollinger band widths) of each
         trading_amt_maW + the raw trading_amt_slope over the FULL
         per-code history, then filter to target_dates.
      2. Upsert into analysis.mov_ave_trading_amt (chunked by date).
      3. Upsert analysis.analysis_identity registry.
    """
    await run_table_step(
        conn, df,
        title="MOV_AVE_TRADING_AMT (internal step of mov_ave_spread)",
        tag="t",
        table=TRADING_AMT_TABLE,
        analysis_name=TRADING_AMT_ANALYSIS_NAME,
        detail_name="mov_ave_trading_amt",
        description=TRADING_AMT_DESCRIPTION,
        needed_cols=list(dict.fromkeys(
            ["sec_type", "code", "date", "trading_amount"]
            + list(TRADING_AMT_MA_COLUMNS)
            + list(TRADING_AMT_MARKET_SHARE_MA_COLUMNS)
            + list(TRADING_AMT_MA_SLOPE_COLUMNS)
            + list(TRADING_AMT_MARKET_SHARE_VS_MA_COLUMNS)
        )),
        compute_fn=_compute_trading_amt_columns,
        sanitize_fn=sanitize_trading_amt_rows,
        force=force,
        pool=pool,
        max_concurrent=max_concurrent,
        sec_type=sec_type,
        code_filter=code_filter,
    )
