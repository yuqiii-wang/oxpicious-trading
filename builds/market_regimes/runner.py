"""Orchestration + DB writes for builds.market_regimes.

The retired builds.market_hypes runner's DELETE-scope / chunked-COPY
write path, writing stats.market_regimes PLUS the derived
stats.market_regime_spans shading table (both wholesale-rebuilt per
scope on every run — margin_changes precedent: ETF adj_close
back-adjustments rewrite price history, so per-date incremental
upserts cannot be trusted). The spans are the gaps-and-islands
collapse of the daily states (the former per-query VIEW, now a
materialized table the /api/analysis/market-regimes endpoint reads).
"""
from __future__ import annotations

import asyncio
import time

import pandas as pd

from _common.build_commons import truncate_table_async
from _common.db_commons import csv_copy_from_frame_async
from builds.market_regimes.config import (
    AMT_BAR,
    AMT_Z_MIN_PERIODS,
    AMT_Z_WINDOW,
    MARKET_REGIME_SPANS_COLUMNS,
    MARKET_REGIMES_COLUMNS,
    SEC_TYPES,
    SPANS_TABLE,
    TABLE,
    VOL_BAR,
    VOL_MIN_PERIODS,
    VOL_SPAN,
    VOL_Z_MIN_PERIODS,
    VOL_Z_WINDOW,
)
from builds.market_regimes.compute import (
    compute_market_regimes,
    compute_regime_spans,
)
from builds.market_regimes.fetch import fetch_regime_source

import logging
logger = logging.getLogger(__name__)

# Registry rows per CSV-COPY chunk (the price_vs_amt precedent — bounds
# the frame slice + rendered CSV bytes held between the DataFrame and
# the COPY stream; the CSV path renders whole columns host-side, so no
# per-row dict list is ever materialized).
_CHUNK_ROWS = 200_000


