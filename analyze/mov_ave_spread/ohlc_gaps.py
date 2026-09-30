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

import time
from typing import Optional, Set

import numpy as np
import pandas as pd

from _common.build_commons import (
    truncate_table_async,
    find_missing_analysis_dates,
)
from _common.db_commons import chunked_purge_async
from _common.df_utils import (
    column_subset,
    grouped_rolling_quantile,
    host_array,
    host_unique,
    to_dt64,
)
from analyze.mov_ave_spread.helpers import null_if_overflow_counted
from analyze._common import (
    build_and_insert_chunked,
    upsert_analysis_identity,
    sanitize_for_db_insert,
)
from analyze.mov_ave_spread.config import (
    OHLC_GAPS_ANALYSIS_NAME,
    OHLC_GAPS_COLUMNS,
    OHLC_GAPS_EXCLUDE_FLAG_COLUMNS,
    OHLC_GAPS_TABLE,
    SEC_TYPE_IDENTITY_TABLE,
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
    result is non-finite. Mirrors helpers.gap_col's guard set."""
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
    # weight ladder lives in analysis_forecasts.ohlc_boost's config.
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
#  Pipeline (internal step — invoked from mov_ave_spread.__main__)
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

    Pipeline
      1. Determine target dates (per-sec_type) by checking missing dates
         in analysis.mov_ave_spreads_detail_ohlc_gaps against source
         identity tables. In force mode, purge the table instead (scoped
         DELETE when called per-sec_type, truncate otherwise).
      2. Exclude estimated rows, compute the 8 gaps over the FULL
         per-code history, then filter to target_dates.
      3. Upsert into analysis.mov_ave_spreads_detail_ohlc_gaps (chunked
         by date).
      4. Upsert analysis.analysis_identity registry.

    Args:
      conn: asyncpg connection (reused from parent).
      df: source DataFrame with at least columns [sec_type, code, date,
          price, open, high, low, is_ohl_estimated].
          Must be the FULL per-code history.
      force: when True, purge analysis.mov_ave_spreads_detail_ohlc_gaps
             first and recompute all rows.
      pool: optional connection pool for parallel upsert chunks.
      sec_type: when provided, process only this sec_type (parent loop
                passes one sec_type at a time to bound memory).
      code_filter: --code mode — the caller already DELETEd this code's
                rows; recompute ALL dates for this code and bypass the
                per-sec_type missing-date detection.
    """
    t0 = time.time()
    logger.info("\n" + "=" * 78)
    logger.info("  MOV_AVE_SPREADS_DETAIL_OHLC_GAPS "
          "(internal step of mov_ave_spread)")
    logger.info("=" * 78)

    needed_cols = (
        ["sec_type", "code", "date", "price", "open", "high", "low"]
        + list(OHLC_GAPS_EXCLUDE_FLAG_COLUMNS)
    )
    # Only select columns that actually exist in the DataFrame (defensive
    # — the parent always provides them, but a unit-test stub might not).
    available = column_subset(df, needed_cols)
    gaps_df = df[available].copy()

    if gaps_df.empty:
        logger.info("    -> no source data; skipping OHLC-gaps step.")
        return

    if sec_type is not None:
        sec_types = (sec_type,)
    else:
        sec_types = tuple(host_unique(gaps_df["sec_type"]))

    # ---- Step 0: determine target dates (per-sec_type) ------------------
    if code_filter is not None:
        logger.info("    mode: SINGLE-CODE (full recompute for this code)")
        target_dates_union: Optional[Set] = None
    elif force:
        logger.info("    mode: FORCE (full recompute)")
        if sec_type is not None:
            # Per-sec_type scope: DELETE only this sec_type's rows — the
            # parent loop calls run_ohlc_gaps once per sec_type, so a
            # whole-table TRUNCATE would wipe the other sec_types' rows.
            logger.info(f"\n[g0/3] Force mode: deleting {sec_type} rows from "
                  "mov_ave_spreads_detail_ohlc_gaps...")
            n_del = await chunked_purge_async(
                conn, OHLC_GAPS_TABLE,
                where_sql="sec_type = $1", params=(sec_type,),
            )
            logger.info(f"    -> deleted {n_del:,} rows; will recompute all "
                  f"{sec_type} rows")
        else:
            logger.info("\n[g0/3] Force mode: truncating "
                  "mov_ave_spreads_detail_ohlc_gaps...")
            await truncate_table_async(conn, OHLC_GAPS_TABLE)
            logger.info("    -> truncated; will recompute all rows")
        target_dates_union: Optional[Set] = None
    else:
        logger.info("    mode: incremental (missing dates only)")
        logger.info("\n[g0/3] Detecting missing dates PER-sec_type "
              "(etf_identity vs ohlc_gaps[etf], etc.)...")
        target_dates_per_st: dict = {}
        for st in sec_types:
            td_st = await find_missing_analysis_dates(
                conn, OHLC_GAPS_TABLE,
                [SEC_TYPE_IDENTITY_TABLE[st]], sec_type=st,
            )
            target_dates_per_st[st] = td_st
            logger.info(f"    -> {st}: {len(td_st)} missing dates")
        target_dates_union = set()
        for s in target_dates_per_st.values():
            target_dates_union |= s
        logger.info(f"    -> union across sec_types: "
              f"{len(target_dates_union)} dates to (re)compute")
        if not target_dates_union:
            logger.info("    -> DB is up to date; nothing to do.")
            return

    # ---- Step 1: exclude estimated rows, compute gaps, filter ----------
    logger.info("\n[g1/3] Computing 8 OHLC gap columns per "
          "(sec_type, code, date) over full history (estimated rows "
          "excluded)...")
    gaps_df = compute_ohlc_gaps(gaps_df)

    if target_dates_union is not None and len(target_dates_union) > 0:
        n_before = len(gaps_df)
        # datetime64 ndarray comparison — isin with a python-date SET
        # never matches a datetime64 column (fetch.py incremental-filter
        # convention).
        td64 = to_dt64(sorted(target_dates_union))
        gaps_df = gaps_df[gaps_df["date"].isin(td64)].reset_index(drop=True)
        logger.info(f"    -> incremental filter: {len(gaps_df):,} of "
              f"{n_before:,} rows are in target_dates_union")

    if gaps_df.empty:
        logger.info("    -> no rows to upsert (all target dates were "
              "estimated-only); skipping OHLC-gaps upsert.")
        return

    # ---- Step 2: build + insert (chunked by date) -----------------------
    logger.info(f"\n[g2/3] Building + inserting {len(gaps_df):,} "
          f"mov_ave_spreads_detail_ohlc_gaps rows in date-bounded chunks "
          f"({'COPY' if force else 'upsert'} per chunk)...")
    n = await build_and_insert_chunked(
        conn, pool, gaps_df,
        sanitize_ohlc_gaps_rows,
        table_name=OHLC_GAPS_TABLE,
        key_columns=["sec_type", "code", "date"],
        force=force,
        sec_types=() if code_filter is not None else sec_types,
        max_concurrent=max_concurrent,
        label="mov_ave_spreads_detail_ohlc_gaps",
    )
    del gaps_df
    logger.info(f"    -> inserted {n:,} rows")

    # ---- Step 3: register in analysis_identity --------------------------
    logger.info(f"\n[g3/3] Upserting analysis.analysis_identity registry...")
    await upsert_analysis_identity(
        conn,
        name=OHLC_GAPS_ANALYSIS_NAME,
        detail_name="mov_ave_spreads_detail_ohlc_gaps",
        description=OHLC_GAPS_DESCRIPTION,
    )

    logger.info(f"\n  mov_ave_spreads_detail_ohlc_gaps wall time: "
          f"{time.time() - t0:.1f}s")
