"""Orchestration + DB writes for builds.market_hypes.

MIGRATED from analyze.mov_ave_spread.market_hypes (run_market_hypes):
the DELETE-scope / chunked-COPY write path is unchanged (writing
stats.mov_ave_market_hypes instead of analysis.mov_ave_market_hypes);
the analysis_identity registry upsert is dropped (no stats-side
registry — the build is documented by the DDL comments + config).
The fetch is owned by fetch.py instead of the parent pipeline's
DataFrame.

REBUILD SEMANTICS (margin_changes precedent): episode boundaries shift
whenever new dates arrive (the trailing episode extends; the centered
threshold windows move), and non-hyped dates leave no footprint —
date-level coverage cannot be diffed against an episodes table. There
is therefore NO per-date incremental upsert: every run DELETEs the
scope's entire rows (one sec_type — or one code in --code mode, which
is pre-deleted) and recomputes ALL episodes from the FULL per-code
history. --force additionally truncates the table upfront (full-
universe force only — a scoped force keeps other sec_types' rows).
"""
from __future__ import annotations

import asyncio
import time

import pandas as pd

from _common.build_commons import copy_insert_async, truncate_table_async
from _common.df_utils import sanitize_for_db_insert
from builds.market_hypes.config import (
    HYPE_CHECKIN_PERIODS,
    HYPE_CHECKIN_SATISFACTION_THRESHOLD,
    HYPE_STD_THRESHOLD_PCT,
    HYPE_THRESHOLD_HALF_WINDOW_ROWS,
    HYPE_TRADING_AMT_THRESHOLD_PCT,
    MARKET_HYPES_COLUMNS,
    SEC_TYPES,
    TABLE,
)
from builds.market_hypes.compute import compute_market_hypes, hype_episodes
from builds.market_hypes.fetch import fetch_hype_source

import logging
logger = logging.getLogger(__name__)


# Episode rows per COPY chunk (bounds the row-dict list materialized
# between the DataFrame and asyncpg's COPY stream).
_EPISODE_CHUNK_ROWS = 100_000


def sanitize_market_hypes_rows(df: pd.DataFrame) -> list[dict]:
    """Sanitize one chunk of the episodes frame for asyncpg COPY
    (NaN/inf -> None + to_dict).

    The frame must already carry the three recorded build-parameter
    columns (attached by run_market_hypes). The NUMERIC(6,4) parameter
    columns are rounded to 4 decimal places; the date / integer key
    columns pass through.
    """
    if df.empty:
        return []
    numeric_params = [
        "min_checkin_satisfaction_threshold",
        "min_trading_amt_threshold",
        "min_std_threshold",
    ]
    return sanitize_for_db_insert(
        df, numeric_cols=numeric_params, round_to=4,
    )


async def _copy_episodes_chunked(
    conn, pool, episodes: pd.DataFrame, *, max_concurrent: int,
) -> int:
    """COPY-insert the episodes frame in row-count chunks.

    Bounds peak memory like build_and_insert_chunked (never
    materializes the full row-dict list): each ~_EPISODE_CHUNK_ROWS
    slice is sanitized inside the concurrency semaphore, then streamed
    via COPY on ``conn`` (sequential) or on a pool connection
    (parallel). COPY is safe because the caller DELETEd the whole
    scope first — the inserted episodes are guaranteed conflict-free.

    Chunks are row-count slices (not date-bounded): episode rows carry
    no single "date" key and each (sec_type, code, start_date,
    end_date, min_checkin_period) PK appears exactly once in the frame,
    so no two chunks can conflict even under parallel COPY.
    """
    n_total = len(episodes)
    if n_total == 0:
        return 0

    bounds = [
        (lo, min(lo + _EPISODE_CHUNK_ROWS, n_total))
        for lo in range(0, n_total, _EPISODE_CHUNK_ROWS)
    ]
    n_chunks = len(bounds)
    columns = list(MARKET_HYPES_COLUMNS)

    use_parallel = (
        pool is not None and max_concurrent > 1 and n_chunks > 1
    )
    if not use_parallel:
        total = 0
        for i, (lo, hi) in enumerate(bounds, start=1):
            rows = sanitize_market_hypes_rows(episodes.iloc[lo:hi])
            n = await copy_insert_async(
                conn, TABLE, rows, columns=columns,
            )
            total += n
            logger.info(f"      episodes chunk {i}/{n_chunks}: COPY {n:,} rows "
                  f"(cumulative {total:,})")
        return total

    pool_max = getattr(pool, "_maxsize", max_concurrent)
    concurrency = max(1, min(max_concurrent, n_chunks, pool_max))
    sem = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()
    counter = [0]
    logger.info(f"      parallel COPY: {n_chunks} chunks, {concurrency} "
          f"concurrent (pool max_size={pool_max})")

    async def _task(i: int, lo: int, hi: int) -> int:
        # Acquire the semaphore BEFORE building the chunk's dicts so at
        # most ``concurrency`` chunks' rows are in flight (the same
        # memory bound as build_and_insert_chunked).
        async with sem:
            rows = sanitize_market_hypes_rows(episodes.iloc[lo:hi])
            async with pool.acquire() as c:
                n = await copy_insert_async(
                    c, TABLE, rows, columns=columns,
                )
        async with lock:
            counter[0] += n
            so_far = counter[0]
        logger.info(f"      episodes chunk {i}/{n_chunks} done: COPY {n:,} rows "
              f"(cumulative {so_far:,})")
        return n

    results = await asyncio.gather(*[
        _task(i, lo, hi) for i, (lo, hi) in enumerate(bounds, start=1)
    ])
    return sum(results)


