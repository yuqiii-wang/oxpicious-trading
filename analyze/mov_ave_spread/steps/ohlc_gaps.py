"""Internal OHLC-gaps step for analyze.mov_ave_spread.

Daily intraday OHLC gap metrics — the quantified 高开低走 / 低开高走 /
冲高回落 / 探底回升 feature set of the 2026-09 OHLC studies
(temp_scripts/study_ohlc_gap_vs_extreme.py, study_ohlc_patterns.py).
One row per (sec_type, code, date) in
analysis.mov_ave_spreads_detail_ohlc_gaps.

Source: the parent mov_ave_spread DataFrame (open/high/low/price, where
ETFs carry the ADJUSTED series — same-date ratios are adjustment-invariant
and cross-date legs become total-return gaps), plus the estimated-row
flag (is_ohl_estimated) carried by fetch_source_data. Rows with the flag
TRUE are excluded ENTIRELY before any computation: synthetic OHLC
(open = prev_close, high = low = close) would zero out the wick/range
gaps. The exclusion is also a prev_close break — prev_close references
the last VALID close, so overnight legs never cross a synthetic row.

8 raw gap columns (signed fractions; prev_close = previous valid close):
  gap_open  = (open - prev_close) / prev_close   overnight leg (高开/低开)
  intra     = (close - open) / open              intraday leg (高走/低走)
  uw        = (high - GREATEST(open, close)) / prev_close   冲高回落 wick
  lw        = (LEAST(open, close) - low) / prev_close       探底回升 wick
  rng       = (high - low) / prev_close          daily range
  up_gap    = (high - close) / close             close-relative upper gap
  dn_gap    = (close - low) / close              close-relative lower gap
  close_pos = (close - low) / (high - low)       close location in range

2026-09-27 PATTERN block (computed on UNROUNDED values):
  uw_top10 / lw_top10 — the wick >= its trailing-252d 90th percentile
  (>= 120 obs) per (sec_type, code)
  pattern — FIRST-MATCH precedence: spike_fade (uw_top10) →
  flush_recover (lw_top10) → the gap×candle quadrants at the ±0.1%
  deadband (intra sign) → neutral; NULL where gap_open is NULL

Incremental mode (``force=False``):
  Only dates present in source identity tables but NOT yet in
  analysis.mov_ave_spreads_detail_ohlc_gaps are (re)computed and upserted
  (per-sec_type detection). Dates whose every source row was estimated
  have no rows here and stay "missing" forever — the recompute filter
  matches 0 rows and nothing is inserted (harmless, logged).

Force mode (``force=True``):
  Scoped per-sec_type DELETE (or whole-table truncate when unscoped), then
  recompute and insert all rows for the active universe.

This module is an INTERNAL step of analyze.mov_ave_spread — invoked from
__main__.py after the OHLC step, reusing the same DB connection + source
DataFrame. It is NOT a standalone runnable.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import grouped_rolling_quantile, host_array
from analyze.mov_ave_spread._common import (
    null_if_overflow_counted,
    run_table_step,
)
from analyze._common import sanitize_for_db_insert
from analyze.mov_ave_spread.config import (
    OHLC_GAPS_ANALYSIS_NAME,
    OHLC_GAPS_COLUMNS,
    OHLC_GAPS_EXCLUDE_FLAG_COLUMNS,
    OHLC_GAPS_TABLE,
)

import logging
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Configuration
# ---------------------------------------------------------------------------

OHLC_GAPS_DESCRIPTION = (
    "OHLC intraday gap detail (ETF + Index + Stock): one wide row per "
    "(sec_type, code, date) with 8 raw daily OHLC gap columns. "
    "Gaps: gap_open = (open-prev_close)/prev_close "
    "(overnight leg, 高开/低开); intra = (close-open)/open (intraday leg, "
    "高走/低走); uw = (high-GREATEST(open,close))/prev_close (upper wick, "
    "冲高回落); lw = (LEAST(open,close)-low)/prev_close (lower wick, "
    "探底回升); rng = (high-low)/prev_close; up_gap = (high-close)/close; "
    "dn_gap = (close-low)/close; close_pos = (close-low)/(high-low). "
    "prev_close = previous VALID (non-estimated) close per code — source "
    "rows with is_ohl_estimated TRUE are excluded "
    "entirely, so overnight legs never cross a synthetic row. ETFs use "
    "the parent pipeline's adjusted series (total-return gaps). Feature "
    "set from the 2026-09 OHLC studies (temp_scripts/"
    "study_ohlc_gap_vs_extreme.py, study_ohlc_patterns.py) plus the "
    "2026-09-27 pattern block: uw_top10 / lw_top10 (wick >= the code's "
    "trailing-252d 90th percentile, >= 120 obs) and pattern (spike_fade "
    "/ flush_recover / the gap_up-gap_down advance-decline quadrants / "
    "neutral; NULL = unknown anatomy) — computed on unrounded values. "
    "Source: same "
    "DataFrame as the mov_ave_spread parent pipeline (no second DB "
    "round-trip)."
)

# Group keys for per-code series (prev_close shift, group bounds).
_GROUP_KEYS = ["sec_type", "code"]

# The wick top-decile trailing window (study_ohlc_patterns: RANK_WIN=252
# / RANK_MIN=120 / TOP=0.90) and the gap deadband (±0.1%).
_WICK_WIN, _WICK_MIN, _WICK_Q = 252, 120, 0.90
_GAP_DEADBAND = 0.001


# ---------------------------------------------------------------------------
#  Compute helpers (pure pandas / cuDF)
# ---------------------------------------------------------------------------

def _frac_over_den(num: pd.Series, den: pd.Series) -> pd.Series:
    """num / den with SQL-like NULL semantics: NULL where num or den is
    NaN, |den| < 1e-12 (denormalized — would blow up the ratio), or the
    result is non-finite. Mirrors _common.guards.gap_col's guard set."""
    out = num / den
    mask = (
        num.isna() | den.isna()
        | (den.abs() < 1e-12)
        | ~np.isfinite(out)
    )
    return out.where(~mask, other=None)


