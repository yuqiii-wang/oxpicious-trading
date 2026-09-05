"""Bulk rolling correlation kernel (pair grain, stride-20 grid).

Ported from analyze.sec_alloc_perf_attribution.compute._gpu_corr (2026-09-04);
only the config import is re-pointed — kernel logic unchanged.

TWO-LAYER GPU ARCHITECTURE
  Layer 1 — process-level cudf.pandas hook (compute/__init__.py).
  Layer 2 — batched tensor kernel ``_common.df_utils.pairwise_rolling_corr``
            (CuPy batched cumsum kernel, ~24x faster than the pandas CPU
            fallback cuDF would take: cuDF does NOT implement
            Rolling.corr — an API gap, not a size issue).

  phase 1 — numpy bookkeeping: per-subject emit masks + base frames
            (only (subject, related benchmark) pairs on GRID dates)
  phase 2 — per window: ONE (T, N, N) pairwise rolling-corr tensor,
            each pair's column assigned by fancy indexing in one shot.

  Subject blocking + BENCHMARK CHUNKING bound VRAM: the live tensor set is
  (T, N, N) float64 with ~8 intermediates, N = subject_block + bench_chunk —
  it scales as T x N^2 (every subject column interacts with ALL columns).
  Both sizes derive from FREE VRAM (capped 75%, per the gpu-df-compute
  playbook): the cudf.pandas pool reservation can leave only ~6 GB free, so
  full-width blocks (all benchmarks in every block) exceed VRAM and fall
  back to the numpy kernel — chunking the benchmark columns restores GPU
  residency at unchanged float64 parity. Blocks halve on CuPy MemoryError.

  Dates stay datetime64[ns] end-to-end (object-date round-trips forced
  fallbacks and dtype mismatches at every merge).
"""
from __future__ import annotations

import math
import time
from typing import Optional

import numpy as np
import pandas as pd

from _common.df_utils import pairwise_rolling_corr
from builds.cross_stats.config import CORR_WINDOWS

# Rough number of (T, N, N) float64 tensors live simultaneously inside
# the cumsum kernel (xm, ym, sx, sy, sxx, syy, sxy, masks).
_TENSOR_LIVE_COUNT: int = 8
_MAX_SUBJECT_BLOCK: int = 64
_MIN_SUBJECT_BLOCK: int = 8
# Subject block when CuPy is unavailable (numpy backend = host RAM, no
# VRAM constraint to fit).
_CPU_SUBJECT_BLOCK: int = 32

# Stride (trading days) between consecutive GRID dates on the GLOBAL
# index calendar — mirrors analysis.industry_correlations' interval.
# corr_{W}d values are materialized ONLY on grid dates (calendar index
# % INTERVAL_DAYS == INTERVAL_DAYS - 1); all other dates store NULL corr.
# This cuts emitted corr rows ~20x (the former daily emit was the
# pipeline's dominant host-memory consumer).
INTERVAL_DAYS: int = 20


async def fetch_corr_grid_dates(conn) -> np.ndarray:
    """Grid dates (every INTERVAL_DAYS trading days) on the GLOBAL index
    calendar (stats.index_basic_stats distinct dates).

    Returned as datetime64[D] so full and incremental runs pick the SAME
    grid dates regardless of lookback trimming (position-based grids
    would misalign between the two modes).
    """
    rows = await conn.fetch(f"""
        WITH cal AS (
            SELECT DISTINCT date FROM stats.index_basic_stats
        ), g AS (
            SELECT date, ROW_NUMBER() OVER (ORDER BY date) - 1 AS idx
            FROM cal
        )
        SELECT date FROM g WHERE idx % {INTERVAL_DAYS} = {INTERVAL_DAYS - 1}
    """)
    return np.asarray([r["date"] for r in rows], dtype="datetime64[D]")


