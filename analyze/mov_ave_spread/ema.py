"""Internal EMA step for analyze.mov_ave_spread.

EMA-based spread detail (price_vs_ema{6,20,60,120,255} +
ema6_vs_ema{20,60,120,255} + ema{W}_slope + ema{W}_curvature +
std_{5,20,60,120,255}days) for ETF + Index + Stock. One row per
(sec_type, code, date) in analysis.mov_ave_spreads_detail_ema.

Source: stats.{etf,index,stock}_tech_stats.ema{6,20,60,120,255} (already
fetched by the parent mov_ave_spread.fetch_source_data — reuses the same
DataFrame, no second DB round-trip). The EMA slope + curvature columns
are also pre-computed by the parent (helpers.compute_ema_slopes_curvatures
via grouped_diff, cuDF-accelerated) and carried in the source DataFrame.
The std_*days columns (rolling population σ of price over W days, ddof=0)
are pre-computed by the parent's helpers.compute_rolling_stds and carried
in the source DataFrame so the EMA table is self-contained for Bollinger
rendering without a JOIN back to the SMA detail table. This step adds
the 9 vs (gap) columns and assembles the final wide row.

9 gap pairs (canonical order):
  5 Price-vs-EMA:  gap = (price - emaX) / emaX,  X ∈ {6,20,60,120,255}
  4 EMA6-vs-EMA:   gap = (ema6 - emaX) / emaX,   X ∈ {20,60,120,255}

5 EMA slope columns (1st derivative = group-diff per (sec_type, code)
ordered by date) + 5 EMA curvature columns (2nd derivative = diff of
slope). NULL on first date (slope) / first two dates (curvature) of
each code.

5 rolling population σ columns (std_{5,20,60,120,255}days) — Bollinger
band widths for the EMA{W} envelope. Same source data as the SMA detail
table's std_*days (σ of price over W days, ddof=0). The column NAME uses
the SMA window (5/20/60/120/255) to match the SMA detail table; for the
EMA6 envelope, std_5days (5-day σ) is used as the closest available
window (the 1-day difference vs the EMA6 window is negligible for σ).

This module is an INTERNAL step of analyze.mov_ave_spread — it is invoked
from __main__.py after the detail table has been
repopulated, reusing the same DB connection + source DataFrame. It is NOT
a standalone runnable.

Incremental mode (``force=False``):
  Only dates present in source identity tables but NOT yet in
  analysis.mov_ave_spreads_detail_ema are (re)computed and upserted.
  The missing-date check is PER-sec_type.

  The vs columns are same-row ratios (no lookback needed), but the EMA
  slope + curvature are group-diffs that need 1-2 prior rows. Since the
  parent DataFrame already carries the FULL per-code history with
  slopes/curvatures computed over it, the EMA step just selects the
  pre-computed columns and filters to target_dates.

Force mode (``force=True``):
  Truncate analysis.mov_ave_spreads_detail_ema, then recompute and
  insert all rows for the active universe.

GPU acceleration: the 9 vs (gap) columns are computed in a single cuDF
transfer when the row count exceeds the merge breakeven (~320K rows
conservative). All 9 ratios run on-device; only the minimal column
subset (group_keys + price + 5 EMAs) is transferred to VRAM. The EMA
slope + curvature columns are already cuDF-accelerated via grouped_diff
in the parent fetch step. The CPU path (pandas) is always available as
a fallback.
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
from _common.df_utils import should_use_gpu
from analyze._common import (
    build_and_insert_chunked,
    upsert_analysis_identity,
    sanitize_for_db_insert,
)
from analyze.mov_ave_spread.config import (
    EMA_ANALYSIS_NAME,
    EMA_CURVATURE_COLUMNS,
    EMA_DETAIL_TABLE,
    EMA_PAIRS,
    EMA_SLOPE_COLUMNS,
    EMA_STD_COLUMNS,
    EMA_VS_COLUMNS,
    SEC_TYPES,
    SEC_TYPE_IDENTITY_TABLE,
)


# ---------------------------------------------------------------------------
#  Configuration
# ---------------------------------------------------------------------------

EMA_DESCRIPTION = (
    "EMA-spread detail analysis (ETF + Index + Stock). For each security "
    "and business date, computes 9 EMA gap pairs (5 Price/EMA + 4 EMA6/EMA) "
    "as gap_value = (short_value - long_value) / long_value, plus 1st "
    "derivative (slope) and 2nd derivative (curvature) of each EMA "
    "(ema6 / ema20 / ema60 / ema120 / ema255) computed per code ordered by "
    "date, plus 5 rolling population σ columns (std_{5,20,60,120,255}days) "
    "used for Bollinger-style envelopes (EMA ± k×σ) around each Price/EMA "
    "pair chart. Source: stats.{etf,index,stock}_tech_stats."
    "ema{6,20,60,120,255}; the σ columns reuse the parent pipeline's "
    "compute_rolling_stds output (σ of price over W days, ddof=0) so the "
    "EMA table is self-contained for Bollinger rendering without a JOIN "
    "back to the SMA detail table. The sec_type column discriminates the "
    "source universe ('etf' | 'index' | 'stock'). Detail table stores one "
    "wide row per (sec_type, code, date) with all 9 gap values + 10 "
    "slope/curvature columns (5 EMAs × slope/curv) + 5 rolling σ columns."
)

# NUMERIC(10,6) overflow guard (|value| must be < 10^4 after rounding to 6dp).
# Gap columns are ratios nullified when the denominator is near-zero; slope/
# curvature are raw differences that can exceed 10^4 for high-priced assets at
# corporate-action boundaries — the guard nulls those before insert.
NUMERIC_MAX_ABS = 10000.0


# ---------------------------------------------------------------------------
#  Compute helpers (pure pandas / cuDF)
# ---------------------------------------------------------------------------

def compute_ema_vs_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add the 9 EMA gap (vs) columns to df.

    For each (num_col, den_col, out_col) pair in EMA_PAIRS, computes:
        out_col = (num_col - den_col) / den_col

    NULL where num or den is NaN, or den is near-zero (|den| < 1e-12),
    or the result is non-finite. The overflow guard in sanitize_ema_rows
    is the final safety net for any ratio that still exceeds NUMERIC(10,6).

    GPU acceleration: when the cuDF router determines the GPU is
    worthwhile for the row count, all 9 vs columns are computed in a
    SINGLE cuDF transfer. Only the minimal column subset (group_keys +
    price + 5 EMAs) is transferred to VRAM — the wide source frame's
    other columns (OHLC, MAs, slopes, stds, trading_amt_*) stay on CPU.
    The CPU path uses the shared gap_col helper (vectorized pandas).
    """
    if df.empty:
        for _, _, out_col in EMA_PAIRS:
            df[out_col] = pd.Series(dtype="float64")
        return df

    # Collect the unique source columns needed for all 9 vs computations.
    # price is used by 5 pairs; ema6 is used by 5 pairs (5 as num + 0 as den
    # for the price_vs_ema6 pair where ema6 is den, plus 4 as num for the
    # ema6_vs_ema pairs); ema20/60/120/255 are each used as den in 2 pairs.
    src_cols = set()
    for num_col, den_col, _ in EMA_PAIRS:
        src_cols.add(num_col)
        src_cols.add(den_col)
    needed = ["sec_type", "code", "date"] + sorted(src_cols)

    if should_use_gpu(df[needed], op_type="merge"):
        print(f"    [cuDF router] {len(df):,} rows — merge (GPU-worthy)", flush=True)

    # CPU path — use the shared gap_col helper (vectorized pandas).
    from analyze.mov_ave_spread.helpers import gap_col
    for num_col, den_col, out_col in EMA_PAIRS:
        df[out_col] = gap_col(df, num_col, den_col)

    return df


