"""Pair-grain orchestrator — routes to the insert pipeline or the corr fast path.

Ported from analyze.sec_alloc_perf_attribution.compute._orchestrator
(2026-09-04); write target hardwired to stats.cross_stats.

  - INSERT mode (``with_corr=False``, default — the main pipeline):
    per-subject merge pipeline (weights + ETF + MA5 ratio) writing FULL
    rows with corr=NULL via chunked COPY. Target dates pre-filtered to
    dates MISSING from the table → rows can never conflict; pure COPY
    (5-10x faster than upsert).
  - CORR-ONLY mode (``with_corr=True`` — the dedicated ``--corr``
    sub-command): upserts corr_20d/60d/255d DIRECTLY from the GPU corr
    tensor frame; the payload carries only the 4 PK cols + 3 corr cols,
    so base columns are never touched. Per-subject frames sit below GPU
    breakeven (~310K rows), which is exactly why the corr path bypasses
    the per-subject merge pipeline entirely.

  [PERF-BLOCKER — declared] the insert path iterates subjects; each
  iteration is a small (~4K-row) merge+MA5 chain. See _perf.py
  DECLARED_BLOCKERS for the accepted rationale.
"""
from __future__ import annotations

import datetime
from typing import Optional, Set

import pandas as pd

from _common.db_commons import batched_copy_by_key_async, bulk_upsert_async

from builds.cross_stats._perf import timed
from builds.cross_stats.config import COMPUTE_CORR, TABLE
from builds.cross_stats.compute._lookback import filter_dataframes_for_lookback
from builds.cross_stats.compute._pivots import prepare_pivots
from builds.cross_stats.compute._filters import (
    filter_target_dates,
    filter_to_target_rows,
)
from builds.cross_stats.compute._merge import (
    merge_subject_with_benchmarks,
    attach_shared_weights,
    build_weights_frame,
)
from builds.cross_stats.compute._gpu_corr import (
    compute_rolling_correlations_bulk,
    fetch_corr_grid_dates,
)
from builds.cross_stats.compute._etf import attach_etf_amounts, compute_ma5_ratio
from builds.cross_stats.compute._sanitize import (
    select_and_sanitize,
    select_and_sanitize_corr,
)

# Subject-block clamps for the code-partitioned corr computation.
_CORR_BLOCK_MIN: int = 8
_CORR_BLOCK_MAX: int = 256