async def run_market_hypes(
    conn,
    df: pd.DataFrame,
    *,
    force: bool = False,
    pool=None,
    max_concurrent: int = 20,
    sec_type: str | None = None,
    code_filter: str | None = None,
) -> None:
    """Run the market-hype EPISODE pipeline against the caller's source
    data.

    The DataFrame must contain the FULL per-code history so the centered
    ±10y percentile windows have enough rows on each side of every date
    (up to 2550 per side) — as produced by fetch.fetch_hype_source, or
    (shape-compatible) the mov_ave_spread parent pipeline's source frame
    (columns [sec_type, code, date, trading_amount, std_5days,
    std_20days, std_60days, std_120days, std_255days]).

    Pipeline
      1. Compute the per-window is_hyped + check-in + per-leg flags over
         the FULL per-code history (centered percentile thresholds +
         check-in counts).
      2. Assemble the hyped runs into CONCATENATED episodes (extend
         through the check-in evidence; bucket by span into
         [W, next window)) — start_date / end_date / hype_days /
         trading_amt_hype_days / std_hype_days per window.
      3. DELETE the scope's rows (the given sec_type — or the single
         --code, whose rows the caller already deleted), then
         COPY-insert the recomputed episodes. There is no per-date
         incremental upsert: episode boundaries shift when new dates
         arrive and non-hyped dates leave no footprint
         (margin_changes precedent).

    Args:
      conn: asyncpg connection.
      df: source DataFrame with at least columns [sec_type, code, date,
          trading_amount, std_5days, std_20days, std_60days,
          std_120days, std_255days]. Must be the FULL per-code history
          (the centered ±10y percentile windows need up to 2550 rows on
          EACH side of every date).
      force: accepted for API compatibility — the rebuild is wholesale
          regardless; force only controls the caller-side upfront
          truncate.
      pool: optional connection pool for parallel COPY chunks.
      max_concurrent: maximum parallel COPY chunks.
      sec_type: when provided, process only this sec_type (per-sec_type
                scoping bounds memory). When None, infers sec_types
                from the DataFrame.
      code_filter: single-code mode (--code): rebuild the episodes of
                   this code only.
    """
    t0 = time.time()
    logger.info("\n" + "=" * 78)
    logger.info("  MOV_AVE_MARKET_HYPES (builds.market_hypes)")
    logger.info("=" * 78)

    if code_filter is not None:
        logger.info(f"    mode: SINGLE-CODE (wholesale episode rebuild for "
              f"{code_filter})")
    else:
        logger.info("    mode: WHOLESALE PER-SEC_TYPE (episodes are rebuilt on "
              "every run — new dates shift episode boundaries)")

    if df.empty:
        logger.info("    -> no source data; skipping market-hypes step.")
        return

    if sec_type is not None:
        sec_types = (sec_type,)
    else:
        sec_types = tuple(sorted(df["sec_type"].unique()))

    # ---- Step 1: compute is_hyped per window over full history ------
    logger.info("\n[h1/3] Computing market-hype flags per check-in window "
          f"({', '.join(str(w) for w in HYPE_CHECKIN_PERIODS)} rows; "
          f"centered ±{HYPE_THRESHOLD_HALF_WINDOW_ROWS}-row "
          f"(20y total) percentile thresholds at "
          f"{HYPE_TRADING_AMT_THRESHOLD_PCT:.1f}% amt / "
          f"{HYPE_STD_THRESHOLD_PCT:.1f}% std; satisfaction > "
          f"{HYPE_CHECKIN_SATISFACTION_THRESHOLD:.1f}%)...")
    hype_df = df.sort_values(
        ["sec_type", "code", "date"]
    ).reset_index(drop=True)
    hype_df = compute_market_hypes(hype_df)

    # ---- Step 2: assemble concat/extended/bucketed episodes ---------
    logger.info("\n[h2/3] Assembling hyped episodes (concat through check-in "
          f"evidence; span buckets [W, next window) for "
          f"{', '.join(str(w) for w in HYPE_CHECKIN_PERIODS)} "
          f"windows)...")
    episodes = hype_episodes(hype_df)
    del hype_df
    # Attach the recorded build parameters + fix the table column order.
    episodes = episodes.reindex(columns=list(MARKET_HYPES_COLUMNS))
    episodes["min_checkin_satisfaction_threshold"] = (
        HYPE_CHECKIN_SATISFACTION_THRESHOLD
    )
    episodes["min_trading_amt_threshold"] = HYPE_TRADING_AMT_THRESHOLD_PCT
    episodes["min_std_threshold"] = HYPE_STD_THRESHOLD_PCT
    n_codes = (
        episodes[["sec_type", "code"]].drop_duplicates().shape[0]
        if not episodes.empty
        else 0
    )
    logger.info(f"    -> {len(episodes):,} episodes across {n_codes:,} "
          f"(sec_type, code) groups")

    # ---- Step 3: replace the scope's rows wholesale -----------------
    if code_filter is not None:
        for st in sec_types:
            status = await conn.execute(
                f"DELETE FROM {TABLE} "
                f"WHERE sec_type = $1 AND code = $2",
                st, code_filter,
            )
            n_del = int(status.rsplit(" ", 1)[-1]) if status else 0
            logger.info(f"    -> deleted {n_del:,} existing episode rows "
                  f"({st}/{code_filter})")
    else:
        for st in sec_types:
            status = await conn.execute(
                f"DELETE FROM {TABLE} WHERE sec_type = $1",
                st,
            )
            n_del = int(status.rsplit(" ", 1)[-1]) if status else 0
            logger.info(f"    -> deleted {n_del:,} existing episode rows "
                  f"({st})")

    n = await _copy_episodes_chunked(
        conn, pool, episodes, max_concurrent=max_concurrent,
    )
    del episodes
    logger.info(f"    -> inserted {n:,} episode rows")

    logger.info(f"\n  {TABLE} wall time: {time.time() - t0:.1f}s")