def compute_ohlc_gaps(df: pd.DataFrame) -> pd.DataFrame:
    """Add the 8 raw gap columns + the 2026-09-27 pattern block to df.

    Estimated-row exclusion runs FIRST (rows with is_ohl_estimated TRUE
    dropped), then prev_close = previous VALID close per (sec_type, code)
    via grouped shift over the sorted frame.

    Pattern block (computed on UNROUNDED values, before the caller's
    NUMERIC(10,6) sanitize — the ±0.1% deadband never sees a rounded
    gap):
      uw_top10 / lw_top10 — the wick >= the code's trailing-252d 90th
        percentile (>= 120 obs; study_ohlc_patterns' rolling
        rank(pct=True) >= 0.90 as a trailing-quantile threshold). The
        grouped_rolling_quantile CPU-fallback is this suite's accepted
        convention.
      pattern — FIRST-MATCH precedence: spike_fade (uw_top10) →
        flush_recover (lw_top10) → the gap×candle quadrants at the
        ±0.1% deadband (intra sign; green = close > open) → neutral
        (|gap| < deadband). NULL where gap_open is NULL (unknown
        anatomy — the code's first valid day).
    """
    if df.empty:
        for c in OHLC_GAPS_COLUMNS:
            df[c] = pd.Series(dtype="float64")
        return df

    # ---- Estimated-row exclusion (synthetic OHLC) ------------------------
    # set(df.columns) materializes the host column list ONCE — a per-check
    # ``c in df.columns`` would trip the cudf Index.__contains__ fallback
    # twice (one avoidable CPU fallback line per run in the pipeline log).
    df_columns = set(df.columns)
    flag_cols = [
        c for c in OHLC_GAPS_EXCLUDE_FLAG_COLUMNS if c in df_columns
    ]
    if flag_cols:
        bad = df[flag_cols].any(axis=1)
        n_before = len(df)
        df = df[~bad].reset_index(drop=True)
        logger.info(f"    -> excluded {n_before - len(df):,} estimated "
              f"source rows ({' / '.join(flag_cols)})")
        if df.empty:
            for c in OHLC_GAPS_COLUMNS:
                df[c] = pd.Series(dtype="float64")
            return df

    # ---- Sort so groups are contiguous (shift kernel contract) ----------
    df = df.sort_values(_GROUP_KEYS + ["date"], kind="stable",
                        ignore_index=True)

    prev_close = df.groupby(_GROUP_KEYS, sort=False)["price"].shift(1)
    op, hi, lo, cl = df["open"], df["high"], df["low"], df["price"]

    df["gap_open"] = _frac_over_den(op - prev_close, prev_close)
    df["intra"] = _frac_over_den(cl - op, op)
    df["rng"] = _frac_over_den(hi - lo, prev_close)
    df["uw"] = _frac_over_den(hi - np.maximum(op, cl), prev_close)
    df["lw"] = _frac_over_den(np.minimum(op, cl) - lo, prev_close)
    df["up_gap"] = _frac_over_den(hi - cl, cl)
    df["dn_gap"] = _frac_over_den(cl - lo, cl)
    df["close_pos"] = _frac_over_den(cl - lo, hi - lo)

    # ---- Pattern block ---------------------------------------------------
    # Wick top-decile flags: the wick >= its trailing-252d 90th
    # percentile per (sec_type, code), minimum 120 trailing obs
    # (study_ohlc_patterns: RANK_WIN=252 / RANK_MIN=120 / TOP=0.90).
    # groupby-rolling under cudf.pandas transparently falls back to CPU
    # (this suite's accepted rolling-quantile convention). The flags
    # ride the frame as FLOAT64 1.0/0.0/NaN — nullable booleans poison
    # the cudf frame contract (the ohlc_boost precedent); the sanitizer
    # converts them to python bool/None at the COPY boundary.
    for src, flag in (("uw", "uw_top10"), ("lw", "lw_top10")):
        q = grouped_rolling_quantile(
            df, _GROUP_KEYS, src, _WICK_WIN, _WICK_MIN, _WICK_Q,
        )
        has = df[src].notna()
        df[flag] = ((df[src] >= q) & has).astype("float64").where(has)

    # The classified pattern: FIRST-MATCH np.select precedence — the
    # wick shapes override the gap quadrants; a NULL gap_open (unknown
    # anatomy) matches nothing and stays NULL. Edges are NOT applied
    # here — the pattern label is the stored fact; the measured-edge →
    # weight ladder lived in the retired ohlc_boost config (removed
    # 2026-10-05 with the forecast layer).
    gap, uw10, lw10 = df["gap_open"], df["uw_top10"], df["lw_top10"]
    green = df["intra"] > 0
    red = df["intra"] < 0
    labels = np.select(
        [
            uw10 == 1.0,
            lw10 == 1.0,
            (gap >= _GAP_DEADBAND) & green,
            (gap >= _GAP_DEADBAND) & red,
            (gap <= -_GAP_DEADBAND) & green,
            (gap <= -_GAP_DEADBAND) & red,
        ],
        [
            "spike_fade", "flush_recover",
            "gap_up_advance", "gap_up_decline",
            "gap_down_advance", "gap_down_decline",
        ],
        default="neutral",
    ).astype(object)
    # ONE sanctioned host transfer: the NULL-anatomy mask leaves the
    # frame once; the labels become an object array with None (never
    # NaN — NaN in an object column trips cudf's MixedTypeError on
    # every later op).
    gap_na = host_array(df["gap_open"].isna().to_numpy())
    labels[gap_na] = None
    df["pattern"] = labels
    return df