async def _copy_rows_chunked(
    conn, pool, rows_df: pd.DataFrame, *, table: str,
    columns: tuple[str, ...], label: str, max_concurrent: int,
) -> int:
    """COPY-insert a frame in row-count chunks (the price_vs_amt
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
    columns = list(columns)

    use_parallel = (
        pool is not None and max_concurrent > 1 and n_chunks > 1
    )
    if not use_parallel:
        total = 0
        for i, (lo, hi) in enumerate(bounds, start=1):
            n = await csv_copy_from_frame_async(
                conn, table, rows_df.iloc[lo:hi], columns=columns,
            )
            total += n
            logger.info(f"      {label} chunk {i}/{n_chunks}: "
                  f"COPY {n:,} rows (cumulative {total:,})")
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
                    c, table, rows_df.iloc[lo:hi], columns=columns,
                )
        async with lock:
            counter[0] += n
            so_far = counter[0]
        logger.info(f"      {label} chunk {i}/{n_chunks} done: "
              f"COPY {n:,} rows (cumulative {so_far:,})")
        return n

    results = await asyncio.gather(*[
        _task(i, lo, hi) for i, (lo, hi) in enumerate(bounds, start=1)
    ])
    return sum(results)


async def run_market_regimes(
    conn,
    df: pd.DataFrame,
    *,
    force: bool = False,
    pool=None,
    max_concurrent: int = 20,
    sec_type: str | None = None,
    code_filter: str | None = None,
) -> None:
    """Run the market-REGIME state pipeline against the caller's source
    data (the FULL per-code history — fetch_regime_source's shape).

    Pipeline
      1. compute_market_regimes: ret_1d -> EWMA vol -> vol_z / amt_z
         (trailing moments, shift 1) -> the 4-state label per row.
      2. Attach the recorded build-parameter columns.
      3. compute_regime_spans: collapse the daily states into their
         contiguous same-regime runs (stats.market_regime_spans).
      4. DELETE the scope's rows from BOTH tables (the given sec_type —
         or the single --code), then COPY-insert the recomputed rows.
    """
    t0 = time.time()
    logger.info("\n" + "=" * 78)
    logger.info("  MARKET_REGIMES (builds.market_regimes)")
    logger.info("=" * 78)

    if code_filter is not None:
        logger.info(f"    mode: SINGLE-CODE (wholesale registry rebuild "
              f"for {code_filter})")
    else:
        logger.info("    mode: WHOLESALE PER-SEC_TYPE (registry rows are "
              "rebuilt on every run — adj_close back-adjustments "
              "rewrite price history)")

    if df.empty:
        logger.info("    -> no source data; skipping market-regimes step.")
        return

    if sec_type is not None:
        sec_types = (sec_type,)
    else:
        sec_types = tuple(sorted(df["sec_type"].unique()))

    # ---- Step 1: compute the regime states over full history ----------
    logger.info("\n[mr1/4] Computing regime states (EWMA vol span "
          f"{VOL_SPAN}, z windows {VOL_Z_WINDOW}/{AMT_Z_WINDOW} "
          f"(min {VOL_Z_MIN_PERIODS}/{AMT_Z_MIN_PERIODS}), bars "
          f"vol_z > {VOL_BAR} / amt_z > {AMT_BAR})...")
    out = compute_market_regimes(
        df.sort_values(["sec_type", "code", "date"]).reset_index(drop=True)
    )

    # ---- Step 2: recorded build parameters + column order -------------
    out = out.reindex(columns=list(MARKET_REGIMES_COLUMNS))
    out["vol_span"] = VOL_SPAN
    out["vol_min_periods"] = VOL_MIN_PERIODS
    out["vol_z_window"] = VOL_Z_WINDOW
    out["vol_z_min_periods"] = VOL_Z_MIN_PERIODS
    out["amt_z_window"] = AMT_Z_WINDOW
    out["amt_z_min_periods"] = AMT_Z_MIN_PERIODS
    out["vol_bar"] = VOL_BAR
    out["amt_bar"] = AMT_BAR
    # NUMERIC(6,4) parameter columns: round at the write boundary.
    out["vol_bar"] = out["vol_bar"].round(4)
    out["amt_bar"] = out["amt_bar"].round(4)

    n_codes = out[["sec_type", "code"]].drop_duplicates().shape[0]
    cov = out["regime"].value_counts(normalize=True) * 100
    logger.info(f"[mr2/4] {len(out):,} state rows across {n_codes:,} "
          f"(sec_type, code) groups — coverage: "
          + " ".join(f"{r}={cov.get(r, 0.0):.1f}%" for r in
                     ("calm", "hot", "panic", "quiet")))

    # ---- Step 3: collapse the daily states into contiguous spans -----
    spans = compute_regime_spans(out)
    spans_per_code = len(spans) / n_codes if n_codes else 0.0
    logger.info(f"[mr3/4] {len(spans):,} regime spans "
          f"({spans_per_code:.0f}/code avg) for stats.market_regime_spans")

    # ---- Step 4: replace the scope's rows wholesale (both tables) -----
    for st in sec_types:
        if code_filter is not None:
            status = await conn.execute(
                f"DELETE FROM {TABLE} "
                f"WHERE sec_type = $1 AND code = $2",
                st, code_filter,
            )
            spans_status = await conn.execute(
                f"DELETE FROM {SPANS_TABLE} "
                f"WHERE sec_type = $1 AND code = $2",
                st, code_filter,
            )
        else:
            status = await conn.execute(
                f"DELETE FROM {TABLE} WHERE sec_type = $1", st,
            )
            spans_status = await conn.execute(
                f"DELETE FROM {SPANS_TABLE} WHERE sec_type = $1", st,
            )
        n_del = int(status.rsplit(" ", 1)[-1]) if status else 0
        n_spans_del = (
            int(spans_status.rsplit(" ", 1)[-1]) if spans_status else 0
        )
        logger.info(f"[mr4/4] deleted {n_del:,} existing state rows + "
              f"{n_spans_del:,} span rows ({st})")

    n = await _copy_rows_chunked(
        conn, pool, out, table=TABLE, columns=MARKET_REGIMES_COLUMNS,
        label="market_regimes", max_concurrent=max_concurrent,
    )
    del out
    logger.info(f"    -> inserted {n:,} state rows")

    n_spans = await _copy_rows_chunked(
        conn, pool, spans, table=SPANS_TABLE,
        columns=MARKET_REGIME_SPANS_COLUMNS,
        label="market_regime_spans", max_concurrent=max_concurrent,
    )
    del spans
    logger.info(f"    -> inserted {n_spans:,} span rows")
    logger.info(f"\n  {TABLE} + {SPANS_TABLE} wall time: "
          f"{time.time() - t0:.1f}s")


async def run_build(
    conn,
    *,
    force: bool = False,
    sec_types: tuple[str, ...] = SEC_TYPES,
    code_filter: str | None = None,
    max_concurrent: int = 20,
    pool=None,
) -> None:
    """Run the market-regimes build over the requested sec_types.

    Per-sec_type loop bounds peak memory (the stock universe's full
    history is the big one). Full-universe --force truncates BOTH
    tables (registry + derived spans) upfront; a scoped run relies on
    the per-scope DELETE so the other sec_types' rows are untouched.
    """
    t0 = time.time()
    scope = f"sec_type={','.join(sec_types)}" + (
        f", code={code_filter}" if code_filter else ""
    )
    logger.info("\n" + "=" * 78)
    logger.info("  BUILD MARKET REGIMES (stats.market_regimes)")
    logger.info("=" * 78)
    logger.info(f"    scope: {scope}; mode: "
          + ("FORCE (truncate + full recompute)" if force
             else "wholesale per-sec_type recompute"))

    if force and not code_filter and set(sec_types) == set(SEC_TYPES):
        await truncate_table_async(conn, TABLE)
        await truncate_table_async(conn, SPANS_TABLE)
        logger.info("    -> truncated target tables (registry + spans)")

    for i, st in enumerate(sec_types, start=1):
        logger.info(f"\n[{i}/{len(sec_types)}] sec_type={st}")
        df = await fetch_regime_source(conn, st, code_filter=code_filter)
        logger.info(f"    -> {len(df):,} (code, date) source rows")
        if df.empty:
            logger.info("    -> no source data; skipping.")
            continue
        await run_market_regimes(
            conn, df,
            force=force, pool=pool, max_concurrent=max_concurrent,
            sec_type=st, code_filter=code_filter,
        )
        del df

    logger.info(f"\n  builds.market_regimes wall time: "
          f"{time.time() - t0:.1f}s")
