"""Internal price-vs-amt step for analyze.mov_ave_spread.

Per-(code, date) Price × Trading-Amount STATE registry into
analysis.mov_ave_price_vs_amt: every day whose state inputs are valid
joins EXACTLY ONE of the 15 price-speed × amount-state categories.

=======================================================================
  FINANCIAL SEMANTICS (the px_vol family's date-level source of truth)
=======================================================================

A (code, date) gets a row when BOTH legs are computable with
information available at that day (every rolling stat shifted 1 row →
no look-ahead). The category definitions are the analysis_forecasts
px_vol engine's VERBATIM — this module imports
``add_px_vol_features`` + the ``PX_VOL_*`` constants from
analyze.analysis_forecasts (single source of truth), so the registry
audits 1:1 against the forecast buckets and the signal detections:

  t = ret_1d / σ_ret(code, 255 rows ending t-1)   (rolling sample std
      ddof=1, min_periods 60, shifted 1 row; σ below sigma_floor 0.005
      excludes bond-like codes)
      sharp_up t > 2.0 | slow_up 1.26 < t <= 2.0 | flat
      | slow_dn -2.0 <= t < -1.29 | sharp_dn t < -2.0

  z = (amt_ratio - μ) / σ   where amt_ratio = trading_amount[t] /
      mean(trading_amount, t-5..t-1) (classic, excludes today) and
      μ/σ are the rolling 255-row (min_periods 60, ddof=1) moments of
      amt_ratio, shifted 1 row
      heavy z > 2.0 | normal | shrink z < -0.92

The row also records the state evidence (px_t / px_z / ret_1d /
px_sigma / amt_ratio) so consumers (the forecast buckets' mean_t /
mean_z config, the signal params JSON, the UI tooltips) recompute
nothing, and the build-parameter set (sigma_window / lb_window /
k_* / z_* / sigma_floor) for consumer-side verification.

Consumers:
  - analysis_forecasts.compute_px_vol — bucket membership + mean_t /
    mean_z (bucket AGGREGATES stay in analysis_forecasts.px_vol_state;
    the dates live here).
  - analysis_signals.signals px_vol — per-day signal detections.
  - data_viz MA-Spread panel — the Px-Vol States date shading.

Source: NOT the parent DataFrame's price column — the parent's etf /
index rows include ESTIMATED closes that the forecast engine filters
out, and the registry must classify on exactly the price series the
engine consumes. Each sec_type's price + trading_amount series is
fetched via fetch_price_vs_amt_source (analyze.analysis_forecasts
.fetch — the engine's price conventions verbatim: ETF =
COALESCE(adj_close, close); estimated closes excluded), scoped to the
parent's active-code universe.

This module is an INTERNAL step of analyze.mov_ave_spread — invoked
from __main__.py right after the trading-amt-ratios step, reusing the
same DB connection.

REBUILD SEMANTICS (margin_changes precedent): ETF adj_close
back-adjustments (dividends) rewrite the whole price history, so
per-date incremental upserts cannot be trusted — every run DELETEs the
step's entire scope (one sec_type — or one code in --code mode) and
recomputes ALL rows from the FULL per-code history.

Force mode (``force=True``): API-compatible extra — the parent's
--force truncates the table upfront; the step's scoped DELETE already
guarantees a clean slate for its scope either way.
"""
from __future__ import annotations

import asyncio
import time

import numpy as np
import pandas as pd

from _common.build_commons import copy_insert_async
from _common.df_utils import column_subset
from analyze._common import (
    sanitize_for_db_insert,
    upsert_analysis_identity,
)
from analyze.analysis_forecasts.config import (
    PX_VOL_K_SHARP,
    PX_VOL_K_SLOW_DN,
    PX_VOL_K_SLOW_UP,
    PX_VOL_LB_WINDOW,
    PX_VOL_SIGMA_FLOOR,
    PX_VOL_SIGMA_WINDOW,
    PX_VOL_SPEED_SIDE,
    PX_VOL_SPEEDS,
    PX_VOL_VOL_STATES,
    PX_VOL_Z_HEAVY,
    PX_VOL_Z_SHRINK,
)
from analyze.analysis_forecasts.fetch import (
    add_px_vol_features,
    fetch_price_vs_amt_source,
)
from analyze.mov_ave_spread.config import (
    PRICE_VS_AMT_ANALYSIS_NAME,
    PRICE_VS_AMT_COLUMNS,
    PRICE_VS_AMT_DESCRIPTION,
    PRICE_VS_AMT_TABLE,
)

import logging
logger = logging.getLogger(__name__)

