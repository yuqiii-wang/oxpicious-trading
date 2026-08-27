"""Bulk rolling correlation computation with GPU acceleration.

GPU ARCHITECTURE (TWO-LAYER)
============================

Layer 1 — Process-level hook (``compute/__init__.py``)
-----------------------------------------------
``compute/__init__.py`` calls ``maybe_enable_cudf_pandas(mode="auto")``
BEFORE any pandas import, so ALL pandas ops route through cuDF where
supported.

Layer 2 — Batched tensor kernel (``_common.df_utils.pairwise_rolling_corr``)
----------------------------------------------------------------------------
cuDF does NOT implement ``Rolling.corr`` AT ALL (API gap, not a size
issue): every ``.rolling().corr()`` call under cudf.pandas prints a
[cudf fallback] and pays an H2D+D2H round-trip on CPU.  The previous
per-benchmark loop made ~#benchmarks x #windows such calls per run.

This module instead follows the correlations-pipeline pattern
(analyze/industry_sentiments/correlations.py):

  phase 1 — numpy bookkeeping: per-subject emit masks + base frames
            (only subject/benchmark pairs with composition overlap)
  phase 2 — per window: ONE ``(T, N, N)`` pairwise rolling-corr tensor
            via ``pairwise_rolling_corr`` (CuPy batched cumsum kernel,
            ~24x faster, pandas-exact semantics incl. pairwise NaN
            exclusion and degenerate-window guards), then each pair's
            column is assigned by fancy indexing in one shot.

Subject blocking bounds VRAM: the tensor is (T, B+blk, B+blk) float64
with ~8 live intermediates, so the subject block size is derived from
FREE VRAM (capped at 75%, per the gpu-df-compute playbook) and halved
on CuPy MemoryError.

DATES stay ``datetime64[ns]`` end-to-end — no ``.dt.date`` conversion
here (the old round-trip python-date -> to_datetime forced fallbacks
and dtype mismatches at every downstream merge).

WHY ``pd.DataFrame`` TYPE HINTS (NOT ``cudf.DataFrame``)
-------------------------------------------------------
``cudf.pandas`` uses the transparent proxy model: ``pd.DataFrame`` IS
the GPU-backed DataFrame when the hook is active.  Type hints describe
the public API contract, not the backend implementation.
"""
from __future__ import annotations

import time
from typing import Optional

import numpy as np
import pandas as pd

from _common.df_utils import pairwise_rolling_corr

from analyze.sec_alloc_perf_attribution.config import CORR_WINDOWS

# Rough number of (T, N, N) float64 tensors live simultaneously inside
# the cumsum kernel (xm, ym, sx, sy, sxx, syy, sxy, masks).
_TENSOR_LIVE_COUNT: int = 8
# Never exceed this block size regardless of free VRAM (bounds latency
# per kernel call and keeps column-name bookkeeping cheap).
_MAX_SUBJECT_BLOCK: int = 64
_MIN_SUBJECT_BLOCK: int = 8

# Stride (trading days) between consecutive GRID dates on the GLOBAL
# index calendar — mirrors analysis.industry_correlations' `interval`
# (default 20). corr_{W}d values are materialized ONLY on grid dates
# (calendar index % INTERVAL_DAYS == INTERVAL_DAYS - 1, i.e. the
# N=20 trailing window STARTS on a multiple-of-20 calendar index);
# all other dates store NULL corr. This cuts emitted corr rows ~20x
# (the former daily emit was the pipeline's dominant host-memory
# consumer: tens of millions of row dicts).
INTERVAL_DAYS: int = 20


