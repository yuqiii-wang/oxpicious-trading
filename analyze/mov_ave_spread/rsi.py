"""Internal RSI step for analyze.mov_ave_spread.

Wilder RSI (3/6/10/14/20/60 days) for ETF + Index + Stock.
One row per (sec_type, code, date) in analysis.mov_ave_rsi.

RSI uses Wilder's smoothing (EWM alpha=1/N, adjust=False, min_periods=N).

Source prices: ETF = COALESCE(etf_adjustment.adj_close,
etf_basic_stats.close); index = index_basic_stats.close; stock =
stock_basic_stats.close (same price convention as the parent
mov_ave_spread analysis — the price column is reused from the parent's
already-fetched source DataFrame, avoiding a second DB round-trip).

This module is an INTERNAL step of analyze.mov_ave_spread — it is invoked
from __main__.py after the detail table has been
repopulated, reusing the same DB connection + source DataFrame. It is NOT
a standalone runnable.

Incremental mode (``force=False``):
  Only dates present in source identity tables but NOT yet in
  analysis.mov_ave_rsi are (re)computed and upserted. The missing-date
  check is PER-sec_type (PK is (sec_type, code, date) — a date populated
  for ETF must not mask the same date being missing for index/stock).

  RSI is recursive, so the FULL per-code history (already in the parent's
  DataFrame) is used and the RSI computed over it, then filtered to
  target_dates before upsert.

Force mode (``force=True``):
  Truncate analysis.mov_ave_rsi, then recompute and insert all rows for
  the active universe.
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
from _common.df_utils import grouped_diff, host_unique, to_dt64
from analyze.mov_ave_spread.helpers import null_if_overflow_counted
from analyze._common import (
    build_and_insert_chunked,
    upsert_analysis_identity,
    sanitize_for_db_insert,
)
from analyze.mov_ave_spread.config import SEC_TYPES, SEC_TYPE_IDENTITY_TABLE

import logging
logger = logging.getLogger(__name__)

# Set after the first GPU-route failure is logged: on a CPU-only host the
# per-call failure is permanent (e.g. cupy not installed) and repeating it
# per chunk is log noise, not new information.
_ewm_gpu_warned = False


# ---------------------------------------------------------------------------
#  Configuration
# ---------------------------------------------------------------------------

RSI_TABLE = "analysis.mov_ave_rsi"
RSI_ANALYSIS_NAME = "mov_ave_rsi"

RSI_DESCRIPTION = (
    "Wilder Relative Strength Index (RSI) analysis (ETF + Index + Stock). "
    "For each security and business date, computes Wilder RSI for 6 "
    "windows (rsi_3days / rsi_6days / rsi_10days / rsi_14days / "
    "rsi_20days / rsi_60days) using Wilder's exponential smoothing "
    "(EWM alpha=1/N, adjust=False, min_periods=N; RSI = "
    "100 - 100/(1+RS) where RS = avg_gain/avg_loss over the per-code "
    "N-day gain/loss series; RSI=100 on pure uptrend, 0 on pure "
    "downtrend, NULL when flat). "
    "The sec_type column discriminates the source universe ('etf' | "
    "'index' | 'stock'); ETF price uses COALESCE(etf_adjustment.adj_close, "
    "etf_basic_stats.close), index uses index_basic_stats.close, stock "
    "uses stock_basic_stats.close."
)

# RSI windows (Wilder smoothing). 3 is an ultra-short momentum window;
# 14 is the classic Wilder default; 6/10/20 are common shorter/longer
# variants; 60 (~3 trading months) is a longer-term momentum window that
# smooths out short-term noise — useful for trend-confirmation alongside
# the shorter windows.
RSI_WINDOWS = (3, 6, 10, 14, 20, 60)

# NUMERIC(10,6) overflow guard (|value| must be < 10^4 after rounding to 6dp).
# RSI is bounded 0..100, so overflow is unlikely — the guard is a safety
# net mirroring the parent mov_ave_spread.
NUMERIC_MAX_ABS = 10000.0


# ---------------------------------------------------------------------------
#  Compute helpers (pure pandas / cuDF)
# ---------------------------------------------------------------------------

def compute_rsi(
    df: pd.DataFrame,
    rsi_windows: tuple = RSI_WINDOWS,
) -> pd.DataFrame:
    """Add rsi_{W}days (Wilder) columns to df, per (sec_type, code)
    ordered by date.

    Wilder RSI: EWM alpha=1/W, adjust=False, min_periods=W. Range 0..100.

    Rows with NULL price produce NULL RSI values (gain/loss are NaN, and
    ``ignore_na=True`` in the EWM skips them without propagating NaN
    forward — RSI carries the last non-null value through gaps).

    Must be called on the FULL per-code history (Wilder smoothing is
    recursive); the caller filters to target_dates afterwards.

    Returns df (sorted by sec_type, code, date) with the new columns added.

    GPU acceleration: the diff (delta = price[t]-price[t-1]) is routed to
    cuDF via the shared ``grouped_diff`` helper when the row count
    exceeds the ``groupby_diff`` breakeven (~320K rows conservative).
    The helper transfers only the minimal column subset (group_keys +
    price) to VRAM. Wilder EWM stays on pandas: cuDF lacks grouped-ewm
    support and the per-group apply fallback is no faster than pandas'
    vectorized ``groupby.ewm``, so the shared helpers' auto-routing is
    the right granularity here.
    """
    if df.empty:
        return df

    df = df.sort_values(["sec_type", "code", "date"]).reset_index(drop=True)
    grp = ["sec_type", "code"]

    # delta = price[t] - price[t-1] via shared grouped_diff (auto-routes
    # to cuDF when the row count exceeds the groupby_diff breakeven).
    grouped_diff(df, grp, "price", out_names="_delta", periods=1, sort=False)
    delta = df["_delta"]
    # gain/loss: positive/negative parts of delta. For null-price rows
    # (delta is NaN), gain and loss must stay NaN — NOT 0 — so the EWM
    # smoothing below (ignore_na=True) skips them instead of treating
    # them as flat days, which would skew RSI for subsequent rows.
    gain = delta.where(delta > 0, 0.0).where(delta.notna())
    loss = (-delta).where(delta < 0, 0.0).where(delta.notna())
    df = df.drop(columns=["_delta"])

    # Wilder RSI: EWM alpha=1/W, adjust=False, min_periods=W.
    # Stays on pandas — cuDF lacks grouped-ewm support (the per-group
    # apply fallback is no faster than pandas' vectorized groupby.ewm),
    # so the shared helpers' auto-routing is the right granularity here.
    # ignore_na=True: null-price rows (NaN gain/loss) are skipped by the
    # EWM, so they neither increment the smoothing nor propagate NaN
    # forward — RSI carries the last non-null value through gaps.
    # Group bounds computed once (contiguous (sec_type, code) blocks) and
    # shared by all 6×2 EWM kernel calls below.
    group_bounds = _host_group_bounds(df, grp) if len(df) else None
    for w in rsi_windows:
        avg_gain = _grouped_ewm_pandas(
            gain, df, grp, 1.0 / w, w, ignore_na=True,
            group_bounds=group_bounds,
        )
        avg_loss = _grouped_ewm_pandas(
            loss, df, grp, 1.0 / w, w, ignore_na=True,
            group_bounds=group_bounds,
        )
        df[f"rsi_{w}days"] = _rsi_from_avgs(avg_gain, avg_loss)

    return df


def sanitize_rsi_rows(df: pd.DataFrame) -> list[dict]:
    """Select the mov_ave_rsi columns, apply the NUMERIC(10,6) overflow
    guard, and sanitize for asyncpg bulk upsert (NaN/inf -> None + to_dict).

    Operates on a DataFrame already carrying the rsi_* columns
    (typically filtered to target_dates).
    """
    if df.empty:
        return []

    rsi_cols = [f"rsi_{w}days" for w in RSI_WINDOWS]
    out_cols = ["sec_type", "code", "date"] + rsi_cols
    out = df[out_cols].copy()

    # Overflow guard (safety net; RSI is 0..100).
    nulled = {}
    for c in rsi_cols:
        clean, n = null_if_overflow_counted(out[c])
        out[c] = clean
        if n > 0:
            nulled[c] = n
    if nulled:
        total = sum(nulled.values())
        per = ", ".join(f"{c}={n}" for c, n in nulled.items())
        logger.info(f"    -> NUMERIC(10,6) overflow-guard nulled {total:,} value(s) "
              f"across {len(nulled)} column(s): {per}")

    return sanitize_for_db_insert(
        out,
        numeric_cols=rsi_cols,
        date_cols=["date"],
    )


# ---------------------------------------------------------------------------
#  Shared helpers (work on both pandas and cuDF Series — both support
#  arithmetic, .where, and boolean masks)
# ---------------------------------------------------------------------------

def _rsi_from_avgs(avg_gain, avg_loss):
    """RSI = 100 - 100/(1+RS), with edge-case handling.

    RSI = 100  when avg_loss == 0 and avg_gain > 0 (pure uptrend).
    RSI = 0    when avg_gain == 0 and avg_loss > 0 (pure downtrend).
    RSI = NaN  when both == 0 (flat / undefined).

    Works on pandas and cuDF Series (both support /, where, ==, >, &).
    """
    rs = avg_gain / avg_loss
    rsi = 100.0 - 100.0 / (1.0 + rs)

    up_only = (avg_loss == 0) & (avg_gain > 0)
    rsi = rsi.where(~up_only, 100.0)

    down_only = (avg_gain == 0) & (avg_loss > 0)
    rsi = rsi.where(~down_only, 0.0)

    # flat (avg_gain == 0 AND avg_loss == 0) needs no override: rs = 0/0
    # evaluates to NaN on both backends, so rsi is already NaN there.
    # The former ``rsi.where(~flat, np.nan)`` triggered a cudf
    # MixedTypeError fallback (np.nan as `other` on a nullable column).
    return rsi


# ---------------------------------------------------------------------------
#  Wilder EWM helper (pandas groupby.ewm — vectorized, no per-group callbacks)
#  Stays on pandas: cuDF lacks grouped-ewm support and the per-group apply
#  fallback is no faster than pandas' Cython groupby.ewm. The diff (delta)
#  IS cuDF-accelerated via the shared grouped_diff helper.
# ---------------------------------------------------------------------------

# Below this row count the pandas Cython groupby-ewm beats the device
# round-trip (2 × ~8 B/row transfers + launch) — stay on pandas.
_EWM_GPU_MIN_ROWS = 200_000


def _host_group_bounds(df: pd.DataFrame, grp: list[str]):
    """(starts, ends) int64 host arrays of contiguous group row ranges.

    Caller guarantees df is sorted by [*grp, date] (compute_rsi
    sorts), so groups are contiguous blocks; a row starts a new group
    when any key differs from the previous row. Unwraps the proxy
    ndarrays ONCE at the pandas→numpy boundary (B-A1 rule #1).
    """
    from _common.df_utils import host_array

    n = len(df)
    sec = host_array(df["sec_type"].to_numpy())
    code = host_array(df["code"].to_numpy())
    change = np.empty(n, dtype=np.bool_)
    change[0] = True
    if n > 1:
        np.logical_or(sec[1:] != sec[:-1], code[1:] != code[:-1],
                      out=change[1:])
    pos = np.flatnonzero(change)
    return pos.astype(np.int64), np.append(pos[1:], n).astype(np.int64)


def _grouped_ewm_gpu(s, df, alpha, min_periods, group_bounds):
    """Fused CUDA grouped EWM (adjust=False, ignore_na=True), one launch.

    Transfers only the value column + group bounds to VRAM, runs the
    kernel, and returns a REAL pandas Series (``pd.Series._fsproxy_slow``
    — B-A1 rule #2) aligned positionally to df.index (groups contiguous
    ⇒ kernel output row order == df row order, same contract the pandas
    path's reset_index+reindex achieves).
    """
    import cupy as cp
    from _common.df_utils import host_array
    from analyze.mov_ave_spread._kernels.ewm import grouped_ewm_mean

    x = host_array(
        pd.to_numeric(s, errors="coerce").to_numpy()
    ).astype("float64")
    starts, ends = group_bounds
    dev = grouped_ewm_mean(
        cp.asarray(x), cp.asarray(starts), cp.asarray(ends),
        alpha=alpha, min_periods=min_periods,
    )
    # Unwrap the proxy index ONCE — the REAL pandas Series ctor would
    # otherwise dispatch on the proxy RangeIndex (RangeIndex._typ
    # AttributeError fallback). _fsproxy_slow only exists under the
    # cudf.pandas proxy; plain pandas IS the real class already.
    return getattr(pd.Series, "_fsproxy_slow", pd.Series)(
        dev.get(), index=host_array(df.index)
    )


def _grouped_ewm_pandas(s, df, grp, alpha, min_periods, ignore_na=False,
                        group_bounds=None):
    """Grouped EWM mean (Wilder smoothing) aligned to df.index.

    GPU route: when ``ignore_na=True`` with precomputed contiguous group
    bounds and a large frame, the fused ``ewm.cpp`` kernel replaces the
    pandas Cython ``groupby.ewm`` CPU fallback (32 fallback lines/run on
    the 6.6M-row stock leg); any failure falls back to pandas.

    ``groupby(keys).ewm(alpha, adjust=False, min_periods).mean()`` returns a
    MultiIndex Series (group keys + original index). Strip the group-key
    levels and reindex to df.index to realign.

    When ``ignore_na=True``, NaN values in the input are skipped by the EWM
    (neither incrementing the smoothing nor propagating NaN forward) — used
    so null-price rows don't corrupt RSI for subsequent non-null rows.
    """
    if (
        ignore_na
        and group_bounds is not None
        and len(df) >= _EWM_GPU_MIN_ROWS
    ):
        try:
            return _grouped_ewm_gpu(s, df, alpha, min_periods, group_bounds)
        except Exception as e:  # noqa: BLE001 — any GPU failure → pandas
            global _ewm_gpu_warned
            if not _ewm_gpu_warned:
                _ewm_gpu_warned = True
                logger.warning(
                    f"      [ewm kernel] GPU route failed "
                    f"({type(e).__name__}: {e}) — pandas fallback "
                    f"(further failures suppressed for this run)",
                )
    keys = [df[k] for k in grp]
    res = (
        s.groupby(keys, sort=False)
        .ewm(alpha=alpha, adjust=False, min_periods=min_periods, ignore_na=ignore_na)
        .mean()
    )
    res = res.reset_index(level=list(range(len(grp))), drop=True)
    return res.reindex(df.index)


# ---------------------------------------------------------------------------
#  Pipeline (internal step — invoked from mov_ave_spread.__main__)
# ---------------------------------------------------------------------------

async def run_rsi(
    conn,
    df: pd.DataFrame,
    *,
    force: bool = False,
    pool=None,
    max_concurrent: int = 20,
    sec_type: str | None = None,
    code_filter: str | None = None,
) -> None:
    """Run the Wilder RSI pipeline against the source price data
    already loaded by the parent mov_ave_spread.

    Reuses the caller's DB connection and source DataFrame (the ``price``
    column is reused — no second DB fetch). The DataFrame must contain the
    FULL per-code history (not filtered to target_dates) so Wilder
    smoothing is correct for the first target date of each code.

    Pipeline
      1. Determine target dates (per-sec_type) by checking missing dates
         in analysis.mov_ave_rsi against source identity tables. In force
         mode, truncate the table instead.
      2. Compute Wilder RSI (6 windows) over the FULL per-code history,
         then filter to target_dates.
      3. Upsert into analysis.mov_ave_rsi (chunked by date).
      4. Upsert analysis.analysis_identity registry.

    Args:
      conn: asyncpg connection (reused from parent).
      df: source DataFrame with at least columns [sec_type, code, date,
          price]. Must be the FULL per-code history.
      force: when True, truncate analysis.mov_ave_rsi first and recompute
             all rows.
      pool: optional connection pool for parallel upsert chunks.
      sec_type: when provided, process only this sec_type (parent loop
                passes one sec_type at a time to bound memory). When
                None, infers sec_types from the DataFrame.
    """
    t0 = time.time()
    logger.info("\n" + "=" * 78)
    logger.info("  MOV_AVE_RSI (internal step of mov_ave_spread)")
    logger.info("=" * 78)

    # Select only the columns RSI needs — the parent DataFrame carries
    # many extra columns (OHLC, MAs, slopes, stds) that are irrelevant
    # here. Rows with NULL price are KEPT (not dropped): their RSI
    # columns will be NULL, but keeping them ensures the API LEFT JOIN
    # always finds a mov_ave_rsi row for every detail date.
    rsi_df = df[["sec_type", "code", "date", "price"]].copy()
    n_null_price = int(rsi_df["price"].isna().sum())
    if n_null_price > 0:
        logger.info(f"    note: {n_null_price:,} rows with NULL price will get "
              f"NULL RSI columns")

    if rsi_df.empty:
        logger.info("    -> no source data; skipping RSI step.")
        return

    # Use the sec_type passed by the parent (per-sec_type loop) or infer
    # from the DataFrame for backward compatibility.
    if sec_type is not None:
        sec_types = (sec_type,)
    else:
        sec_types = tuple(host_unique(rsi_df["sec_type"]))

    # ---- Step 0: determine target dates (per-sec_type) --------------
    if code_filter is not None:
        # Single-code mode (--code): the caller already DELETEd this
        # code's rows from the table, so compute ALL dates for this code
        # and bypass the per-sec_type skip-filter (sec_types=() at the
        # insert below keeps every row — dates covered by OTHER codes
        # would otherwise mask this code's missing dates).
        logger.info("    mode: SINGLE-CODE (full recompute for this code)")
        target_dates_union: Optional[Set] = None
    elif force:
        logger.info("    mode: FORCE (full recompute)")
        if sec_type is not None:
            # Per-sec_type scope: DELETE only this sec_type's rows — the
            # parent loop calls run_rsi once per sec_type, so a whole-
            # table TRUNCATE here would wipe the other sec_types' rows
            # (in a --sec-type scoped run they are NOT rebuilt).
            logger.info(f"\n[r0/3] Force mode: deleting {sec_type} rows from "
                  "mov_ave_rsi...")
            status = await conn.execute(
                f"DELETE FROM {RSI_TABLE} WHERE sec_type = $1", sec_type,
            )
            n_del = int(status.rsplit(" ", 1)[-1]) if status else 0
            logger.info(f"    -> deleted {n_del:,} rows; will recompute all "
                  f"{sec_type} rows")
        else:
            logger.info("\n[r0/3] Force mode: truncating mov_ave_rsi...")
            await truncate_table_async(conn, RSI_TABLE)
            logger.info("    -> truncated; will recompute all rows")
        target_dates_union: Optional[Set] = None
    else:
        logger.info("    mode: incremental (missing dates only)")
        logger.info("\n[r0/3] Detecting missing dates PER-sec_type "
              "(etf_identity vs mov_ave_rsi[etf], etc.)...")
        target_dates_per_st: dict = {}
        for st in sec_types:
            td_st = await find_missing_analysis_dates(
                conn, RSI_TABLE,
                [SEC_TYPE_IDENTITY_TABLE[st]], sec_type=st,
            )
            target_dates_per_st[st] = td_st
            logger.info(f"    -> {st}: {len(td_st)} missing dates")
        # Union across sec_types — a date is "to do" if ANY sec_type
        # is missing it.
        target_dates_union = set()
        for s in target_dates_per_st.values():
            target_dates_union |= s
        logger.info(f"    -> union across sec_types: "
              f"{len(target_dates_union)} dates to (re)compute")
        if not target_dates_union:
            logger.info("    -> DB is up to date; nothing to do.")
            return

    # ---- Step 1: compute RSI over full history -----------------------
    logger.info("\n[r1/3] Computing Wilder RSI per (sec_type, code, date) "
          "over full history...")
    rsi_df = compute_rsi(rsi_df)

    if target_dates_union is not None and len(target_dates_union) > 0:
        n_before = len(rsi_df)
        # datetime64 ndarray comparison — isin with a python-date SET
        # never matches a datetime64 column (fetch.py incremental-filter
        # convention).
        td64 = to_dt64(sorted(target_dates_union))
        rsi_df = rsi_df[rsi_df["date"].isin(td64)].reset_index(drop=True)
        logger.info(f"    -> incremental filter: {len(rsi_df):,} of {n_before:,} "
              f"rows are in target_dates_union")

    if rsi_df.empty:
        logger.info("    -> no rows to upsert; skipping RSI upsert.")
        return

    # ---- Step 2: build + insert (chunked by date) -------------------
    # sanitize_rsi_rows materializes one Python dict per row; for the full
    # stock universe (6.7M rows) that is multi-GB and OOMs. Build + insert
    # per date-chunk so peak memory is bounded to one chunk's dicts
    # (~100K rows). Mirrors the parent mov_ave_spread detail step.
    logger.info(f"\n[r2/3] Building + inserting {len(rsi_df):,} mov_ave_rsi rows "
          f"in date-bounded chunks ({'COPY' if force else 'upsert'} per "
          f"chunk)...")
    n = await build_and_insert_chunked(
        conn, pool, rsi_df,
        sanitize_rsi_rows,
        table_name=RSI_TABLE,
        key_columns=["sec_type", "code", "date"],
        force=force,
        sec_types=() if code_filter is not None else sec_types,
        max_concurrent=max_concurrent,
        label="mov_ave_rsi",
    )
    del rsi_df
    logger.info(f"    -> inserted {n:,} rows")

    # ---- Step 3: register in analysis_identity ----------------------
    logger.info(f"\n[r3/3] Upserting analysis.analysis_identity registry...")
    await upsert_analysis_identity(
        conn,
        name=RSI_ANALYSIS_NAME,
        detail_name="mov_ave_rsi",
        description=RSI_DESCRIPTION,
    )

    logger.info(f"\n  mov_ave_rsi wall time: {time.time() - t0:.1f}s")
