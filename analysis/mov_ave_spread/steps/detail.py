"""Detail-frame builds for analysis.mov_ave_spread (steps/detail.py).

Builds the two same-grain detail frames (one row per sec_type, code,
date) from the pipeline's source frame, split 2026-11-08 out of the
former wide single frame:

  - build_detail_frame   -> analysis.mov_ave_spreads_detail (CORE:
    12 gap columns (the 9 original pairs + the 3 senior inter-MA legs)
    + the alignment aggregate (score 0..5 + its ma_alignment state over
    the five canonical legs — 2026-11-09) + 6 derivative columns
    (price + ma5/20 x slope/curv — see config.MA_DERIV_WINDOWS) + 5
    rolling price σ columns)
  - build_trailing_frame -> analysis.mov_ave_spreads_detail_trailing
    (the per-gap trailing stats: pct_rank / zscore / verdict — one
    triple per PAIRS gap, computed in the fetch derive chain — see
    fetch/source.compute_gap_trailing_stats)

The parent __main__ invokes each via build_and_insert_chunked_df (CSV
COPY path) — the detail "steps" of the pipeline. The trading-amount
columns the former wide frame carried were dropped outright: their
single authoritative home is analysis.mov_ave_trading_amt (written by
the trading-amt internal step in the same pass).

Broken into smaller, cuDF-friendly steps:
  - _assemble_detail_columns / _assemble_trailing_columns: vectorized
    column assembly (trailing stats PROJECTED, not recomputed)
  - _null_overflow_columns: NUMERIC(10,6) overflow guard
  - verdict emit boundary: SMALLINT codes -> COPY-safe "0"/"" strings
  - build_detail_frame / build_trailing_frame: orchestrate the steps,
    return a DataFrame for the CSV COPY writer
    (csv_copy_from_frame_async) — no per-row dicts
"""
from __future__ import annotations

import pandas as pd

from _common.df_utils import safe_columns
from analysis.mov_ave_spread.config import (
    ALIGNMENT_LEG_GAPS,
    GAP_COLUMNS,
    SPREAD_VERDICT_COLUMNS,
)
from analysis.mov_ave_spread._common import (
    gap_col,
    null_if_overflow_counted,
)

import logging
logger = logging.getLogger(__name__)

# float64 verdict code -> COPY-safe CSV text ("" for the not-realized NaN).
_VERDICT_CODE_TEXT = {0.0: "0", 1.0: "1", 2.0: "2"}
# alignment_score 0..5 -> COPY-safe CSV text (same NULL-as-"" contract).
_SCORE_CODE_TEXT = {float(v): str(v) for v in range(6)}

# The five canonical alignment legs as (num_col, den_col) source pairs —
# the close>ma5>ma20>ma60>ma120>ma252 chain.
_ALIGNMENT_LEG_SOURCES = (
    ("price", "ma5"),
    ("ma5", "ma20"),
    ("ma20", "ma60"),
    ("ma60", "ma120"),
    ("ma120", "ma252"),
)

# Guard against config drift: the leg gap names and source pairs must
# describe the same chain (the senior PAIRS entries carry them).
assert tuple(ALIGNMENT_LEG_GAPS) == (
    "price_vs_ma5", "ma5_vs_ma20", "ma20_vs_ma60",
    "ma60_vs_ma120", "ma120_vs_ma252",
)


def _alignment_columns(leg_gaps: list[pd.Series]) -> dict[str, pd.Series]:
    """The alignment aggregate over the five canonical legs.

    score = count of positive legs (0..5), NULL when ANY leg is NULL
    (insufficient MA history — never a verdict); state = the
    ``ma_alignment`` label code (2 bull = score 5, 0 bear = score 0,
    1 mixed otherwise), NULL with the score. Both carried as float64
    NaN/code (the emit boundary renders COPY-safe text) — the int64-
    with-NULLs column class degrades every later to_numpy (the
    rsi_ranks emit-boundary lesson). Pure elementwise chains —
    cuDF-compatible: NaN > 0 is False on a float64 column (the plain
    NaN class, not nullable), and bools cast to int64 before any
    arithmetic (the bool×numeric ban).
    """
    all_defined = leg_gaps[0].notna()
    for leg in leg_gaps[1:]:
        all_defined = all_defined & leg.notna()
    total = leg_gaps[0].gt(0).astype("int64")
    for leg in leg_gaps[1:]:
        total = total + leg.gt(0).astype("int64")
    score = total.astype("float64").where(all_defined)
    state = (
        1
        + (score == 5.0).astype("int64")
        - (score == 0.0).astype("int64")
    ).astype("float64").where(score.notna())
    return {"alignment_score": score, "alignment_state": state}


