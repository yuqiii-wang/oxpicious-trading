"""Internal trading-amount-ratios step for analyze.mov_ave_spread.

Liquidity-impact ratios for ETF + Index + Stock: 10 capital-per-movement
ratio columns, one row per (sec_type, code, date) in
analysis.mov_ave_trading_amt_ratios.

=======================================================================
  FINANCIAL SEMANTICS
=======================================================================

Every column has the general form

    R = (trading amount in MILLIONS of yuan) / (price movement)

which is the RECIPROCAL of the Amihud (2002) illiquidity measure
(ILLIQ = |price change| / dollar volume). Interpretation:

    HIGH R  -> deep market: much capital was absorbed per unit of
               price movement (thick order book).
    LOW  R  -> thin market: small capital moved the price a lot
               (high price impact per yuan traded).

The daily price move decomposes into three measurable legs, and each
leg gets its own ratio family:

    close[t] - close[t-1]   net daily move      (= price_slope)
    open[t]  - close[t-1]   overnight gap       (jump between sessions)
    high[t]  - low[t]       intraday range      (movement envelope)

1. SLOPE RATIOS (signed, close-to-close basis) — 6 columns:
     trading_amt_vs_price_slope_ratio
         = (trading_amount / 1M) / price_slope
       today's capital per unit of NET daily price change.
     trading_amt_ma{W}_vs_price_ma{W}_slope_ratio
         = (trading_amt_ma{W} / 1M) / ma{W}_slope
       matching-timescale (W-day average capital per unit of the W-day
       price-MA daily step) — trend liquidity: the capital behind the
       average trend step, smoothed of single-day noise.

2. RANGE RATIO (unsigned — intraday depth gauge):
     trading_amt_vs_high_low_ratio
         = (trading_amount / 1M) / (high - low)
       capital per unit of intraday range. Range-based liquidity in the
       Parkinson-volatility spirit: high turnover + narrow range = deep
       book; low turnover + wide range = a volatile / thin session.

3. OVERNIGHT-GAP RATIO (signed — gap-day liquidity gauge):
     trading_amt_vs_overnight_gap_ratio
         = (trading_amount / 1M) / (open[t] - close[t-1])
       capital traded on a day that gapped, per unit of gap. A high
       ratio on a big gap = the gap attracted flow (breakout
       confirmation); a low ratio = a thin overnight market. NOTE: the
       original draft name "gap betw prev close today close" would be
       literally IDENTICAL to price_slope (close[t] - close[t-1]) —
       already covered by the slope ratios — so the column implements
       the standard trading "gap" instead: where today's session OPENS
       relative to yesterday's close.

4. MA5 VERSIONS of 2 & 3 (matching timescale):
     trading_amt_ma5_vs_high_low_ma5_ratio
         = (trading_amt_ma5 / 1M) / MA5(high - low)
     trading_amt_ma5_vs_overnight_gap_ma5_ratio
         = (trading_amt_ma5 / 1M) / MA5(open[t] - close[t-1])
       5-day average capital per unit of 5-day average range / gap.

Conventions (shared by all 10 columns):
  - Sign: turnover >= 0, so sign(R) = sign(movement). A negative
    slope/gap ratio means the move was downward. Range ratios are
    always positive.
  - Zero-movement guard: a 0 denominator is auto-set to 1.0, so the
    stored value equals the capital in millions — a pragmatic floor
    for flat / limit-locked days (A-share 一字板), NOT a true ratio.
  - Scale: ratios are in PRICE units (not scale-free). Absolute values
    are comparable across time within a code, and cross-sectionally
    only among similarly-priced instruments. Typical magnitudes:
    ~10^2 (small stocks) .. ~10^5 (broad indices / liquid ETFs; e.g.
    5e11-yuan index turnover with a 30-point move ≈ 16,667).
  - NUMERIC(10,4): |value| must stay < 10^6; the sanitize step nulls
    anything at/beyond that bound.

Source: the same source DataFrame already loaded by the parent
mov_ave_spread.fetch_source_data — reuses the same DataFrame, no second
DB round-trip. The trading_amt_ma{*} and ma{W}_slope / price_slope
columns are pre-computed by the parent's helper functions; only the
daily range / overnight gap and their MA5s are computed here.

This module is an INTERNAL step of analyze.mov_ave_spread — invoked
from __main__.py right after the trading-amt step, reusing the same DB
connection + source DataFrame.

Incremental mode (``force=False``):
  Only dates present in source identity tables but NOT yet in
  analysis.mov_ave_trading_amt_ratios are (re)computed and upserted.
  The missing-date check is PER-sec_type.

Force mode (``force=True``):
  Truncate analysis.mov_ave_trading_amt_ratios, then recompute and
  insert all rows for the active universe.
"""
from __future__ import annotations