# Speeds → ordinal (PX_VOL_SPEEDS order) and vols → ordinal
# (PX_VOL_VOL_STATES order) for the vectorized classification.
_SPEED_IDX = {s: i for i, s in enumerate(PX_VOL_SPEEDS)}
_VOL_IDX = {v: i for i, v in enumerate(PX_VOL_VOL_STATES)}

# Rows per COPY chunk (margin_changes precedent — bounds the row-dict
# list materialized between the DataFrame and asyncpg's COPY stream).
_CHUNK_ROWS = 200_000


def classify_price_vs_amt(df: pd.DataFrame) -> pd.DataFrame:
    """Classify the add_px_vol_features outputs into the 15 categories.

    Input: a (sec_type, code, date)-sorted frame carrying the px_t /
    px_z / ret_1d / px_sigma / amt_ratio columns (NaN where the day has
    no valid state). Days with NaN on either leg are DROPPED (no row —
    the speed bands and vol bands are each exhaustive over the reals,
    so every fully-valid day lands in exactly one category).

    Returns a frame with PRICE_VS_AMT_COLUMNS' data fields (sec_type /
    code / date / px_speed / vol_state / side / state values); the
    recorded build-parameter columns are attached by run_price_vs_amt.
    """
    out_cols = [
        "sec_type", "code", "date",
        "px_speed", "vol_state", "side",
        "px_t", "px_z", "ret_1d", "px_sigma", "amt_ratio",
    ]
    valid = df["px_t"].notna() & df["px_z"].notna()
    if not valid.any():
        return pd.DataFrame(columns=out_cols)
    df = df.loc[valid].reset_index(drop=True)

    # Host numpy classification (unwrap at the pandas→numpy boundary —
    # the cudf.pandas proxy would dispatch every scalar compare).
    t = df["px_t"].to_numpy(dtype="float64", copy=True)
    z = df["px_z"].to_numpy(dtype="float64", copy=True)
    # Speed bands are exhaustive over the reals (px_t is NaN-free here).
    speed_ord = np.select(
        [
            t > PX_VOL_K_SHARP,
            (t > PX_VOL_K_SLOW_UP) & (t <= PX_VOL_K_SHARP),
            (t >= -PX_VOL_K_SLOW_DN) & (t <= PX_VOL_K_SLOW_UP),
            (t >= -PX_VOL_K_SHARP) & (t < -PX_VOL_K_SLOW_DN),
        ],
        [
            _SPEED_IDX["sharp_up"],
            _SPEED_IDX["slow_up"],
            _SPEED_IDX["flat"],
            _SPEED_IDX["slow_dn"],
        ],
        default=_SPEED_IDX["sharp_dn"],
    )
    vol_ord = np.select(
        [z > PX_VOL_Z_HEAVY, z >= PX_VOL_Z_SHRINK],
        [_VOL_IDX["heavy"], _VOL_IDX["normal"]],
        default=_VOL_IDX["shrink"],
    )
    speeds = np.asarray(PX_VOL_SPEEDS, dtype=object)[speed_ord]
    vols = np.asarray(PX_VOL_VOL_STATES, dtype=object)[vol_ord]
    sides = np.asarray(
        [PX_VOL_SPEED_SIDE[s] for s in PX_VOL_SPEEDS], dtype=object,
    )[speed_ord]

    # State-value columns first (the category columns are attached
    # below — they don't exist on the input frame).
    out = df[[
        "sec_type", "code", "date",
        "px_t", "px_z", "ret_1d", "px_sigma", "amt_ratio",
    ]].copy()
    out["px_speed"] = speeds
    out["vol_state"] = vols
    out["side"] = sides
    return out


def sanitize_price_vs_amt_rows(df: pd.DataFrame) -> list[dict]:
    """Sanitize one chunk of the registry frame for asyncpg COPY
    (NaN/inf -> None + to_dict). The NUMERIC(4,2)/NUMERIC(6,4) parameter
    columns are rounded; the state values pass through as float8."""
    if df.empty:
        return []
    return sanitize_for_db_insert(
        df,
        numeric_cols=["k_slow_up", "k_slow_dn", "k_sharp",
                      "z_heavy", "z_shrink", "sigma_floor"],
        round_to=4,
    )