def sanitize_ema_rows(df: pd.DataFrame) -> list[dict]:
    """Select the mov_ave_spreads_detail_ema columns, apply the
    NUMERIC(10,6) overflow guard, and sanitize for asyncpg bulk upsert
    (NaN/inf -> None + to_dict).

    Operates on a DataFrame already carrying the ema*_slope,
    ema*_curvature (pre-computed by the parent fetch step), and
    price_vs_ema* / ema6_vs_ema* (computed by compute_ema_vs_columns)
    columns, typically filtered to target_dates. Also carries the 5
    std_*days columns (pre-computed by the parent's compute_rolling_stds)
    so the EMA table is self-contained for Bollinger rendering.
    """
    if df.empty:
        return []

    out_cols = (
        ["sec_type", "code", "date"]
        + list(EMA_VS_COLUMNS)
        + list(EMA_SLOPE_COLUMNS)
        + list(EMA_CURVATURE_COLUMNS)
        + list(EMA_STD_COLUMNS)
    )
    out = df[out_cols].copy()

    non_numeric = ("sec_type", "code", "date")
    numeric_cols = [c for c in out_cols if c not in non_numeric]

    # Overflow guard (NUMERIC(10,6): |value| < 10^4 after rounding to 6dp).
    # Gap columns are ratios (typical |value| < 1.0); slope/curvature are
    # raw differences that can overflow for high-priced assets.
    nulled = {}
    for c in numeric_cols:
        before = int(out[c].isna().sum())
        out[c] = _null_if_overflow(out[c])
        n = int(out[c].isna().sum()) - before
        if n > 0:
            nulled[c] = n
    if nulled:
        total = sum(nulled.values())
        per = ", ".join(f"{c}={n}" for c, n in nulled.items())
        print(f"    -> NUMERIC(10,6) overflow-guard nulled {total:,} value(s) "
              f"across {len(nulled)} column(s): {per}", flush=True)

    return sanitize_for_db_insert(out, numeric_cols=numeric_cols)