def _corr_block_size(n_subjects: int, n_pairs: int,
                      n_grid_dates: int) -> int:
    """Subject-block size keeping ONE block's corr frame under 12 GB
    (est. 256 B/row; see CORR_ROW_BYTES rationale in the migrated config).
    Falls back to _CORR_BLOCK_MAX when the estimate degenerates."""
    if n_subjects == 0 or n_pairs == 0 or n_grid_dates == 0:
        return _CORR_BLOCK_MAX
    rows_per_subject: float = (n_pairs / n_subjects) * n_grid_dates
    budget_bytes: int = 12 * 1024 ** 3
    budget_rows: int = budget_bytes // 256
    block: int = int(budget_rows // max(1.0, rows_per_subject))
    return max(_CORR_BLOCK_MIN, min(_CORR_BLOCK_MAX, block))


async def _fetch_broad_market_codes(conn, benchmark_codes) -> set[str]:
    """Broad-market codes among the active benchmarks."""
    rows = await conn.fetch("""
        SELECT DISTINCT code FROM stats.sec_classification
        WHERE sector_id = 'BROAD' AND type = 'index' AND is_active = TRUE
    """)
    return {r["code"] for r in rows} & set(benchmark_codes)


def _related_benchmarks(shared_weights: dict,
                        broad_market_codes: set[str]) -> dict[str, set[str]]:
    """subject_code -> set(benchmark_code) with non-zero shared weight.

    Zero-weight pairs are NOT stored — handled via LEFT JOIN + COALESCE.
    Broad-market codes are added for every subject (they benchmark
    everything).
    """
    related: dict[str, set[str]] = {}
    for (sc, bc), (cw, _bw) in shared_weights.items():
        if cw and cw > 0:
            related.setdefault(sc, set()).add(bc)
    for sc in related:
        related[sc] |= broad_market_codes
    return related


# ---------------------------------------------------------------------------
#  Orchestrator
# ---------------------------------------------------------------------------
async def build_and_insert(conn, subject_closes: pd.DataFrame,
                           index_closes: pd.DataFrame,
                           shared_weights: dict,
                           etf_amount_by_index: pd.DataFrame,
                           sec_type: str,
                           *,
                           target_dates: Optional[Set[datetime.date]] = None,
                           with_corr: bool = COMPUTE_CORR,
                           ) -> int:
    """Route to the insert pipeline or the corr-only fast path.

    Args:
        conn: asyncpg connection.
        subject_closes: DataFrame [date, code, subject_close].
        index_closes:   DataFrame [date, benchmark_code, benchmark_close].
        shared_weights: dict {(subject_code, benchmark_code):
                        (code_wt, bench_wt)}.
        etf_amount_by_index: DataFrame [date, index_code, etf_amount]
            (unused by the corr-only path).
        sec_type: 'index' (self-pairs excluded) or 'etf'.
        target_dates: when non-empty, only rows whose date is in this set
            are written. Insert mode: dates MISSING from the table (the
            filter_target_dates DB check enforces this, making COPY
            conflict-free). Corr-only mode: EXISTING grid dates to
            refresh.
        with_corr: corr-only mode. Default False.

    Returns:
        Total rows written.
    """
    n_subjects = subject_closes["code"].nunique() if not subject_closes.empty else 0
    n_indices = (index_closes["benchmark_code"].nunique()
                 if not index_closes.empty else 0)
    print(f"    -> {n_subjects} {sec_type}s x {n_indices} indices "
          f"(cross-product on shared dates), "
          f"mode={'corr-only' if with_corr else 'insert'}",
          flush=True)

    if n_subjects == 0 or n_indices == 0:
        print("    -> no data to insert.", flush=True)
        return 0

    # Lookback pre-filter (both modes).
    subject_closes, index_closes, etf_amount_by_index = (
        filter_dataframes_for_lookback(
            subject_closes, index_closes, etf_amount_by_index,
            target_dates if target_dates is not None else set(),
        )
    )

    broad_market_codes = await _fetch_broad_market_codes(
        conn, index_closes["benchmark_code"].unique()
    )
    subject_related = _related_benchmarks(shared_weights, broad_market_codes)

    if with_corr:
        return await _corr_only_update(
            conn, subject_closes, index_closes, etf_amount_by_index,
            subject_related, sec_type, target_dates, n_subjects,
        )
    return await _insert_rows(
        conn, subject_closes, index_closes, etf_amount_by_index,
        shared_weights, subject_related, sec_type, target_dates,
        n_subjects,
    )


# ---------------------------------------------------------------------------
#  Corr-only fast path (--corr sub-command)
# ---------------------------------------------------------------------------
async def _corr_only_update(conn, subject_closes: pd.DataFrame,
                            index_closes: pd.DataFrame,
                            etf_amount_by_index: pd.DataFrame,
                            subject_related: dict[str, set[str]],
                            sec_type: str,
                            target_dates: Optional[Set[datetime.date]],
                            n_subjects: int) -> int:
    """Upsert corr_20d/60d/255d straight from the GPU tensor frame."""
    print("    -> Corr-only fast path: GPU tensor -> upsert "
          "(no per-subject pipeline, base columns untouched)", flush=True)

    benchmark_close_wide, _etf_wide, _etf_long = (
        prepare_pivots(index_closes, etf_amount_by_index)
    )
    grid_dates = await fetch_corr_grid_dates(conn)
    subject_codes = sorted(subject_closes["code"].unique())
    n_pairs = sum(len(v) for v in subject_related.values())
    block = _corr_block_size(len(subject_codes), n_pairs, len(grid_dates))
    print(f"    -> {len(subject_codes)} subjects in blocks of {block} "
          f"(corr frame budget 12 GB per block)",
          flush=True)

    ts_targets = (pd.to_datetime(sorted(target_dates))
                  if target_dates else None)

    total = 0
    with timed("corr"):
        for blk_start in range(0, len(subject_codes), block):
            block_codes: list[str] = subject_codes[blk_start:blk_start + block]
            block_subjects = subject_closes[
                subject_closes["code"].isin(block_codes)
            ]
            corr_bulk = compute_rolling_correlations_bulk(
                block_subjects, benchmark_close_wide,
                subject_related, block_codes,
                grid_dates=grid_dates,
            )
            if corr_bulk.empty:
                continue
            if ts_targets is not None:
                corr_bulk = corr_bulk[corr_bulk["date"].isin(ts_targets)]
            # Self-pairs: the related map adds broad codes to EVERY
            # subject — including a broad subject itself. The main
            # pipeline excludes self-pairs, so upserting them would
            # INSERT base-column-less rows.
            corr_bulk = corr_bulk[corr_bulk["code"] != corr_bulk["benchmark_code"]]
            rows = select_and_sanitize_corr(corr_bulk, sec_type)
            del corr_bulk
            if not rows:
                continue
            n = await bulk_upsert_async(
                conn, TABLE, rows,
                key_columns=["code", "date", "sec_type", "benchmark_code"],
            )
            total += n
            done = min(blk_start + block, len(subject_codes))
            print(f"    [{done}/{len(subject_codes)}] subjects: {n:,} corr "
                  f"rows (cumulative: {total:,})", flush=True)
    return total


# ---------------------------------------------------------------------------
#  Insert path (main pipeline — full rows, corr=NULL, chunked COPY)
# ---------------------------------------------------------------------------
async def _insert_rows(conn, subject_closes: pd.DataFrame,
                       index_closes: pd.DataFrame,
                       etf_amount_by_index: pd.DataFrame,
                       shared_weights: dict,
                       subject_related: dict[str, set[str]],
                       sec_type: str,
                       target_dates: Optional[Set[datetime.date]],
                       n_subjects: int) -> int:
    """Per-subject merge pipeline writing FULL rows via chunked COPY."""
    # DB-side skip for already-present target dates (safety net).
    target_dates = await filter_target_dates(conn, target_dates)
    if target_dates is not None and len(target_dates) == 0:
        print("    -> all target dates already present; nothing to do.",
              flush=True)
        return 0

    benchmark_close_wide, _etf_amount_wide, etf_amount_long = (
        prepare_pivots(index_closes, etf_amount_by_index)
    )
    weights_frame = build_weights_frame(shared_weights)

    total = 0
    done = 0
    with timed("pair-grain"):
        for subject_code in sorted(subject_closes["code"].unique()):
            done += 1
            related = subject_related.get(subject_code, set())
            if not related:
                if done % 10 == 0 or done == n_subjects:
                    print(f"    [{done}/{n_subjects}] {subject_code}: "
                          f"skip (no non-zero shared weight with any "
                          f"benchmark)", flush=True)
                continue

            related_idx = index_closes[
                index_closes["benchmark_code"].isin(related)
            ]

            one_subject = subject_closes[subject_closes["code"] == subject_code]
            merged = merge_subject_with_benchmarks(
                one_subject, related_idx, sec_type
            )
            if merged is None:
                continue

            merged = attach_shared_weights(merged, weights_frame, subject_code)
            merged = attach_etf_amounts(
                merged, etf_amount_long, sec_type, subject_code
            )
            merged = compute_ma5_ratio(merged)
            merged = filter_to_target_rows(
                merged, target_dates, subject_code, done - 1, n_subjects
            )

            rows = select_and_sanitize(merged)
            del merged
            if not rows:
                continue

            n = await batched_copy_by_key_async(
                conn, TABLE, rows, key="code",
            )
            total += n
            if done % 10 == 0 or done == n_subjects:
                print(f"    [{done}/{n_subjects}] {subject_code}: "
                      f"{len(rows):,} rows (cumulative: {total:,})",
                      flush=True)
    return total
