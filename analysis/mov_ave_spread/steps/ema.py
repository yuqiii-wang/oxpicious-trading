"""Internal EMA step for analysis.mov_ave_spread.

EMA-based spread detail (price_vs_ema{6,12,26} + ema6_vs_ema{12,26} +
ema12_vs_ema26 + ema{6,12}_slope + ema{6,12}_curvature +
std_{5,12,26}days) for ETF + Index + Stock + Industry. One row per
(sec_type, code, date) in analysis.mov_ave_spreads_detail_ema.

2026-10-26 the TYPICAL EMA rebase (user-directed "in ma-spread Pairs
(Exponential MA) is still using non-typical ema, which is wrong"): the
pair vocabulary covers exactly the TYPICAL EMA spans {6, 12, 26} (6 the
A-share short, 12/26 the classic MACD pair — precomputed in tech_stats
since the same day); the SMA-convention windows (20/60/120/255) stay in
the Simple-MA section/detail table.

Source: stats.{etf,index,stock}_tech_stats.ema{6,12,26} (already
fetched by the parent mov_ave_spread.fetch_source_data — reuses the same
DataFrame, no second DB round-trip; industry computes them in-pipeline
with the same conventions). The EMA slope + curvature columns
(windows 6/12, each a DIRECT diff of the EMA line — emaW[t] − emaW[t−1];
curvature = that slope's diff) are pre-computed by the parent
(_common.derive.compute_ema_slopes_curvatures via grouped_diff, cuDF-accelerated)
and carried in the source DataFrame. The std_*days columns (rolling
population σ of price over W days, ddof=0) are pre-computed by the
parent's _common.derive.compute_rolling_stds and carried in the source DataFrame
so the EMA table is self-contained for Bollinger rendering without a JOIN
back to the SMA detail table. This step adds the 6 vs (gap) columns and
assembles the final wide row.

6 gap pairs (canonical order):
  3 Price-vs-EMA:  gap = (price - emaX) / emaX,  X ∈ {6,12,26}
  3 EMA-vs-EMA:    gap = (emaS - emaL) / emaL,   (6,12) (6,26) (12,26)
                   — the classic 12/26 pair IS the MACD line's
                   components.

2 EMA slope columns (1st derivative = group-diff per (sec_type, code)
ordered by date) + 2 EMA curvature columns (2nd derivative = diff of
slope), windows 6/12. NULL on first date (slope) / first two dates
(curvature) of each code; the 26 leg carries no derivatives (the SMA
table's 60/120/255 precedent). The price leg's derivatives live on the
SMA detail table (mov_ave_spreads_detail.price_slope).

3 rolling population σ columns (std_{5,12,26}days) — Bollinger
band widths for the EMA{W} envelope over the typical long windows.
The column NAME uses the SMA window for the 6 leg (std_5days — the
1-day difference vs the EMA6 window is negligible for σ); std_12days /
std_26days match the EMA windows exactly and exist only on this table.

This module is an INTERNAL step of analysis.mov_ave_spread — it is invoked
from __main__.py after the detail table has been
repopulated, reusing the same DB connection + source DataFrame. It is NOT
a standalone runnable.

Incremental mode (``force=False``):
  Only dates present in source identity tables but NOT yet in
  analysis.mov_ave_spreads_detail_ema are (re)computed and upserted.
  The missing-date check is PER-sec_type. The vs columns are same-row
  ratios (no lookback needed), so the EMA step just computes them over
  the FULL per-code history and filters to target_dates.

Force mode (``force=True``):
  Truncate analysis.mov_ave_spreads_detail_ema, then recompute and
  insert all rows for the active universe.

GPU acceleration: the 6 vs (gap) columns are computed in a single cuDF
transfer when the row count exceeds the merge breakeven (~320K rows
conservative). All 6 ratios run on-device; only the minimal column
subset (group_keys + price + 3 EMAs) is transferred to VRAM. The CPU
path (pandas) is always available as a fallback.
"""
from __future__ import annotations

import pandas as pd

from analysis._common import sanitize_for_db_insert
from analysis.mov_ave_spread._common import (
    TableStep,
    gap_col,
    null_if_overflow_counted,
)
from analysis.mov_ave_spread.config import (
    EMA_ANALYSIS_NAME,
    EMA_CURVATURE_COLUMNS,
    EMA_DETAIL_TABLE,
    EMA_PAIRS,
    EMA_SLOPE_COLUMNS,
    EMA_STD_COLUMNS,
    EMA_VS_COLUMNS,
)

import logging
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Configuration
# ---------------------------------------------------------------------------