def compute_rolling_correlations_bulk(
    subject_closes: pd.DataFrame,
    benchmark_close_wide: pd.DataFrame,
    subject_related_benchmarks: dict[str, set[str]],
    subject_codes: list[str],
    *,
    grid_dates: Optional[np.ndarray] = None,
    enable_gpu: Optional[bool] = None,
) -> pd.DataFrame:
    """Bulk rolling correlations for ALL subjects.

    One wide (dates x subjects+benchmarks) matrix; per window ONE batched
    kernel pass extracts only the (subject, related benchmark) cells.

    Args:
        subject_closes: [date (datetime64), code, subject_close].
        benchmark_close_wide: (datetime64 index, benchmark_code cols).
        subject_related_benchmarks: {subject_code: set(benchmark_code)}.
        subject_codes: sorted list of all subject codes to process.
        grid_dates: sorted datetime64 array (fetch_corr_grid_dates). None
            → grid derived from combined calendar positions (only correct
            in full-recompute mode).

    Returns DataFrame [date, code, benchmark_code, corr_20d, corr_60d,
    corr_255d]; empty when no work.
    """
    t_start = time.time()
    if enable_gpu is not None:
        print(f"    [corr_bulk] enable_gpu={enable_gpu} (informational; "
              f"backend chosen per-window by the kernel)", flush=True)

    if not _is_datetime64(subject_closes["date"]):
        subject_closes = subject_closes.copy()
        subject_closes["date"] = pd.to_datetime(subject_closes["date"])
    if not _is_datetime64(benchmark_close_wide.index):
        benchmark_close_wide = benchmark_close_wide.copy()
        benchmark_close_wide.index = pd.to_datetime(
            benchmark_close_wide.index
        )

    subject_wide = (
        subject_closes.pivot(
            index="date", columns="code", values="subject_close"
        )
        .sort_index()
    )

    # Inverted index: benchmark_code -> related subject codes.
    benchmark_to_subjects: dict[str, list[str]] = {}
    for sc in subject_codes:
        for bc in subject_related_benchmarks.get(sc, set()):
            benchmark_to_subjects.setdefault(bc, []).append(sc)

    active_benchmarks = {
        bc for bc, subs in benchmark_to_subjects.items() if subs
    }
    if not active_benchmarks:
        return _empty_corr_frame()

    active_bench_cols = [
        c for c in benchmark_close_wide.columns if c in active_benchmarks
    ]
    bench_wide_active = benchmark_close_wide[active_bench_cols]

    # Namespaced outer merge on date: subjects and benchmarks may share
    # codes (broad indices appear in both roles). Outer merge preserves
    # each series' own dates; pairwise-valid kernel semantics handle
    # misalignment. Pre-sort columns the same way the kernel does so
    # tensor [i, j] slots match our column positions.
    combined = (
        subject_wide.add_prefix("s|").reset_index()
        .merge(
            bench_wide_active.add_prefix("b|").reset_index(),
            on="date", how="outer",
        )
        .set_index("date")
        .sort_index().sort_index(axis=1)
    )

    dates64: np.ndarray = combined.index.to_numpy()  # datetime64[ns]
    t_len: int = len(combined)
    if t_len == 0:
        return _empty_corr_frame()

    # ---- stride grid mask (corr emitted ONLY on grid dates) ----------
    if grid_dates is not None and len(grid_dates):
        grid64 = np.asarray(grid_dates).astype(dates64.dtype)
        grid_mask: np.ndarray = np.isin(dates64, grid64)
        n_grid = int(grid_mask.sum())
        grid_via = "global calendar (caller)"
    else:
        grid_mask = np.zeros(t_len, dtype=bool)
        grid_mask[INTERVAL_DAYS - 1::INTERVAL_DAYS] = True
        n_grid = int(grid_mask.sum())
        grid_via = "combined calendar positions"
    print(f"    [corr_bulk] stride grid: every {INTERVAL_DAYS} trading "
          f"days — {n_grid}/{t_len:,} dates are grid dates ({grid_via})",
          flush=True)

    col_names: np.ndarray = np.asarray(combined.columns)
    sub_pos: dict[str, int] = {
        n[2:]: i for i, n in enumerate(col_names) if n.startswith("s|")
    }
    bench_pos: dict[str, int] = {
        n[2:]: i for i, n in enumerate(col_names) if n.startswith("b|")
    }
    valid: np.ndarray = combined.notna().to_numpy()  # (T, N) bool

    # ---------------- phase 1: emit masks + base frames ---------------
    # Only (subject, related benchmark) pairs with BOTH series valid on a
    # GRID date emit a corr row.
    slices: list[tuple[int, np.ndarray, np.ndarray, np.ndarray,
                       np.ndarray, str]] = []
    base_frames: list[pd.DataFrame] = []
    for sc in subject_codes:
        si = sub_pos.get(sc)
        if si is None:
            continue
        bcs = [b for b in subject_related_benchmarks.get(sc, ())
               if b in bench_pos]
        if not bcs:
            continue
        bidx = np.fromiter(
            (bench_pos[b] for b in bcs), dtype=np.int64, count=len(bcs)
        )
        emit = valid[:, [si]] & valid[:, bidx]  # (T, B)
        emit &= grid_mask[:, None]
        date_idx, col_idx = np.nonzero(emit)
        if date_idx.size == 0:
            continue
        bench_names = np.asarray(bcs, dtype=object)
        base_frames.append(pd.DataFrame({
            "date": dates64[date_idx],
            "code": sc,
            "benchmark_code": bench_names[col_idx],
        }))
        # row_bench: per-row benchmark code (the emitted rows' column_idx
        # into bench_names) — used to scatter cells into benchmark chunks.
        slices.append((si, bidx, date_idx, col_idx, bench_names[col_idx], sc))

    if not slices:
        return _empty_corr_frame()

    # ---------------- phase 2: one tensor per window -------------------
    # Benchmark columns are a contiguous position range in the sorted
    # col_names (b| sorts before s|); chunks slice that range so the
    # (T, block+chunk, block+chunk) live tensor set fits free VRAM.
    bench_global_names = np.asarray(
        [n[2:] for n in col_names if n.startswith("b|")], dtype=object
    )
    n_bench: int = len(bench_global_names)
    block, chunk = _fit_block_and_chunk(t_len, n_bench)
    if chunk < n_bench:
        print(f"    [corr_bulk] VRAM-fit sizing: subject block={block}, "
              f"benchmark chunk={chunk} (tensor N={block + chunk} cols) — "
              f"full-width blocks exceed free VRAM; chunking keeps the "
              f"kernel on GPU", flush=True)
    subject_names = [n[2:] for n in col_names if n.startswith("s|")]

    for w_idx, N in enumerate(CORR_WINDOWS):
        corr_col = f"corr_{N}d"
        min_p = max(N * 2 // 3, 3)
        # Host-side accumulators: cells are scattered per chunk (pure
        # numpy) and the whole column is assigned to the frame once per
        # window — avoids per-chunk cudf partial-setitem fallbacks.
        corr_accs: list[np.ndarray] = [
            np.full(len(f), np.nan) for f in base_frames
        ]
        for blk_start in range(0, len(subject_names), block):
            blk = subject_names[blk_start:blk_start + block]
            blk_set = set(blk)
            blk_sub_cols = [f"s|{s}" for s in blk]
            # while (not for-range): a MemoryError halves `chunk`, and the
            # next chunk must start from the current c1 with the NEW size —
            # a fixed range step would leave uncomputed gaps.
            c0 = 0
            while c0 < n_bench:
                c1 = min(c0 + chunk, n_bench)
                chunk_names = bench_global_names[c0:c1]
                keep_cols = blk_sub_cols + [f"b|{n}" for n in chunk_names]
                block_wide = combined[keep_cols]
                while True:
                    try:
                        tensor = pairwise_rolling_corr(
                            block_wide, N, min_periods=min_p,
                        )
                        break
                    except MemoryError:
                        if chunk <= 1:
                            raise
                        chunk = max(chunk // 2, 1)
                        print(f"    [corr_bulk] CuPy MemoryError: benchmark "
                              f"chunk -> {chunk}, retrying", flush=True)
                        c1 = min(c0 + chunk, n_bench)
                        chunk_names = bench_global_names[c0:c1]
                        keep_cols = blk_sub_cols + \
                            [f"b|{n}" for n in chunk_names]
                        block_wide = combined[keep_cols]
                c0 = c1

                # Block/chunk-local positions (kernel sorts block_wide
                # columns).
                blk_cols = np.asarray(block_wide.columns)
                blk_sub_pos = {
                    n[2:]: i for i, n in enumerate(blk_cols)
                    if n.startswith("s|")
                }
                for (si, bidx, date_idx, col_idx, row_bench, sc), acc in zip(
                        slices, corr_accs):
                    if sc not in blk_set:
                        continue
                    # chunk_names is sorted ascending (col_names order) and
                    # the chunk covers bench positions [c0, c1) — the local
                    # index of each row's benchmark is a vectorized
                    # searchsorted (no per-row dict lookups).
                    rows = (bidx >= c0) & (bidx < c1)
                    rows = rows[col_idx]  # mask over this frame's rows
                    if not rows.any():
                        continue
                    local = np.searchsorted(chunk_names, row_bench[rows])
                    acc[rows] = tensor[
                        date_idx[rows], blk_sub_pos[sc], local
                    ]
                del tensor
        for f, acc in zip(base_frames, corr_accs):
            f[corr_col] = acc
        if w_idx == 0:
            n_rows = sum(len(f) for f in base_frames)
            print(f"    [corr_bulk] emitted {n_rows:,} (pair, date) rows "
                  f"across {len(slices)} subjects; T={t_len:,} dates, "
                  f"{len(col_names):,} series; block={block}, "
                  f"bench_chunk={chunk}", flush=True)

    # ---------------- combine ------------------------------------------
    result = pd.concat(base_frames, ignore_index=True)
    result = result.sort_values(
        ["code", "benchmark_code", "date"]
    ).reset_index(drop=True)

    elapsed = time.time() - t_start
    n_benchmarks = len(active_bench_cols)
    print(
        f"    [corr_bulk] {len(result):,} rows from "
        f"{len(subject_codes)} subjects x {n_benchmarks} benchmarks, "
        f"elapsed: {elapsed:.2f}s",
        flush=True,
    )

    return result


def _fit_block_and_chunk(t_len: int, n_bench: int) -> tuple[int, int]:
    """(subject_block, benchmark_chunk) keeping the kernel's live (T, N, N)
    float64 tensor set within 75% of FREE VRAM, where N = block + chunk.

    Every subject column interacts with ALL matrix columns, so the live
    set scales as T x N^2. The former subject-only block size ignored the
    N^2 cross term and — with the cudf.pandas pool reservation leaving
    only ~6 GB free — produced full-width blocks that exceeded VRAM and
    fell back to the numpy kernel. Chunking the BENCHMARK columns keeps
    N within budget and restores GPU residency at unchanged float64
    parity.

    Without CuPy (numpy backend = host RAM, no VRAM cap):
    (_CPU_SUBJECT_BLOCK, n_bench) — every benchmark in one pass.
    """
    try:
        import cupy as cp
        free_bytes, _total = cp.cuda.runtime.memGetInfo()
    except Exception:
        return _CPU_SUBJECT_BLOCK, n_bench
    budget = 0.75 * free_bytes
    per_cell = _TENSOR_LIVE_COUNT * 8.0 * max(t_len, 1)
    n_max = int(math.sqrt(max(budget, 1.0) / per_cell))
    block = min(_MAX_SUBJECT_BLOCK, max(_MIN_SUBJECT_BLOCK, n_max - n_bench))
    chunk = min(n_bench, max(1, n_max - block))
    return block, chunk


def _is_datetime64(series: pd.Series) -> bool:
    return pd.api.types.is_datetime64_any_dtype(series)


def _empty_corr_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.Series(dtype="datetime64[ns]"),
        "code": pd.Series(dtype="object"),
        "benchmark_code": pd.Series(dtype="object"),
        **{f"corr_{N}d": pd.Series(dtype="float64")
           for N in CORR_WINDOWS},
    })
