"""Internal price-vs-amt step for analyze.mov_ave_spread.

Per-(code, date) Price × Trading-Amount STATE registry into
analysis.mov_ave_price_vs_amt: every day whose state inputs are valid
joins EXACTLY ONE of the 15 price-speed × amount-state categories.

=======================================================================
  FINANCIAL SEMANTICS (the px_vol family's date-level source of truth)
=======================================================================

A (code, date) gets a row when BOTH legs are computable with
information available at that day (every rolling stat shifted 1 row →
no look-ahead). The category definitions are THIS package's —
``add_px_vol_features`` (px_vol.py) + the ``PX_VOL_*`` calibration
constants (config.py), recorded on every row so consumers can verify
what they read:

  t = ret_1d / σ_ret(code, 255 rows ending t-1)   (rolling sample std
      ddof=1, min_periods 60, shifted 1 row; σ below sigma_floor 0.005
      excludes bond-like codes)
      sharp_up t > 2.0 | slow_up 1.26 < t <= 2.0 | flat
      | slow_dn -2.0 <= t < -1.29 | sharp_dn t < -2.0

  z = (log(trading_amount[t]) - μ) / σ   the z-scored log AMOUNT LEVEL
      — what heavy/shrink (Amt Up/Down) claim: the day's amount vs the
      code's OWN trailing-year amount distribution. μ/σ are the
      rolling 255-row (min_periods 60, ddof=1) moments of
      log(trading_amount), shifted 1 row.
      heavy z > 2.0 | normal | shrink z < -0.92
      (The RETIRED 量比-ratio z — amt_ratio = amount / its own 5-day
      trailing mean, z vs the ratio's trailing moments — fired heavy
      on drought bounces: in a declining-volume regime the 5-day base
      collapses, so a day whose amount sat far below the code's level
      scored ratio ≈ 1.7 → z > 2. amt_ratio stays recorded as
      evidence only; amt_metric='log_level' guards consumers against
      stale registries.)

The row also records the state evidence (px_t / px_z / ret_1d /
px_sigma / amt_ratio) so consumers (the UI tooltips) recompute
nothing, and the build-parameter set (sigma_window / lb_window /
k_* / z_* / sigma_floor / amt_metric) for consumer-side verification.

Consumers:
  - data_viz MA-Spread panel — the Px-Vol States date shading.

Source: NOT the parent DataFrame's price column — the parent's etf /
index rows include ESTIMATED closes that would pollute ret_1d / σ_ret.
Each sec_type's price + trading_amount series is fetched via
fetch_price_vs_amt_source (px_vol.py — the shared per-sec_type price
conventions: ETF = COALESCE(adj_close, close); estimated closes
excluded), scoped to the parent's active-code universe.

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

from _common.db_commons import csv_copy_from_frame_async
from _common.df_utils import column_subset, host_unique
from _common.db_commons import chunked_purge_async
from analyze._common import upsert_analysis_identity
from analyze.mov_ave_spread.config import (
    PRICE_VS_AMT_ANALYSIS_NAME,
    PRICE_VS_AMT_COLUMNS,
    PRICE_VS_AMT_DESCRIPTION,
    PRICE_VS_AMT_TABLE,
    PX_VOL_AMT_METRIC,
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
from analyze.mov_ave_spread.px_vol import (
    add_px_vol_features,
    fetch_price_vs_amt_source,
)

import logging
logger = logging.getLogger(__name__)

# Speeds → ordinal (PX_VOL_SPEEDS order) and vols → ordinal
# (PX_VOL_VOL_STATES order) for the vectorized classification.
_SPEED_IDX = {s: i for i, s in enumerate(PX_VOL_SPEEDS)}
_VOL_IDX = {v: i for i, v in enumerate(PX_VOL_VOL_STATES)}

# Rows per COPY chunk (margin_changes precedent — bounds the frame slice
# + rendered CSV bytes held between the DataFrame and the COPY stream;
# the CSV path renders whole columns host-side, so no per-row dict list
# is ever materialized).
_CHUNK_ROWS = 200_000

# The recorded build-parameter columns (rounded to 4 dp at the write
# boundary — NUMERIC(4,2)/NUMERIC(6,4) parity with the former dict path).
# The state-value columns need NO prep: the CSV writer renders NaN as an
# empty field (SQL NULL) and ±inf as 'inf' / '-inf', which DOUBLE
# PRECISION COPY accepts — identical to the former dict path, where the
# state columns were NOT in numeric_cols (only NaN swept to None there;
# inf passed through to float8).
_PARAM_COLS = (
    "k_slow_up", "k_slow_dn", "k_sharp", "z_heavy", "z_shrink",
    "sigma_floor",
)


def prep_price_vs_amt_frame(rows_df: pd.DataFrame) -> pd.DataFrame:
    """Prepare the registry frame for the CSV COPY boundary (in place).

    Rounds the NUMERIC(4,2)/NUMERIC(6,4) parameter columns to 4 dp —
    the only transform the former sanitize_for_db_insert dict path
    applied beyond the NaN→NULL sweep the CSV writer now handles.
    """
    param_cols = list(_PARAM_COLS)
    rows_df[param_cols] = rows_df[param_cols].round(4)
    return rows_df


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


async def _copy_rows_chunked(
    conn, pool, rows_df: pd.DataFrame, *, max_concurrent: int,
) -> int:
    """COPY-insert the registry frame in row-count chunks (market_hypes
    precedent — the caller DELETEd the whole scope first, so the
    inserted rows are guaranteed conflict-free).

    CSV COPY (csv_copy_from_frame_async): whole-column host rendering —
    no per-row dict list, ~10x less client CPU than the binary record
    path. ``prep_price_vs_amt_frame`` ran once on the whole frame
    upstream, so chunks are plain ``.iloc`` slices.
    """
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
            n = await csv_copy_from_frame_async(
                conn, PRICE_VS_AMT_TABLE, rows_df.iloc[lo:hi],
                columns=columns,
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
            async with pool.acquire() as c:
                n = await csv_copy_from_frame_async(
                    c, PRICE_VS_AMT_TABLE, rows_df.iloc[lo:hi],
                    columns=columns,
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

    The registry must classify on EXACTLY the analysis_forecasts input
    price series — so the source frame is NOT the parent's DataFrame
    (whose etf/index rows include estimated closes that would pollute
    ret_1d / σ_ret): each sec_type's price + trading_amount series
    is fetched via fetch_price_vs_amt_source (the shared per-sec_type
    price conventions), scoped to the parent DataFrame's
    active-code universe. The frame is the FULL per-code history: the
    states are trailing-window stats (shifted 1 row), so new dates
    never change past rows — but ETF adj_close back-adjustments DO,
    hence the wholesale rebuild.

    Pipeline
      1. Per sec_type: fetch the clean source frame, run
         add_px_vol_features (this package's feature layer), then
         classify every fully-valid day into
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
        sec_types = tuple(sorted(set(host_array(
            df["sec_type"].to_numpy()
        ).tolist())))

    # ---- Step 1: fetch clean source + features + classification ------
    # add_px_vol_features groups by ``code`` — codes are unique within
    # one sec_type, so one frame per sec_type.
    logger.info(f"\n[p1/2] Fetching the forecast-engine price/amt series + "
          f"classifying the 15 categories (σ-window {PX_VOL_SIGMA_WINDOW} "
          f"rows, vol leg {PX_VOL_AMT_METRIC} (量比 base "
          f"{PX_VOL_LB_WINDOW} evidence-only), bars t ±{PX_VOL_K_SHARP}/±"
          f"{PX_VOL_K_SLOW_UP}/{PX_VOL_K_SLOW_DN}, z +{PX_VOL_Z_HEAVY}/"
          f"{PX_VOL_Z_SHRINK}, σ floor {PX_VOL_SIGMA_FLOOR})...")
    parts: list[pd.DataFrame] = []
    for st in sec_types:
        if code_filter is None:
            codes = host_unique(df.loc[df["sec_type"] == st, "code"])
        else:
            codes = [code_filter]
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
    rows_df["amt_metric"] = PX_VOL_AMT_METRIC
    # CSV-COPY boundary prep: round the parameter columns + null
    # non-finite state values (once, frame-level — before chunking).
    rows_df = prep_price_vs_amt_frame(rows_df)

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
            n_del = await chunked_purge_async(
                conn, PRICE_VS_AMT_TABLE,
                where_sql="sec_type = $1", params=(st,),
            )
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