EMA_DESCRIPTION = (
    "EMA-spread detail analysis (ETF + Index + Stock + Industry), the "
    "TYPICAL EMA pair set (2026-10-26 rebase): for each security and "
    "business date, computes 6 EMA gap pairs (3 Price/EMA + 3 EMA/EMA) "
    "over the typical EMA spans {6, 12, 26} — 6 the A-share short, "
    "12/26 the classic MACD pair — as gap_value = (short_value - "
    "long_value) / long_value, plus 1st derivative (slope) and 2nd "
    "derivative (curvature) of the EMA6 and EMA12 lines (each a DIRECT "
    "diff — emaW[t] − emaW[t−1] and that slope's diff; the 26 leg "
    "carries no derivatives, the SMA table's 60/120/255 precedent; the "
    "price leg's derivatives live on the SMA detail table) computed per "
    "code ordered by date, plus 3 rolling population σ columns "
    "(std_{5,12,26}days) used for Bollinger-style envelopes (EMA ± k×σ) "
    "around each Price/EMA pair chart. Source: stats.{etf,index,stock}_"
    "tech_stats.ema{6,12,26} (industry computed in-pipeline with the "
    "same conventions); the σ columns reuse the parent pipeline's "
    "compute_rolling_stds output (σ of price over W days, ddof=0) so the "
    "EMA table is self-contained for Bollinger rendering without a JOIN "
    "back to the SMA detail table. The sec_type column discriminates the "
    "source universe. Detail table stores one wide row per (sec_type, "
    "code, date) with all 6 gap values + 4 slope/curvature columns "
    "(EMA6/EMA12 × slope/curv) + 3 rolling σ columns."
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
    """Add the 6 EMA gap (vs) columns to df.

    For each (num_col, den_col, out_col) pair in EMA_PAIRS, computes:
        out_col = (num_col - den_col) / den_col

    NULL where num or den is NaN, or den is near-zero (|den| < 1e-12),
    or the result is non-finite. The overflow guard in sanitize_ema_rows
    is the final safety net for any ratio that still exceeds NUMERIC(10,6).

    GPU acceleration: when the cuDF router determines the GPU is
    worthwhile for the row count, all 6 vs columns are computed in a
    SINGLE cuDF transfer. Only the minimal column subset (group_keys +
    price + 3 EMAs) is transferred to VRAM — the wide source frame's
    other columns (OHLC, MAs, slopes, stds, trading_amt_*) stay on CPU.
    The CPU path uses the shared gap_col helper (vectorized pandas).
    """
    if df.empty:
        for _, _, out_col in EMA_PAIRS:
            df[out_col] = pd.Series(dtype="float64")
        return df

    # All 6 ratios are elementwise whole-frame ops via the shared gap_col
    # helper — the cudf.pandas GPU fast path (elementwise where-chains run
    # on device under activate(); no per-row Python work anywhere).
    for num_col, den_col, out_col in EMA_PAIRS:
        df[out_col] = gap_col(df, num_col, den_col)

    return df


def sanitize_ema_rows(df: pd.DataFrame) -> list[dict]:
    """Select the mov_ave_spreads_detail_ema columns, apply the
    NUMERIC(10,6) overflow guard, and sanitize for asyncpg bulk upsert
    (NaN/inf -> None + to_dict).

    Operates on a DataFrame already carrying the ema{6,12}_slope /
    ema{6,12}_curvature (pre-computed by the parent fetch step) and
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
    # Gap columns are ratios (typical |value| < 1.0); the σ columns are
    # price-unit values that can overflow for high-priced assets.
    nulled = {}
    for c in numeric_cols:
        clean, n = null_if_overflow_counted(out[c])
        out[c] = clean
        if n > 0:
            nulled[c] = n
    if nulled:
        total = sum(nulled.values())
        per = ", ".join(f"{c}={n}" for c, n in nulled.items())
        logger.info(f"    -> NUMERIC(10,6) overflow-guard nulled {total:,} value(s) "
              f"across {len(nulled)} column(s): {per}")

    return sanitize_for_db_insert(out, numeric_cols=numeric_cols)


# ---------------------------------------------------------------------------
#  Pipeline (internal step — invoked from mov_ave_spread.__main__ via the
#  TableStep template in _common.steps)
# ---------------------------------------------------------------------------

class EmaStep(TableStep):
    """Run the EMA-spread-detail pipeline against the source data already
    loaded by the parent mov_ave_spread.

    Reuses the caller's DB connection and source DataFrame (the ``price``
    and ``ema{6,20,60,120,255}`` columns are reused — no second DB
    fetch). The DataFrame must contain the FULL per-code history (not
    filtered to target_dates) so the rolling σ columns are correct for
    the first target date of each code.

    Pipeline (see _common.steps.TableStep.execute)
      0. Determine target dates (per-sec_type) by checking missing dates
         in analysis.mov_ave_spreads_detail_ema against source identity
         tables. In force mode, purge the table scope instead.
      1. Compute the 6 vs (gap) columns over the FULL per-code history,
         then filter to target_dates.
      2. Upsert into analysis.mov_ave_spreads_detail_ema (chunked by date).
      3. Upsert analysis.analysis_identity registry.
    """

    title = "MOV_AVE_SPREAD_DETAIL_EMA (internal step of mov_ave_spread)"
    tag = "e"
    table = EMA_DETAIL_TABLE
    analysis_name = EMA_ANALYSIS_NAME
    detail_name = "mov_ave_spreads_detail_ema"
    description = EMA_DESCRIPTION
    needed_cols = tuple(
        ["sec_type", "code", "date", "price", "ema6", "ema12", "ema26"]
        + list(EMA_SLOPE_COLUMNS)
        + list(EMA_CURVATURE_COLUMNS)
        + list(EMA_STD_COLUMNS)
    )

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        return compute_ema_vs_columns(df)

    def sanitize_rows(self, df: pd.DataFrame) -> list[dict]:
        return sanitize_ema_rows(df)