import time
from typing import Optional, Set

import numpy as np
import pandas as pd

from _common.build_commons import (
    truncate_table_async,
    find_missing_analysis_dates,
)
from _common.df_utils import column_subset, grouped_rolling_agg
from analyze._common import (
    build_and_insert_chunked,
    upsert_analysis_identity,
    sanitize_for_db_insert,
)
from analyze.mov_ave_spread.config import (
    SEC_TYPE_IDENTITY_TABLE,
    TRADING_AMT_HIGH_LOW_RATIO_COLUMN,
    TRADING_AMT_MA5_HIGH_LOW_RATIO_COLUMN,
    TRADING_AMT_MA5_OVERNIGHT_GAP_RATIO_COLUMN,
    TRADING_AMT_MA_COLUMNS,
    TRADING_AMT_OVERNIGHT_GAP_RATIO_COLUMN,
    TRADING_AMT_PRICE_SLOPE_SOURCE_COLUMNS,
    TRADING_AMT_RATIOS_ANALYSIS_NAME,
    TRADING_AMT_RATIOS_COLUMNS,
    TRADING_AMT_RATIOS_DESCRIPTION,
    TRADING_AMT_RATIOS_MAX_ABS,
    TRADING_AMT_RATIOS_TABLE,
    TRADING_AMT_SLOPE_VS_PRICE_RATIO_COLUMNS,
)
from analyze.mov_ave_spread.helpers import null_if_overflow_counted

# Transient (not persisted) intermediate columns: daily intraday range,
# daily overnight gap, and their 5-day MAs. Used as ratio denominators.
_HIGH_LOW_RANGE_TMP = "_high_low_range"
_OVERNIGHT_GAP_TMP = "_overnight_gap"
_HIGH_LOW_MA5_TMP = "_high_low_ma5"
_OVERNIGHT_GAP_MA5_TMP = "_overnight_gap_ma5"

# Window for the MA5-timescale range / gap ratio denominators.
_RATIOS_MA_WINDOW = 5


# ---------------------------------------------------------------------------
#  Compute helpers (pure pandas / cuDF)
# ---------------------------------------------------------------------------

def _safe_divide_millions(df: pd.DataFrame, numerator_col: str,
                          denominator_col: str, out_col: str) -> None:
    """Safe division with million-unit conversion and zero-denominator guard.

    result = (numerator / 1_000_000) / denominator;
    denominator=0 auto-set to 1.0 (the stored value then equals the
    capital in millions — the pragmatic floor for flat / limit-locked
    days). NULL when either input is NULL or the result is not finite.
    """
    num = pd.to_numeric(df[numerator_col], errors="coerce")
    den = pd.to_numeric(df[denominator_col], errors="coerce")
    den = den.where(den != 0, 1.0)
    result = (num / 1_000_000) / den
    bad = num.isna() | den.isna() | ~np.isfinite(result)
    df[out_col] = result.where(~bad)