# ---------------------------------------------------------------------------
#  Step 5b: BULK rolling correlations (all subjects x all benchmarks)
# ---------------------------------------------------------------------------
async def fetch_corr_grid_dates(conn) -> np.ndarray:
    """Grid dates (every INTERVAL_DAYS trading days) on the GLOBAL index
    calendar (stats.index_basic_stats distinct dates, 0-based index %
    INTERVAL_DAYS == INTERVAL_DAYS - 1).

    Returned as datetime64[D] so both full and incremental runs pick the
    SAME grid dates regardless of any lookback trimming of the in-memory
    frames (position-based grids would misalign between the two modes).
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
    return np.asarray(
        [r["date"] for r in rows], dtype="datetime64[D]"
    )


def compute_rolling_correlations_bulk(
    subject_closes: pd.DataFrame,
    benchmark_close_wide: pd.DataFrame,
    subject_related_benchmarks: dict[str, set[str]],
    subject_codes: list[str],
    *,
    grid_dates: Optional[np.ndarray] = None,
    enable_gpu: Optional[bool] = None,
) -> pd.DataFrame:
    """Bulk rolling correlation computation for ALL subjects.

    Builds one wide (dates x subjects+benchmarks) matrix, then per window
    computes the full pairwise tensor in a single batched kernel pass and
    extracts only the (subject, related benchmark) cells.

    STRIDE (grid) semantics — mirrors analysis.industry_correlations:
    corr values are emitted ONLY on GRID dates (every INTERVAL_DAYS
    trading days on the GLOBAL index calendar, passed by the caller as
    ``grid_dates`` so incremental runs align with full runs). Non-grid
    dates never emit a corr row here — the daily table rows for those
    dates simply carry NULL corr columns after the left merge.

    Args:
        subject_closes: DataFrame [date (datetime64), code, subject_close].
        benchmark_close_wide: DataFrame (datetime64 index, benchmark_code cols).
        subject_related_benchmarks: {subject_code: set(benchmark_code)}.
        subject_codes: sorted list of all subject codes to process.
        grid_dates: sorted datetime64 array of GLOBAL grid dates (from
            fetch_corr_grid_dates). When None, the grid is derived from
            the combined calendar positions (idx % INTERVAL_DAYS ==
            INTERVAL_DAYS - 1) — only correct in full-recompute mode.
        enable_gpu: retained for API compatibility; the backend choice
            (CuPy / numpy / pandas) is made inside pairwise_rolling_corr
            from the actual matrix size.

    Returns:
        DataFrame [date (datetime64[ns]), code, benchmark_code,
        corr_20d, corr_60d, corr_255d].  Empty DataFrame when
        no work to do.
    """
    t_start = time.time()
    if enable_gpu is not None:
        print(f"    [corr_bulk] enable_gpu={enable_gpu} (informational; "
              f"backend chosen per-window by the kernel)", flush=True)

    # Step 0: Ensure date columns are datetime64 for cudf.pandas compat.
    if not _is_datetime64(subject_closes["date"]):
        subject_closes = subject_closes.copy()
        subject_closes["date"] = pd.to_datetime(subject_closes["date"])
    if not _is_datetime64(benchmark_close_wide.index):
        benchmark_close_wide = benchmark_close_wide.copy()
        benchmark_close_wide.index = pd.to_datetime(
            benchmark_close_wide.index
        )

    # Step 1: Create subject-wide pivot (dates x subjects).
    subject_wide = (
        subject_closes.pivot(
            index="date", columns="code", values="subject_close"
        )
        .sort_index()
    )

    # Inverted index: benchmark_code -> related subject codes.
    benchmark_to_subjects: dict[str, list[str]] = {}
    for sc in subject_codes:
        related = subject_related_benchmarks.get(sc, set())
        for bc in related:
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
    # codes (broad-market indices appear in both roles), so prefix to
    # keep columns distinct.  Outer merge preserves each series' own
    # dates; the kernel's pairwise-valid semantics handle misalignment.
    combined = (
        subject_wide.add_prefix("s|").reset_index()
        .merge(
            bench_wide_active.add_prefix("b|").reset_index(),
            on="date", how="outer",
        )
        .set_index("date")
    )
    # pairwise_rolling_corr sorts columns internally; pre-sort the same
    # way so tensor [i, j] slots match OUR column positions.
    combined = combined.sort_index().sort_index(axis=1)

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
    # Pure numpy bookkeeping (per playbook: heavy math at pool level,
    # emit at key level).  Only (subject, related benchmark) pairs with
    # BOTH series valid on a GRID date emit a corr row — the stride
    # restriction (grid_mask) keeps the emitted row count ~20x smaller
    # than the daily emit (dominant host-memory consumer before).
    slices: list[tuple[int, np.ndarray, np.ndarray, np.ndarray]] = []
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
        emit &= grid_mask[:, None]  # stride: grid dates only
        date_idx, col_idx = np.nonzero(emit)
        if date_idx.size == 0:
            continue
        base_frames.append(pd.DataFrame({
            "date": dates64[date_idx],
            "code": sc,
            "benchmark_code": np.asarray(bcs, dtype=object)[col_idx],
        }))
        slices.append((si, bidx, date_idx, col_idx))

    if not slices:
        return _empty_corr_frame()

    # ---------------- phase 2: one tensor per window -------------------
    n_subject_cols = len(sub_pos)
    block = _subject_block_size(t_len, len(col_names) - n_subject_cols)
    subject_names = [n[2:] for n in col_names if n.startswith("s|")]

    for w_idx, N in enumerate(CORR_WINDOWS):
        corr_col = f"corr_{N}d"
        min_p = max(N * 2 // 3, 3)
        for blk_start in range(0, len(subject_names), block):
            blk = subject_names[blk_start:blk_start + block]
            blk_set = set(blk)
            keep_cols = [f"s|{s}" for s in blk] + \
                        [n for n in col_names if n.startswith("b|")]
            block_wide = combined[keep_cols]
            while True:
                try:
                    tensor = pairwise_rolling_corr(
                        block_wide, N, min_periods=min_p,
                    )
                    break
                except MemoryError:
                    if block <= _MIN_SUBJECT_BLOCK:
                        raise
                    block = max(block // 2, _MIN_SUBJECT_BLOCK)
                    print(f"    [corr_bulk] CuPy MemoryError: subject "
                          f"block -> {block}, retrying", flush=True)
                    blk = subject_names[blk_start:blk_start + block]
                    blk_set = set(blk)
                    keep_cols = [f"s|{s}" for s in blk] + \
                                [n for n in col_names if n.startswith("b|")]
                    block_wide = combined[keep_cols]

            # Block-local positions (kernel sorted block_wide columns).
            blk_cols = np.asarray(block_wide.columns)
            blk_sub_pos = {
                n[2:]: i for i, n in enumerate(blk_cols)
                if n.startswith("s|")
            }
            blk_bench_pos = {
                n[2:]: i for i, n in enumerate(blk_cols)
                if n.startswith("b|")
            }
            for (si, bidx, date_idx, col_idx), f in zip(slices, base_frames):
                sc = f["code"].iloc[0] if len(f) else None
                if sc not in blk_set:
                    continue
                # Every benchmark column is kept in every block, so this
                # maps 1:1 with bidx / col_idx (no filtering allowed —
                # it would misalign the fancy index below).
                b_local = np.fromiter(
                    (blk_bench_pos[n[2:]] for n in col_names[bidx]),
                    dtype=np.int64, count=len(bidx),
                )
                f[corr_col] = tensor[
                    date_idx, blk_sub_pos[sc], b_local[col_idx]
                ]
            del tensor
        if w_idx == 0:
            n_rows = sum(len(f) for f in base_frames)
            print(f"    [corr_bulk] emitted {n_rows:,} (pair, date) rows "
                  f"across {len(slices)} subjects; T={t_len:,} dates, "
                  f"{len(col_names):,} series; block={block}", flush=True)

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


# ---------------------------------------------------------------------------
#  VRAM-aware subject block size (playbook §4)
# ---------------------------------------------------------------------------
def _subject_block_size(t_len: int, n_bench: int) -> int:
    """Pick the subject block size so the kernel's live tensor set fits
    in 75% of FREE VRAM.  Falls back to 32 when CuPy is unavailable
    (numpy backend keeps everything in host RAM)."""
    try:
        import cupy as cp
        free_bytes, _total = cp.cuda.runtime.memGetInfo()
        budget = 0.75 * free_bytes
        # bytes ≈ _TENSOR_LIVE_COUNT * T * (B + blk)^2 * 8
        per_row = _TENSOR_LIVE_COUNT * 8.0 * t_len
        room = budget / per_row - n_bench
        block = int(room)
    except Exception:
        block = 32
    return max(_MIN_SUBJECT_BLOCK, min(_MAX_SUBJECT_BLOCK, block))


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------
def _is_datetime64(series: pd.Series) -> bool:
    """Check if a Series or Index has datetime64 dtype."""
    return pd.api.types.is_datetime64_any_dtype(series)


def _empty_corr_frame() -> pd.DataFrame:
    """Return an empty DataFrame with the standard corr output columns."""
    return pd.DataFrame({
        "date": pd.Series(dtype="datetime64[ns]"),
        "code": pd.Series(dtype="object"),
        "benchmark_code": pd.Series(dtype="object"),
        **{f"corr_{N}d": pd.Series(dtype="float64")
           for N in CORR_WINDOWS},
    })