async def run_build(
    conn,
    *,
    force: bool = False,
    sec_types: tuple[str, ...] = SEC_TYPES,
    code_filter: str | None = None,
    max_concurrent: int = 20,
    pool=None,
) -> None:
    """Run the market-hype build over the requested sec_types.

    Per-sec_type loop bounds peak memory (the stock universe's full
    history is the big one). Each sec_type's source is fetched with the
    FULL per-code history the centered ±10y threshold windows require,
    then handed to run_market_hypes (which deletes + rewrites the
    scope's rows wholesale).

    Full-universe --force truncates the table upfront (reset storage);
    a scoped run relies on run_market_hypes' per-scope DELETE so the
    other sec_types' rows are untouched.
    """
    t0 = time.time()
    scope = f"sec_type={','.join(sec_types)}" + (
        f", code={code_filter}" if code_filter else ""
    )
    logger.info("\n" + "=" * 78)
    logger.info("  BUILD MARKET HYPES (stats.mov_ave_market_hypes)")
    logger.info("=" * 78)
    logger.info(f"    scope: {scope}; mode: "
          + ("FORCE (truncate + full recompute)" if force
             else "wholesale per-sec_type recompute"))

    if force and not code_filter and set(sec_types) == set(SEC_TYPES):
        await truncate_table_async(conn, TABLE)
        logger.info("    -> truncated target table")

    for i, st in enumerate(sec_types, start=1):
        logger.info(f"\n[{i}/{len(sec_types)}] sec_type={st}")
        df = await fetch_hype_source(conn, st, code_filter=code_filter)
        logger.info(f"    -> {len(df):,} (code, date) source rows")
        if df.empty:
            logger.info("    -> no source data; skipping.")
            continue
        await run_market_hypes(
            conn, df,
            force=force, pool=pool, max_concurrent=max_concurrent,
            sec_type=st, code_filter=code_filter,
        )
        del df

    logger.info(f"\n  builds.market_hypes wall time: {time.time() - t0:.1f}s")