async def _copy_rows_chunked(
    conn, pool, rows_df: pd.DataFrame, *, max_concurrent: int,
) -> int:
    """COPY-insert the registry frame in row-count chunks (market_hypes
    precedent — the caller DELETEd the whole scope first, so the
    inserted rows are guaranteed conflict-free)."""
    n_total = len(rows_df)
    if n_total == 0:
        return 0

    bounds = [
        (lo, min(lo + _CHUNK_ROWS, n_total))
        for lo in range(0, n_total, _CHUNK_ROWS)
    ]
    n_chunks = len(bounds)
    columns = list(PRICE_VS_AMT_COLUMNS)

    use_parallel = (
        pool is not None and max_concurrent > 1 and n_chunks > 1
    )
    if not use_parallel:
        total = 0
        for i, (lo, hi) in enumerate(bounds, start=1):
            rows = sanitize_price_vs_amt_rows(rows_df.iloc[lo:hi])
            n = await copy_insert_async(
                conn, PRICE_VS_AMT_TABLE, rows, columns=columns,
            )
            total += n
            logger.info(f"      price_vs_amt chunk {i}/{n_chunks}: COPY {n:,} "
                  f"rows (cumulative {total:,})")
        return total

    pool_max = getattr(pool, "_maxsize", max_concurrent)
    concurrency = max(1, min(max_concurrent, n_chunks, pool_max))
    sem = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()
    counter = [0]
    logger.info(f"      parallel COPY: {n_chunks} chunks, {concurrency} "
          f"concurrent (pool max_size={pool_max})")

    async def _task(i: int, lo: int, hi: int) -> int:
        async with sem:
            rows = sanitize_price_vs_amt_rows(rows_df.iloc[lo:hi])
            async with pool.acquire() as c:
                n = await copy_insert_async(
                    c, PRICE_VS_AMT_TABLE, rows, columns=columns,
                )
        async with lock:
            counter[0] += n
            so_far = counter[0]
        logger.info(f"      price_vs_amt chunk {i}/{n_chunks} done: COPY "
              f"{n:,} rows (cumulative {so_far:,})")
        return n

    results = await asyncio.gather(*[
        _task(i, lo, hi) for i, (lo, hi) in enumerate(bounds, start=1)
    ])
    return sum(results)


# ---------------------------------------------------------------------------
#  Pipeline (internal step — invoked from mov_ave_spread.__main__)
# ---------------------------------------------------------------------------