def compute_trading_amt_slope_vs_price_ratios(df: pd.DataFrame) -> pd.DataFrame:
    """Add 6 liquidity-impact ratio columns (trading-amount / price-slope).

    Each column = (trading_amount_in_millions / price_slope):
      col[0] = (trading_amount / 1M) / price_slope             (raw vs raw)
      col[1] = (trading_amt_ma5 / 1M) / ma5_slope
      col[2] = (trading_amt_ma20 / 1M) / ma20_slope
      col[3] = (trading_amt_ma60 / 1M) / ma60_slope
      col[4] = (trading_amt_ma120 / 1M) / ma120_slope
      col[5] = (trading_amt_ma255 / 1M) / ma255_slope

    Trading amount is divided by 1,000,000 to express capital in millions.
    Matching-timescale: numerator and denominator use the same window.
    Denominator=0 is auto-set to 1.0 to avoid division-by-zero.

    Interpretation: how many millions of capital accompany one unit of
    price movement — the reciprocal of the Amihud illiquidity measure
    (higher = deeper market).

    NULL when numerator or denominator is NULL.
    NUMERIC(10,4).
    """
    if df.empty:
        for col in TRADING_AMT_SLOPE_VS_PRICE_RATIO_COLUMNS:
            df[col] = pd.Series(dtype="float64")
        return df

    # col[0]: (trading_amount / 1M) / price_slope
    _safe_divide_millions(
        df,
        numerator_col="trading_amount",
        denominator_col="price_slope",
        out_col=TRADING_AMT_SLOPE_VS_PRICE_RATIO_COLUMNS[0],
    )

    # col[1..5]: (trading_amt_maW / 1M) / maW_slope, matching timescale
    ma_numerator_cols = list(TRADING_AMT_MA_COLUMNS)
    price_slope_cols = list(TRADING_AMT_PRICE_SLOPE_SOURCE_COLUMNS[1:])
    for i in range(len(ma_numerator_cols)):
        _safe_divide_millions(
            df,
            numerator_col=ma_numerator_cols[i],
            denominator_col=price_slope_cols[i],
            out_col=TRADING_AMT_SLOPE_VS_PRICE_RATIO_COLUMNS[i + 1],
        )

    return df


def compute_range_and_gap_mas(df: pd.DataFrame) -> pd.DataFrame:
    """Add the transient range / overnight-gap columns and their MA5s.

    Adds (transient, not persisted):
      _high_low_range[t]   = high[t] - low[t]
        The intraday movement envelope. Always >= 0; 0 on flat /
        limit-locked days; NaN when high or low is missing.
      _overnight_gap[t]    = open[t] - price[t-1]
        The overnight gap: where today's session opens relative to
        yesterday's close (adjusted space for ETFs — both open and price
        come from the same adjusted source columns). NaN on the first
        date of each code or when open / prev close is missing.
      _high_low_ma5        = 5-day rolling mean of _high_low_range
      _overnight_gap_ma5   = 5-day rolling mean of _overnight_gap
        NULL until 5 consecutive non-NaN observations (min_periods=5 —
        a missing OHLC row yields NaN rather than a zero movement,
        unlike the trading-amount MAs which treat NULL turnover as 0).

    Computed per (sec_type, code) ordered by date via the shared
    ``grouped_rolling_agg`` helper (cuDF-compatible).
    """
    df = df.sort_values(["sec_type", "code", "date"]).reset_index(drop=True)
    grp_keys = ["sec_type", "code"]

    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    df[_HIGH_LOW_RANGE_TMP] = high - low

    open_ = pd.to_numeric(df["open"], errors="coerce")
    prev_close = df.groupby(grp_keys, sort=False)["price"].shift(1)
    prev_close = pd.to_numeric(prev_close, errors="coerce")
    df[_OVERNIGHT_GAP_TMP] = open_ - prev_close

    df[_HIGH_LOW_MA5_TMP] = grouped_rolling_agg(
        df, grp_keys, _HIGH_LOW_RANGE_TMP, window=_RATIOS_MA_WINDOW,
        min_periods=_RATIOS_MA_WINDOW, agg="mean",
    )
    df[_OVERNIGHT_GAP_MA5_TMP] = grouped_rolling_agg(
        df, grp_keys, _OVERNIGHT_GAP_TMP, window=_RATIOS_MA_WINDOW,
        min_periods=_RATIOS_MA_WINDOW, agg="mean",
    )

    return df