def _assemble_detail_columns(
    df: pd.DataFrame,
) -> pd.DataFrame:
    """Assemble the CORE detail DataFrame: 12 gap + alignment (2) +
    6 derivative + 5 σ columns.

    Pure vectorized pandas — no Python loops, no object-dtype
    intermediates. cuDF-compatible.
    """
    price_vs_ma5 = gap_col(df, "price", "ma5")
    ma5_vs_ma20 = gap_col(df, "ma5", "ma20")
    ma20_vs_ma60 = gap_col(df, "ma20", "ma60")
    ma60_vs_ma120 = gap_col(df, "ma60", "ma120")
    ma120_vs_ma252 = gap_col(df, "ma120", "ma252")
    alignment = _alignment_columns([
        price_vs_ma5, ma5_vs_ma20, ma20_vs_ma60,
        ma60_vs_ma120, ma120_vs_ma252,
    ])
    return pd.DataFrame({
        "sec_type":    df["sec_type"],
        "code":        df["code"],
        "date":        df["date"],
        "price_vs_ma5":   price_vs_ma5,
        "price_vs_ma20":  gap_col(df, "price", "ma20"),
        "price_vs_ma60":  gap_col(df, "price", "ma60"),
        "price_vs_ma120": gap_col(df, "price", "ma120"),
        "price_vs_ma252": gap_col(df, "price", "ma252"),
        "ma5_vs_ma20":    ma5_vs_ma20,
        "ma5_vs_ma60":    gap_col(df, "ma5",   "ma60"),
        "ma5_vs_ma120":   gap_col(df, "ma5",   "ma120"),
        "ma5_vs_ma252":   gap_col(df, "ma5",   "ma252"),
        "ma20_vs_ma60":   ma20_vs_ma60,
        "ma60_vs_ma120":  ma60_vs_ma120,
        "ma120_vs_ma252": ma120_vs_ma252,
        **alignment,
        "price_slope":     df["price_slope"],
        "ma5_slope":       df["ma5_slope"],
        "ma20_slope":      df["ma20_slope"],
        "price_curvature": df["price_curvature"],
        "ma5_curvature":   df["ma5_curvature"],
        "ma20_curvature":  df["ma20_curvature"],
        "std_5days":   df["std_5days"],
        "std_20days":  df["std_20days"],
        "std_60days":  df["std_60days"],
        "std_120days": df["std_120days"],
        "std_252days": df["std_252days"],
    })


def _assemble_trailing_columns(
    df: pd.DataFrame,
) -> pd.DataFrame:
    """Assemble the TRAILING companion DataFrame: the 27 per-gap
    trailing-stat columns (computed over FULL history in
    fetch/source.compute_gap_trailing_stats — projected here, never
    recomputed: build chunks are date-bounded slices without the
    252-row lookback). Verdict columns carry the spread_verdict
    label code as float64 NaN/0/1/2 until the emit boundary below.
    """
    return pd.DataFrame({
        "sec_type":    df["sec_type"],
        "code":        df["code"],
        "date":        df["date"],
        **{f"{gap}_pct_rank": df[f"{gap}_pct_rank"] for gap in GAP_COLUMNS},
        **{f"{gap}_zscore":   df[f"{gap}_zscore"]   for gap in GAP_COLUMNS},
        **{f"{gap}_verdict":  df[f"{gap}_verdict"]  for gap in GAP_COLUMNS},
    })


def _null_overflow_columns(
    out_df: pd.DataFrame,
    non_numeric_cols: tuple[str, ...],
    wide_numeric_cols: tuple[str, ...] = (),
) -> dict[str, int]:
    """Null any value whose absolute value would overflow its column's
    NUMERIC bound.

    Default bound: NUMERIC(10,6) — |value| < 10^4 after rounding to 6 dp.
    Wide bound (columns in ``wide_numeric_cols``): NUMERIC(24,4) —
    |value| < 10^20 after rounding to 4 dp (the trading_amt_* columns
    the former wide frame carried; kept for any future wide-bound
    caller).

    Returns a dict of {column: count_nulled} for logging.
    """
    from analysis.mov_ave_spread.config import NUMERIC_WIDE_MAX_ABS

    # Host-pure column list (iterating a proxied Index falls back under
    # cudf.pandas).
    numeric_cols = [c for c in safe_columns(out_df) if c not in non_numeric_cols]
    nulled_counts = {}
    for c in numeric_cols:
        if c in wide_numeric_cols:
            clean, n = null_if_overflow_counted(
                out_df[c], max_abs=NUMERIC_WIDE_MAX_ABS, scale=4,
            )
        else:
            clean, n = null_if_overflow_counted(out_df[c])
        out_df[c] = clean
        if n > 0:
            nulled_counts[c] = n
    return nulled_counts