async def run_price_vs_amt(
    conn,
    df: pd.DataFrame,
    *,
    force: bool = False,
    pool=None,
    max_concurrent: int = 20,
    sec_type: str | None = None,
    code_filter: str | None = None,
) -> None:
    """Run the price-vs-amt STATE registry pipeline.

    The registry must classify on EXACTLY the price series the forecast
    engine consumes — so the source frame is NOT the parent's DataFrame
    (whose etf/index rows include estimated closes that the forecast
    engine filters out): each sec_type's price + trading_amount series
    is fetched via fetch_price_vs_amt_source (the forecast engine's
    fetch conventions verbatim), scoped to the parent DataFrame's
    active-code universe. The frame is the FULL per-code history: the
    states are trailing-window stats (shifted 1 row), so new dates
    never change past rows — but ETF adj_close back-adjustments DO,
    hence the wholesale rebuild.

    Pipeline
      1. Per sec_type: fetch the clean source frame, run
         add_px_vol_features (the forecast engine's feature layer,
         imported verbatim), then classify every fully-valid day into
         its px_speed × vol_state category.
      2. DELETE the step's entire scope (the given sec_type — or the
         single --code, whose rows the caller already deleted), then
         COPY-insert the recomputed rows.
      3. Upsert analysis.analysis_identity registry.

    Args:
      conn: asyncpg connection (reused from parent).
      df: the parent's source DataFrame — only its (sec_type, code)
          universe is used (the active codes with recent data).
      force: accepted for API compatibility with the other internal
          steps — the rebuild is wholesale regardless.
      pool: optional connection pool for parallel COPY chunks.
      max_concurrent: maximum parallel COPY chunks.
      sec_type: when provided, process only this sec_type (parent loop
                passes one sec_type at a time to bound memory).
      code_filter: single-code mode (--code): rebuild this code only.
    """
    t0 = time.time()
    logger.info("\n" + "=" * 78)
    logger.info("  MOV_AVE_PRICE_VS_AMT (internal step of mov_ave_spread)")
    logger.info("=" * 78)

    if code_filter is not None:
        logger.info(f"    mode: SINGLE-CODE (wholesale registry rebuild for "
              f"{code_filter})")
    elif force:
        logger.info("    mode: FORCE (wholesale registry rebuild; the parent "
              "truncated the table upfront)")
    else:
        logger.info("    mode: WHOLESALE PER-SEC_TYPE (registry rows are "
              "rebuilt on every run — adj_close back-adjustments rewrite "
              "price history)")

    needed_cols = column_subset(df, ["sec_type", "code"])
    if df[needed_cols].empty:
        logger.info("    -> no source data; skipping price-vs-amt step.")
        return

    if sec_type is not None:
        sec_types = (sec_type,)
    else:
        sec_types = tuple(sorted(df["sec_type"].unique()))

    # ---- Step 1: fetch clean source + features + classification ------
    # add_px_vol_features groups by ``code`` — codes are unique within
    # one sec_type, so one frame per sec_type.
    logger.info(f"\n[p1/2] Fetching the forecast-engine price/amt series + "
          f"classifying the 15 categories (σ-window {PX_VOL_SIGMA_WINDOW} "
          f"rows, 量比 base {PX_VOL_LB_WINDOW}, bars t ±{PX_VOL_K_SHARP}/±"
          f"{PX_VOL_K_SLOW_UP}/{PX_VOL_K_SLOW_DN}, z +{PX_VOL_Z_HEAVY}/"
          f"{PX_VOL_Z_SHRINK}, σ floor {PX_VOL_SIGMA_FLOOR})...")
    parts: list[pd.DataFrame] = []
    for st in sec_types:
        codes = (
            df.loc[df["sec_type"] == st, "code"].unique().tolist()
            if code_filter is None
            else [code_filter]
        )
        if not codes:
            continue
        src = await fetch_price_vs_amt_source(conn, st, codes)
        if src.empty:
            logger.info(f"    [{st}] no source rows; skipped")
            continue
        src = src.sort_values(["code", "date"]).reset_index(drop=True)
        src = add_px_vol_features(src)
        parts.append(classify_price_vs_amt(src))
        logger.info(f"    [{st}] {len(src):,} feature rows -> "
              f"{len(parts[-1]):,} state rows")
        del src
    if not parts:
        logger.info("    -> no state rows; nothing to write.")
        return
    rows_df = pd.concat(parts, ignore_index=True)
    del parts

    # Attach the recorded build parameters + fix the column order.
    rows_df = rows_df.reindex(columns=list(PRICE_VS_AMT_COLUMNS))
    rows_df["sigma_window"] = PX_VOL_SIGMA_WINDOW
    rows_df["lb_window"] = PX_VOL_LB_WINDOW
    rows_df["k_slow_up"] = PX_VOL_K_SLOW_UP
    rows_df["k_slow_dn"] = PX_VOL_K_SLOW_DN
    rows_df["k_sharp"] = PX_VOL_K_SHARP
    rows_df["z_heavy"] = PX_VOL_Z_HEAVY
    rows_df["z_shrink"] = PX_VOL_Z_SHRINK
    rows_df["sigma_floor"] = PX_VOL_SIGMA_FLOOR

    n_codes = rows_df[["sec_type", "code"]].drop_duplicates().shape[0]
    logger.info(f"    -> {len(rows_df):,} state rows across {n_codes:,} "
          f"(sec_type, code) groups")

    # ---- Step 2: replace the scope's rows wholesale -------------------
    if code_filter is not None:
        for st in sec_types:
            status = await conn.execute(
                f"DELETE FROM {PRICE_VS_AMT_TABLE} "
                f"WHERE sec_type = $1 AND code = $2",
                st, code_filter,
            )
            n_del = int(status.rsplit(" ", 1)[-1]) if status else 0
            logger.info(f"    -> deleted {n_del:,} existing state rows "
                  f"({st}/{code_filter})")
    else:
        for st in sec_types:
            status = await conn.execute(
                f"DELETE FROM {PRICE_VS_AMT_TABLE} WHERE sec_type = $1",
                st,
            )
            n_del = int(status.rsplit(" ", 1)[-1]) if status else 0
            logger.info(f"    -> deleted {n_del:,} existing state rows "
                  f"({st})")

    n = await _copy_rows_chunked(
        conn, pool, rows_df, max_concurrent=max_concurrent,
    )
    del rows_df
    logger.info(f"    -> inserted {n:,} state rows")

    # ---- Step 3: register in analysis_identity ------------------------
    logger.info(f"\n[p2/2] Upserting analysis.analysis_identity registry...")
    await upsert_analysis_identity(
        conn,
        name=PRICE_VS_AMT_ANALYSIS_NAME,
        detail_name="mov_ave_price_vs_amt",
        description=PRICE_VS_AMT_DESCRIPTION,
    )

    logger.info(f"\n  mov_ave_price_vs_amt wall time: "
          f"{time.time() - t0:.1f}s")