def sanitize_ohlc_gaps_rows(df: pd.DataFrame) -> list[dict]:
    """Select the mov_ave_spreads_detail_ohlc_gaps columns, apply the
    NUMERIC(10,6) overflow guard, and sanitize for asyncpg bulk upsert
    (NaN/inf -> None + to_dict)."""
    if df.empty:
        return []

    out_cols = (
        ["sec_type", "code", "date"]
        + list(OHLC_GAPS_COLUMNS)
    )
    out = df[out_cols].copy()

    # The float64 wick flags (1.0/0.0/NaN) -> python bool/None now: the
    # BOOLEAN columns must receive real bools (asyncpg rejects floats),
    # and a nullable-boolean dtype would poison the frame (the
    # compute-side convention). One host pass per column.
    for c in ("uw_top10", "lw_top10"):
        fv = host_array(out[c].to_numpy(dtype="float64"))
        out[c] = np.where(np.isnan(fv), None, fv > 0)

    non_numeric = ("sec_type", "code", "date", "pattern",
                   "uw_top10", "lw_top10")
    numeric_cols = [c for c in out_cols if c not in non_numeric]

    # Overflow guard (NUMERIC(10,6): |value| < 10^4 after rounding to 6dp).
    # Gap values are ratios (typical |value| < 0.5) — the guard is a safety
    # net for denormalized denominators that slipped past _frac_over_den.
    nulled = {}
    for c in numeric_cols:
        clean, n = null_if_overflow_counted(out[c])
        out[c] = clean
        if n > 0:
            nulled[c] = n
    if nulled:
        total = sum(nulled.values())
        per = ", ".join(f"{c}={n}" for c, n in nulled.items())
        logger.info(f"    -> NUMERIC(10,6) overflow-guard nulled {total:,} "
              f"value(s) across {len(nulled)} column(s): {per}")

    return sanitize_for_db_insert(out, numeric_cols=numeric_cols)