def build_detail_frame(df: pd.DataFrame) -> pd.DataFrame:
    """For each (sec_type, code, date) row, project the 12 gap values +
    the alignment aggregate + 6 derivative columns + 5 rolling σ
    columns and return the CORE detail DataFrame ready for
    ``csv_copy_from_frame_async`` (COPY ... FORMAT csv).

    Orchestrates 3 smaller steps:
      1. _assemble_detail_columns — vectorized column assembly
      2. _null_overflow_columns — NUMERIC(10,6) overflow guard for gap /
         slope / curvature / std columns. Also nulls NaN/±inf — the CSV
         writer renders NaN as an empty field (SQL NULL), so no
         sanitize_for_db_insert pass is needed (the old dict path's
         astype(object).where + to_dict cost ~3s of client CPU per
         100k-row chunk for zero benefit here).
      3. the alignment code emit boundary — alignment_score/
         alignment_state are SMALLINT label codes carried as float64
         NaN/0..5: rendered to COPY-safe strings ("" = NULL) exactly
         like the trailing verdicts (a numeric render would emit "2.0"
         float text, which COPY rejects into SMALLINT).
    """
    if df.empty:
        return df.iloc[0:0]

    # Step 1: assemble the core detail columns (vectorized).
    out_df = _assemble_detail_columns(df)

    # Step 2: overflow guard (default NUMERIC(10,6) bound). The two
    # alignment code columns are excluded — codes never overflow and are
    # rendered to text in step 3.
    non_numeric_cols = ("sec_type", "code", "date",
                        "alignment_score", "alignment_state")
    nulled_counts = _null_overflow_columns(out_df, non_numeric_cols)
    if nulled_counts:
        total = sum(nulled_counts.values())
        per_col = ", ".join(f"{c}={n}" for c, n in nulled_counts.items())
        logger.info(f"    -> overflow-guard nulled {total:,} value(s) across "
              f"{len(nulled_counts)} column(s): {per_col}")

    # Step 3: alignment code emit boundary (see the docstring).
    out_df["alignment_score"] = (
        out_df["alignment_score"].map(_SCORE_CODE_TEXT).fillna(""))
    out_df["alignment_state"] = (
        out_df["alignment_state"].map(_VERDICT_CODE_TEXT).fillna(""))

    return out_df


def build_trailing_frame(df: pd.DataFrame) -> pd.DataFrame:
    """For each (sec_type, code, date) row, project the 27 trailing-stat
    columns (9 pct_rank + 9 zscore + 9 verdict) and return the TRAILING
    companion DataFrame ready for ``csv_copy_from_frame_async``.

    Orchestrates 3 smaller steps:
      1. _assemble_trailing_columns — vectorized column assembly
         (stats projected, never recomputed at chunk build)
      2. _null_overflow_columns — NUMERIC(7,6)/(10,6) default-bound
         overflow guard for the pct_rank/zscore columns
      3. verdict emit boundary. The renderer keeps each column's
         native dtype, so a numeric verdict column would render "0.0"
         float text — which PostgreSQL COPY rejects into SMALLINT.
         Render the codes as cuDF-native STRINGS ("0"/"1"/"2",
         NaN -> "" = NULL): unquoted CSV digits parse cleanly, and str
         columns ride the incremental path's per-sec_type
         groupby/concat filter natively (object arrays would not).
    """
    if df.empty:
        return df.iloc[0:0]

    # Step 1: assemble the trailing columns (vectorized).
    out_df = _assemble_trailing_columns(df)

    # Step 2: overflow guard. The 9 verdict columns are excluded — they
    # are SMALLINT label codes carried as float64 NaN/0/1/2 (codes never
    # overflow) and are rendered to COPY-safe text in step 3.
    non_numeric_cols = ("sec_type", "code", "date", *SPREAD_VERDICT_COLUMNS)
    nulled_counts = _null_overflow_columns(out_df, non_numeric_cols)
    if nulled_counts:
        total = sum(nulled_counts.values())
        per_col = ", ".join(f"{c}={n}" for c, n in nulled_counts.items())
        logger.info(f"    -> overflow-guard nulled {total:,} value(s) across "
              f"{len(nulled_counts)} column(s): {per_col}")

    # Step 3: verdict emit boundary (see the docstring).
    for col in SPREAD_VERDICT_COLUMNS:
        out_df[col] = (
            out_df[col].map(_VERDICT_CODE_TEXT).fillna("")
        )

    return out_df