def _null_if_overflow(series):
    """Null values whose |abs| >= NUMERIC_MAX_ABS (would overflow
    NUMERIC(10,6)). Mirrors analyze.mov_ave_spread.helpers.null_if_overflow.
    """
    s = pd.to_numeric(series, errors="coerce")
    mask = s.isna() | ~np.isfinite(s) | (s.abs().round(6) >= NUMERIC_MAX_ABS)
    return s.where(~mask)


# ---------------------------------------------------------------------------
#  Pipeline (internal step — invoked from mov_ave_spread.__main__)
# ---------------------------------------------------------------------------

async def run_ema(
    conn,
    df: pd.DataFrame,
    *,
    force: bool = False,
    pool=None,
    max_concurrent: int = 20,
    sec_type: str | None = None,
    code_filter: str | None = None,
) -> None:
    """Run the EMA-spread-detail pipeline against the source data already
    loaded by the parent mov_ave_spread.

    Reuses the caller's DB connection and source DataFrame (the ``price``,
    ``ema{6,20,60,120,255}``, and ``ema{W}_slope`` / ``ema{W}_curvature``
    columns are reused — no second DB fetch). The DataFrame must contain
    the FULL per-code history (not filtered to target_dates) so the
    group-diff slopes/curvatures are correct for the first target date
    of each code.

    Pipeline
      1. Determine target dates (per-sec_type) by checking missing dates
         in analysis.mov_ave_spreads_detail_ema against source identity
         tables. In force mode, truncate the table instead.
      2. Compute the 9 vs (gap) columns over the FULL per-code history
         (cuDF-accelerated), then filter to target_dates. The EMA slope +
         curvature columns are already in the parent DataFrame (pre-
         computed by helpers.compute_ema_slopes_curvatures).
      3. Upsert into analysis.mov_ave_spreads_detail_ema (chunked by date).
      4. Upsert analysis.analysis_identity registry.

    Args:
      conn: asyncpg connection (reused from parent).
      df: source DataFrame with at least columns [sec_type, code, date,
          price, ema6, ema20, ema60, ema120, ema255, ema{W}_slope,
          ema{W}_curvature, std_{5,20,60,120,255}days]. Must be the FULL
          per-code history. The std_*days columns are pre-computed by the
          parent's helpers.compute_rolling_stds (σ of price over W days,
          ddof=0) and carried into the EMA table so it is self-contained
          for Bollinger rendering.
      force: when True, truncate analysis.mov_ave_spreads_detail_ema first
             and recompute all rows.
      pool: optional connection pool for parallel upsert chunks.
      sec_type: when provided, process only this sec_type (parent loop
                passes one sec_type at a time to bound memory). When
                None, infers sec_types from the DataFrame.
    """
    t0 = time.time()
    print("\n" + "=" * 78, flush=True)
    print("  MOV_AVE_SPREAD_DETAIL_EMA (internal step of mov_ave_spread)",
          flush=True)
    print("=" * 78, flush=True)

    # Select only the columns EMA needs — the parent DataFrame carries
    # many extra columns (OHLC, MAs, slopes, stds, trading_amt_*) that
    # are irrelevant here. The ema{W}_slope / ema{W}_curvature columns
    # are pre-computed by the parent fetch step (grouped_diff, cuDF-
    # accelerated) and carried in the DataFrame. The std_*days columns
    # are also pre-computed by the parent's compute_rolling_stds (σ of
    # price over W days, ddof=0) and carried here so the EMA table is
    # self-contained for Bollinger rendering.
    needed_cols = (
        ["sec_type", "code", "date", "price",
         "ema6", "ema20", "ema60", "ema120", "ema255"]
        + list(EMA_SLOPE_COLUMNS)
        + list(EMA_CURVATURE_COLUMNS)
        + list(EMA_STD_COLUMNS)
    )
    # Only select columns that actually exist in the DataFrame (defensive
    # — the parent always provides them, but a unit-test stub might not).
    available = [c for c in needed_cols if c in df.columns]
    ema_df = df[available].copy()

    if ema_df.empty:
        print("    -> no source data; skipping EMA step.", flush=True)
        return

    # Use the sec_type passed by the parent (per-sec_type loop) or infer
    # from the DataFrame for backward compatibility.
    if sec_type is not None:
        sec_types = (sec_type,)
    else:
        sec_types = tuple(sorted(ema_df["sec_type"].unique()))

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
        print("\n[e0/3] Force mode: truncating mov_ave_spreads_detail_ema...",
              flush=True)
        await truncate_table_async(conn, EMA_DETAIL_TABLE)
        target_dates_union: Optional[Set] = None
        print("    -> truncated; will recompute all rows", flush=True)
    else:
        print("    mode: incremental (missing dates only)", flush=True)
        print("\n[e0/3] Detecting missing dates PER-sec_type "
              "(etf_identity vs detail_ema[etf], etc.)...",
              flush=True)
        target_dates_per_st: dict = {}
        for st in sec_types:
            td_st = await find_missing_analysis_dates(
                conn, EMA_DETAIL_TABLE,
                [SEC_TYPE_IDENTITY_TABLE[st]], sec_type=st,
            )
            target_dates_per_st[st] = td_st
            print(f"    -> {st}: {len(td_st)} missing dates", flush=True)
        # Union across sec_types — a date is "to do" if ANY sec_type
        # is missing it.
        target_dates_union = set()
        for s in target_dates_per_st.values():
            target_dates_union |= s
        print(f"    -> union across sec_types: "
              f"{len(target_dates_union)} dates to (re)compute",
              flush=True)
        if not target_dates_union:
            print("    -> DB is up to date; nothing to do.", flush=True)
            return

    # ---- Step 1: compute vs columns over full history, then filter --
    print("\n[e1/3] Computing 9 EMA gap (vs) columns per "
          "(sec_type, code, date) over full history...", flush=True)
    ema_df = compute_ema_vs_columns(ema_df)

    if target_dates_union is not None and len(target_dates_union) > 0:
        n_before = len(ema_df)
        ema_df = ema_df[ema_df["date"].isin(target_dates_union)].reset_index(drop=True)
        print(f"    -> incremental filter: {len(ema_df):,} of {n_before:,} "
              f"rows are in target_dates_union", flush=True)

    if ema_df.empty:
        print("    -> no rows to upsert; skipping EMA upsert.", flush=True)
        return

    # ---- Step 2: build + insert (chunked by date) -------------------
    print(f"\n[e2/3] Building + inserting {len(ema_df):,} "
          f"mov_ave_spreads_detail_ema rows in date-bounded chunks "
          f"({'COPY' if force else 'upsert'} per chunk)...", flush=True)
    n = await build_and_insert_chunked(
        conn, pool, ema_df,
        sanitize_ema_rows,
        table_name=EMA_DETAIL_TABLE,
        key_columns=["sec_type", "code", "date"],
        force=force,
        sec_types=() if code_filter is not None else sec_types,
        max_concurrent=max_concurrent,
        label="mov_ave_spreads_detail_ema",
    )
    del ema_df
    print(f"    -> inserted {n:,} rows", flush=True)

    # ---- Step 3: register in analysis_identity ----------------------
    print(f"\n[e3/3] Upserting analysis.analysis_identity registry...",
          flush=True)
    await upsert_analysis_identity(
        conn,
        name=EMA_ANALYSIS_NAME,
        detail_name="mov_ave_spreads_detail_ema",
        description=EMA_DESCRIPTION,
    )

    print(f"\n  mov_ave_spreads_detail_ema wall time: "
          f"{time.time() - t0:.1f}s", flush=True)