def compute_trading_amt_high_low_and_gap_ratios(
    df: pd.DataFrame,
) -> pd.DataFrame:
    """Add the 4 range / overnight-gap ratio columns.

      trading_amt_vs_high_low_ratio
          = (trading_amount / 1M) / _high_low_range
      trading_amt_vs_overnight_gap_ratio
          = (trading_amount / 1M) / _overnight_gap
      trading_amt_ma5_vs_high_low_ma5_ratio
          = (trading_amt_ma5 / 1M) / _high_low_ma5
      trading_amt_ma5_vs_overnight_gap_ma5_ratio
          = (trading_amt_ma5 / 1M) / _overnight_gap_ma5

    See the module docstring for the financial semantics. Denominator=0
    auto-set to 1.0 (flat / limit-locked days). NULL when any input is
    NULL. NUMERIC(10,4).

    Must be called AFTER compute_range_and_gap_mas (reads its transient
    columns).
    """
    if df.empty:
        for col in (
            TRADING_AMT_HIGH_LOW_RATIO_COLUMN,
            TRADING_AMT_OVERNIGHT_GAP_RATIO_COLUMN,
            TRADING_AMT_MA5_HIGH_LOW_RATIO_COLUMN,
            TRADING_AMT_MA5_OVERNIGHT_GAP_RATIO_COLUMN,
        ):
            df[col] = pd.Series(dtype="float64")
        return df

    _safe_divide_millions(
        df,
        numerator_col="trading_amount",
        denominator_col=_HIGH_LOW_RANGE_TMP,
        out_col=TRADING_AMT_HIGH_LOW_RATIO_COLUMN,
    )
    _safe_divide_millions(
        df,
        numerator_col="trading_amount",
        denominator_col=_OVERNIGHT_GAP_TMP,
        out_col=TRADING_AMT_OVERNIGHT_GAP_RATIO_COLUMN,
    )
    _safe_divide_millions(
        df,
        numerator_col="trading_amt_ma5",
        denominator_col=_HIGH_LOW_MA5_TMP,
        out_col=TRADING_AMT_MA5_HIGH_LOW_RATIO_COLUMN,
    )
    _safe_divide_millions(
        df,
        numerator_col="trading_amt_ma5",
        denominator_col=_OVERNIGHT_GAP_MA5_TMP,
        out_col=TRADING_AMT_MA5_OVERNIGHT_GAP_RATIO_COLUMN,
    )

    return df


def sanitize_trading_amt_ratios_rows(df: pd.DataFrame) -> list[dict]:
    """Select the mov_ave_trading_amt_ratios columns, apply overflow
    guards, and sanitize for asyncpg bulk insert (NaN/inf -> None +
    to_dict).

    All 10 ratio columns are NUMERIC(10,4) — |value| < 10^6 after
    rounding to 4 decimal places (TRADING_AMT_RATIOS_MAX_ABS). Typical
    magnitudes are 10^2..10^5; only pathological combos (ultra-high
    turnover with a sub-tick price move) reach the bound and are nulled.
    """
    if df.empty:
        return []

    out_cols = list(TRADING_AMT_RATIOS_COLUMNS)
    out = df[out_cols].copy()

    non_numeric = ("sec_type", "code", "date")
    numeric_cols = [c for c in out_cols if c not in non_numeric]

    nulled = {}
    for c in numeric_cols:
        clean, n = null_if_overflow_counted(
            out[c], max_abs=TRADING_AMT_RATIOS_MAX_ABS, scale=4,
        )
        out[c] = clean
        if n > 0:
            nulled[c] = n
    if nulled:
        total = sum(nulled.values())
        per = ", ".join(f"{c}={n}" for c, n in nulled.items())
        print(f"    -> overflow-guard nulled {total:,} value(s) across "
              f"{len(nulled)} column(s): {per}", flush=True)

    return sanitize_for_db_insert(out, numeric_cols=numeric_cols)