# ---------------------------------------------------------------------------
#  Pipeline (internal step — invoked from mov_ave_spread.__main__; the
#  skeleton lives in _common.steps.run_table_step)
# ---------------------------------------------------------------------------

async def run_ohlc_gaps(
    conn,
    df: pd.DataFrame,
    *,
    force: bool = False,
    pool=None,
    max_concurrent: int = 20,
    sec_type: str | None = None,
    code_filter: str | None = None,
) -> None:
    """Run the OHLC-gaps pipeline against the source data already loaded by
    the parent mov_ave_spread.

    Reuses the caller's DB connection and source DataFrame (the
    ``open`` / ``high`` / ``low`` / ``price`` columns — ETF-adjusted — plus
    the estimated-row flags). The DataFrame must contain the FULL per-code
    history (not filtered to target dates) so prev_close is correct for
    the first target date of each code.

    Pipeline (see _common.steps.run_table_step)
      0. Determine target dates (per-sec_type) by checking missing dates
         in analysis.mov_ave_spreads_detail_ohlc_gaps against source
         identity tables. In force mode, purge the table scope instead
         (scoped DELETE when called per-sec_type, truncate otherwise).
      1. Exclude estimated rows, compute the 8 gaps over the FULL
         per-code history, then filter to target_dates.
      2. Upsert into analysis.mov_ave_spreads_detail_ohlc_gaps (chunked
         by date).
      3. Upsert analysis.analysis_identity registry.
    """
    await run_table_step(
        conn, df,
        title="MOV_AVE_SPREADS_DETAIL_OHLC_GAPS "
              "(internal step of mov_ave_spread)",
        tag="g",
        table=OHLC_GAPS_TABLE,
        analysis_name=OHLC_GAPS_ANALYSIS_NAME,
        detail_name="mov_ave_spreads_detail_ohlc_gaps",
        description=OHLC_GAPS_DESCRIPTION,
        needed_cols=(
            ["sec_type", "code", "date", "price", "open", "high", "low"]
            + list(OHLC_GAPS_EXCLUDE_FLAG_COLUMNS)
        ),
        compute_fn=compute_ohlc_gaps,
        sanitize_fn=sanitize_ohlc_gaps_rows,
        force=force,
        pool=pool,
        max_concurrent=max_concurrent,
        sec_type=sec_type,
        code_filter=code_filter,
    )