# ---------------------------------------------------------------------------
#  Pipeline (internal step — invoked from mov_ave_spread.__main__)
# ---------------------------------------------------------------------------

async def run_trading_amt_ratios(
    conn,
    df: pd.DataFrame,
    *,
    force: bool = False,
    pool=None,
    max_concurrent: int = 20,
    sec_type: str | None = None,
    code_filter: str | None = None,
) -> None:
    """Run the trading-amount-ratios pipeline against the source data
    already loaded by the parent mov_ave_spread.

    Reuses the caller's DB connection and source DataFrame (the
    ``trading_amount``, ``trading_amt_ma{*}``, ``price_slope``, and
    ``ma{W}_slope`` columns are reused — no second DB fetch). The
    DataFrame must contain the FULL per-code history so the MA5 range /
    gap rolling computations have enough lookback rows.

    Pipeline
      1. Determine target dates (per-sec_type) by checking missing dates
         in analysis.mov_ave_trading_amt_ratios against source identity
         tables. In force mode, truncate the table instead.
      2. Compute the 6 slope ratios over the FULL per-code history.
      3. Compute the daily range / overnight gap + their MA5s and the 4
         range / gap ratios over the FULL per-code history, then filter
         to target_dates.
      4. Upsert into analysis.mov_ave_trading_amt_ratios (chunked by
         date) and register in analysis.analysis_identity.

    Args:
      conn: asyncpg connection (reused from parent).
      df: source DataFrame with at least columns [sec_type, code, date,
          price, open, high, low, trading_amount, trading_amt_ma{W},
          price_slope, ma{W}_slope]. Must be the FULL per-code history.
      force: when True, truncate analysis.mov_ave_trading_amt_ratios
             first and recompute all rows.
      pool: optional connection pool for parallel upsert chunks.
      sec_type: when provided, process only this sec_type (parent loop
                passes one sec_type at a time to bound memory). When
                None, infers sec_types from the DataFrame.
    """
    t0 = time.time()
    print("\n" + "=" * 78, flush=True)
    print("  MOV_AVE_TRADING_AMT_RATIOS (internal step of mov_ave_spread)",
          flush=True)
    print("=" * 78, flush=True)

    needed_cols = list(dict.fromkeys(
        ["sec_type", "code", "date", "price", "open", "high", "low",
         "trading_amount"]
        + list(TRADING_AMT_MA_COLUMNS)
        + list(TRADING_AMT_PRICE_SLOPE_SOURCE_COLUMNS)
    ))
    available = column_subset(df, needed_cols)
    ta_df = df[available].copy()

    if ta_df.empty:
        print("    -> no source data; skipping trading-amt-ratios step.",
              flush=True)
        return

    if sec_type is not None:
        sec_types = (sec_type,)
    else:
        sec_types = tuple(sorted(ta_df["sec_type"].unique()))

    # ---- Step 0: determine target dates (per-sec_type) --------------
    if code_filter is not None:
        # Single-code mode (--code): the caller already DELETEd this
        # code's rows from the table, so compute ALL dates for this code
        # and bypass the per-sec_type skip-filter (sec_types=() at the
        # insert below keeps every row — dates covered by OTHER codes
        # would otherwise mask this code's gaps).
        print("    mode: SINGLE-CODE (full recompute for this code)",
              flush=True)
        target_dates_union: Optional[Set] = None
    elif force:
        print("    mode: FORCE (full recompute)", flush=True)
        print("\n[t0/4] Force mode: truncating mov_ave_trading_amt_ratios...",
              flush=True)
        await truncate_table_async(conn, TRADING_AMT_RATIOS_TABLE)
        target_dates_union: Optional[Set] = None
        print("    -> truncated; will recompute all rows", flush=True)
    else:
        print("    mode: incremental (missing dates only)", flush=True)
        print("\n[t0/4] Detecting missing dates PER-sec_type "
              "(etf_identity vs trading_amt_ratios[etf], etc.)...",
              flush=True)
        target_dates_per_st: dict = {}
        for st in sec_types:
            td_st = await find_missing_analysis_dates(
                conn, TRADING_AMT_RATIOS_TABLE,
                [SEC_TYPE_IDENTITY_TABLE[st]], sec_type=st,
            )
            target_dates_per_st[st] = td_st
            print(f"    -> {st}: {len(td_st)} missing dates", flush=True)
        target_dates_union = set()
        for s in target_dates_per_st.values():
            target_dates_union |= s
        print(f"    -> union across sec_types: "
              f"{len(target_dates_union)} dates to (re)compute",
              flush=True)
        if not target_dates_union:
            print("    -> DB is up to date; nothing to do.", flush=True)
            return

    # ---- Step 1: compute the 6 slope ratios over full history -------
    print("\n[t1/4] Computing 6 slope-ratio columns "
          "((trading_amt / 1M) / price_slope, matching-timescale)...",
          flush=True)
    ta_df = compute_trading_amt_slope_vs_price_ratios(ta_df)

    # ---- Step 2: compute range / gap MAs + 4 ratio columns ----------
    print("[t2/4] Computing daily range / overnight gap + MA5s and 4 "
          "range / gap ratio columns "
          "((ta or ta_ma5 / 1M) / (range or gap, matching timescale))...",
          flush=True)
    ta_df = compute_range_and_gap_mas(ta_df)
    ta_df = compute_trading_amt_high_low_and_gap_ratios(ta_df)

    if target_dates_union is not None and len(target_dates_union) > 0:
        n_before = len(ta_df)
        ta_df = ta_df[ta_df["date"].isin(target_dates_union)].reset_index(drop=True)
        print(f"    -> incremental filter: {len(ta_df):,} of {n_before:,} "
              f"rows are in target_dates_union", flush=True)

    if ta_df.empty:
        print("    -> no rows to upsert; skipping trading-amt-ratios "
              "upsert.", flush=True)
        return

    # ---- Step 3: build + insert (chunked by date) -------------------
    print(f"\n[t3/4] Building + inserting {len(ta_df):,} "
          f"mov_ave_trading_amt_ratios rows in date-bounded chunks "
          f"({'COPY' if force else 'upsert'} per chunk)...", flush=True)
    n = await build_and_insert_chunked(
        conn, pool, ta_df,
        sanitize_trading_amt_ratios_rows,
        table_name=TRADING_AMT_RATIOS_TABLE,
        key_columns=["sec_type", "code", "date"],
        force=force,
        sec_types=() if code_filter is not None else sec_types,
        max_concurrent=max_concurrent,
        label="mov_ave_trading_amt_ratios",
    )
    del ta_df
    print(f"    -> inserted {n:,} rows", flush=True)

    # ---- Step 4: register in analysis_identity ----------------------
    print(f"\n[t4/4] Upserting analysis.analysis_identity registry...",
          flush=True)
    await upsert_analysis_identity(
        conn,
        name=TRADING_AMT_RATIOS_ANALYSIS_NAME,
        detail_name="mov_ave_trading_amt_ratios",
        description=TRADING_AMT_RATIOS_DESCRIPTION,
    )

    print(f"\n  mov_ave_trading_amt_ratios wall time: "
          f"{time.time() - t0:.1f}s", flush=True)
