"""Internal correlations step for analyze.industry_sentiments.

Windowed pairwise Pearson correlation of industries' MA curves.

Populates analysis.industry_correlations with one row per
(industry_id, benchmark_industry_id, pool_size, start_date, interval)
where:
  - pool_size is the SAME for both industries (single column; cross-pool
    comparisons conflate cross-index size effects with sentiment
    co-movement and are not materialized).
  - industry_id < benchmark_industry_id (lexicographic) to deduplicate
    (A,B) vs (B,A). Self-pairs (A = B) are skipped (self-corr is always 1).

SOURCE
  stats.industry_basic_stats.mean_close (per-industry composite close
  series built by builds.industry — the former mean_price column,
  rehooked 2026-08-24).

WINDOW SEMANTICS (corr_ma{W}_{W}d, W in WINDOWS = [20, 60, 255])
  For each industry the MA-W curve is the trailing W-trading-day rolling
  mean of mean_close. Window starts sit on the pool calendar GRID:
  start indices 0, INTERVAL_DAYS, 2*INTERVAL_DAYS, ... (INTERVAL_DAYS =
  20 — the stride between consecutive compute windows, stored as the
  `interval` column). The window for corr_ma{W}_{W}d spans the W trading
  days [start_date, start_date + W); the stored value is the Pearson
  correlation between the two industries' MA-W curves over those W
  dates. Only FULL windows (every date present, both MA-W curves defined
  on every window date) are materialized; otherwise the column is NULL.
  A window's value is final once its last date exists, so rows are
  emitted exactly when start_date + W - 1 first appears in the source.

COMPUTATION ARRANGEMENT (pool-level matrices + vectorized emit)
  1. Per pool: pivot to a wide (date x industry) matrix — GPU-native.
  2. MA-W curves via ``wide.rolling(W, min_periods=W).mean()`` —
     GPU-native (cuDF implements rolling MEAN; only Rolling.corr is
     missing, which this design no longer needs).
  3. Grid starts: calendar indices 0, INTERVAL_DAYS, ... — full-window
     validity per column via a cumsum over the NaN mask (vectorized).
  4. Per (pool, W): ONE batched BLAS matmul ((F, N, w) @ (F, w, N) via
     sliding_window_view over the grid starts) gives ALL pairwise
     window correlations at once (sum algebra; NaN cells zero-filled —
     valid pairs never touch a filled cell because both columns are
     fully defined over the window).
  5. Vectorized 3D emit masks (S, N, N) + ONE np.nonzero; row dicts are
     built host-side directly from numpy columns (industry-major
     lexsort, values rounded + None-masked in _round_none) and written
     in ONE accumulated batched_copy_by_key_async /
     copy_or_upsert_split_async call (whole-industry chunks, never
     splitting an industry). No DataFrames in the emit/sanitize path —
     the former per-industry frame constructors caused ~15K cudf
     fallbacks (each a H2D/D2H round-trip) and 234 tiny COPYs.

Incremental mode (``target_dates`` is a non-empty set of WINDOW END
dates — see find_missing_corr_window_ends):
  Only rows whose window END date (start_date + W - 1 for some W with a
  non-NULL corr) is in ``target_dates`` are upserted. The full
  mean_close history per (industry, pool_size) is still loaded so the
  MA curves and windows are correct. No truncate is issued.

Force mode (``force=True``):
  Truncates analysis.industry_correlations first, then recomputes and
  inserts all rows (target_dates is ignored).

This module is an INTERNAL step of analyze.industry_sentiments — it is
invoked from __main__.py after the sentiments table has been repopulated,
reusing the same DB connection. It is NOT a standalone runnable.
"""
from __future__ import annotations

import datetime
import time
from typing import Optional, Set

import numpy as np
import pandas as pd

from _common.build_commons import (
    copy_or_upsert_split_async,
    truncate_table_async,
    rec_col,
    rec_cols,
)
from _common.db_commons import batched_copy_by_key_async
from _common.df_utils import epoch_col_to_dt64
from analyze._common import upsert_analysis_identity
from _common.df_utils.rolling_corr import release_cupy_pool


def _cupy_available() -> bool:
    """Cached CuPy + CUDA device check (same probe as recurring_cycles._fft)."""
    global _CUPY_OK
    if _CUPY_OK is None:
        try:
            import cupy as cp  # noqa: F401

            cp.cuda.runtime.getDeviceCount()
            _CUPY_OK = True
        except Exception:
            _CUPY_OK = False
    return _CUPY_OK


_CUPY_OK: Optional[bool] = None
_GPU_MIN_BYTES: int = 64 << 20      # route to GPU only above 64 MiB of work
_GPU_BACKEND_LOGGED: bool = False


# ---------------------------------------------------------------------------
#  Configuration
# ---------------------------------------------------------------------------

TABLE = "analysis.industry_correlations"
ANALYSIS_NAME = "industry_correlations"
ANALYSIS_DESCRIPTION = (
    "Windowed pairwise Pearson correlation between two industries' MA "
    "curves of mean_close (stats.industry_basic_stats.mean_close, built "
    "by builds.industry — the former mean_price column, rehooked). One "
    "row per (industry_id, benchmark_industry_id, pool_size, start_date, "
    "interval) with corr_ma20_20d / corr_ma60_60d / corr_ma255_255d. "
    "Windows start on the pool calendar grid every `interval` (default "
    "20) trading days; corr_ma{W}_{W}d correlates the two industries' "
    "MA-W curves over the W trading days starting on start_date. Both "
    "industries are compared in the SAME pool_size slice (single "
    "pool_size column). Self-pairs (A=B) excluded. Order convention: "
    "industry_id < benchmark_industry_id to deduplicate. Only same-pool "
    "slices materialized (all, small, mid, large). Built by "
    "analyze.industry_sentiments (internal step, incremental / force)."
)

# Same-pool slices materialized. Cross-pool comparisons (e.g. corr(A.small,
# B.large)) are intentionally NOT materialized — see module docstring.
POOL_SIZES = ["small", "mid", "large", "all"]

# Window lengths in trading days. The MA-{W} curve is the trailing W-day
# rolling mean of mean_close; corr_ma{W}_{W}d correlates the two industries'
# MA-W curves over the W-day window starting on start_date.
WINDOWS = [20, 60, 255]

# Stride in trading days between consecutive window starts on the pool
# calendar grid (stored as the `interval` column, default 20).
INTERVAL_DAYS = 20

# Minimum overlapping dates for a (pair, pool) to be materialized at all.
# Pairs with fewer overlapping dates cannot produce a full window even for
# the shortest MA (20), so every corr column would be NULL.
MIN_OVERLAP = min(WINDOWS)

# Baseline source table (re-exported for __main__ step headers).
BASELINE_TABLE = "stats.industry_basic_stats"


# ---------------------------------------------------------------------------
#  Missing-window detection (incremental entry point)
# ---------------------------------------------------------------------------

async def find_missing_corr_window_ends(
    conn,
) -> Set[datetime.date]:
    """Return the set of source dates that are POTENTIAL window END dates
    on the calendar grid but not yet covered by a computed window end.

    A date t is a potential window end iff its 0-based calendar index idx
    satisfies (idx - W + 1) % INTERVAL_DAYS == 0 AND idx - W + 1 >= W - 1
    for some W in WINDOWS: the window start must sit on the calendar grid
    (idx - W + 1 % INTERVAL_DAYS == 0) AND be late enough that the MA-W
    curve is defined from the very first window row (start >= W - 1 for
    industries spanning the whole calendar). A potential end is COVERED
    when some row already carries a non-NULL corr for the window that
    ends on it (start_date = t - W + 1).

    This replaces find_missing_analysis_dates for the correlations step:
    the table is keyed by window START dates, which lag the source
    calendar by design, so comparing raw source dates against
    start_date values would never converge.

    Note: the calendar is the GLOBAL distinct date set of
    stats.industry_basic_stats; per-pool calendars are assumed to share
    its grid (verified: contiguous series, no interior gaps). Use
    --force after backfills that change early history.
    """
    # Residue -> smallest window producing that end-index residue.
    # A date with index idx is a potential end for residue r iff
    # idx % INTERVAL_DAYS == r AND idx >= first_coverable(r), where
    # first_coverable(r) = smallest grid start >= W - 1, plus W - 1
    # (grid start must be a multiple of INTERVAL_DAYS, and >= W - 1 so
    # the MA-W curve is defined on the first window row).
    min_w_by_res: dict[int, int] = {}
    for w in WINDOWS:
        r = (w - 1) % INTERVAL_DAYS
        min_w_by_res[r] = min(min_w_by_res.get(r, w), w)
    pot_conds = " OR ".join(
        f"(idx % {INTERVAL_DAYS}) = {r} AND "
        f"idx >= {(-(-(w - 1) // INTERVAL_DAYS)) * INTERVAL_DAYS + w - 1}"
        for r, w in sorted(min_w_by_res.items())
    )
    # Covered end of a row = the calendar date at (calendar index of
    # start_date) + W - 1 — TRADING-day arithmetic. A plain
    # ``start_date + (W - 1)`` adds CALENDAR days and lands on the wrong
    # date (off by ~1 day per 3 trading days, ~106 days for W=255), so
    # covered ends would never match the grid's potential ends.
    cov_selects = " UNION ".join(
        f"SELECT c2.date AS d FROM {TABLE} t "
        f"JOIN cal c1 ON c1.date = t.start_date "
        f"JOIN cal c2 ON c2.idx = c1.idx + {w - 1} "
        f"WHERE t.corr_ma{w}_{w}d IS NOT NULL"
        for w in WINDOWS
    )
    sql = f"""
        WITH cal AS (
            SELECT date, ROW_NUMBER() OVER (ORDER BY date) - 1 AS idx
            FROM (SELECT DISTINCT date FROM {BASELINE_TABLE}) s
        ),
        pot AS (
            SELECT DISTINCT date FROM cal WHERE {pot_conds}
        ),
        cov AS ({cov_selects})
        SELECT p.date
        FROM pot p
        LEFT JOIN cov c ON c.d = p.date
        WHERE c.d IS NULL
    """
    rows = await conn.fetch(sql)
    return {r["date"] for r in rows}


# ---------------------------------------------------------------------------
#  Pipeline
# ---------------------------------------------------------------------------

def _grid_start_indices(t_len: int) -> np.ndarray:
    """0-based calendar indices of window starts (stride INTERVAL_DAYS)."""
    return np.arange(0, t_len, INTERVAL_DAYS, dtype=np.int64)


def _window_col_ok(
    ma: np.ndarray, starts: np.ndarray, w: int,
) -> np.ndarray:
    """Per (grid start, industry): is the MA-{w} curve defined on EVERY
    date of the window [s, s + w)?

    MA-{w} is the trailing w-day rolling mean, so it is defined at t iff
    the underlying series has w contiguous valid dates ending at t. The
    window is fully defined iff no NaN cell falls in the block
    ma[s : s + w] (checked via a cumsum over the NaN mask).

    Returns (n_starts, N) bool; False for partial windows (s + w > T).
    """
    t_len, n_ind = ma.shape
    nan_cs = np.cumsum(np.isnan(ma), axis=0)          # (T, N)
    # Zero-row prefix: NaN count over rows [s, s + w) = cs[s + w] - cs[s].
    nan_cs0 = np.vstack(
        [np.zeros((1, n_ind), dtype=nan_cs.dtype), nan_cs]
    )                                                  # (T + 1, N)
    idx_e = np.minimum(starts + w, t_len)              # (S,)
    nan_cnt = nan_cs0[idx_e] - nan_cs0[starts]         # (S, N)
    full = (starts + w) <= t_len
    return (nan_cnt == 0) & full[:, None]


def _window_corr_stack(
    ma: np.ndarray, starts: np.ndarray, w: int, col_ok: np.ndarray,
) -> np.ndarray:
    """Per grid start: the full (N, N) Pearson-correlation matrix of the
    MA-{w} curves over the window [s, s + w).

    Fully vectorized: ONE sliding-window gather + ONE batched BLAS
    matmul ((F, N, w) @ (F, w, N)) replaces the former per-start loop.
    NaN cells are zero-filled before the matmul — valid pairs (both
    columns fully defined over the window, per ``col_ok``) never touch a
    filled cell, so their sums are exact. Entries for invalid pairs are
    NaN-masked afterward.
    """
    n_starts = starts.size
    t_len, n_ind = ma.shape
    stack = np.full((n_starts, n_ind, n_ind), np.nan, dtype=np.float64)
    full = np.nonzero((starts + w) <= t_len)[0]
    if full.size == 0:
        return stack
    s_full = starts[full]
    # sliding_window_view appends the window axis LAST: (T-w+1, N, w).
    # Fancy-index the grid starts (materializes a copy), then transpose
    # to (F, w, N) contiguous so NaN->0 fill + matmuls run in place.
    x0 = np.ascontiguousarray(
        np.lib.stride_tricks.sliding_window_view(ma, w, axis=0)[s_full]
        .transpose(0, 2, 1)
    )
    np.copyto(x0, 0.0, where=np.isnan(x0))
    n_full = s_full.size
    # Backend routing — the batched (F, w, N) @ (F, w, N) matmul + einsums
    # are the intense block. Above the size threshold route through CuPy
    # (cuBLAS/cuTensor on GPU); any failure falls back to host numpy.
    corr: Optional[np.ndarray] = None
    est_bytes = x0.nbytes + n_full * n_ind * n_ind * 8
    if est_bytes >= _GPU_MIN_BYTES and _cupy_available():
        global _GPU_BACKEND_LOGGED
        try:
            import cupy as cp

            gx0 = cp.asarray(x0)
            gsx = gx0.sum(axis=1)                                 # (F, N)
            gsxx = cp.einsum("fwi,fwi->fi", gx0, gx0)             # (F, N)
            gsxy = gx0.transpose(0, 2, 1) @ gx0                   # (F, N, N)
            gcov = gsxy - cp.einsum("fi,fj->fij", gsx, gsx) / w
            gvar = gsxx - gsx * gsx / w
            # NOTE: cupy has NO errstate — zero-variance pairs simply
            # yield 0/0 -> NaN here, matching the numpy path's output.
            gcorr = gcov / cp.sqrt(cp.einsum("fi,fj->fij", gvar, gvar))
            corr = cp.asnumpy(gcorr)
            del gx0, gsx, gsxx, gsxy, gcov, gvar, gcorr
            release_cupy_pool()
            if not _GPU_BACKEND_LOGGED:
                print(f"    [corr] window-corr backend: cupy "
                      f"(est {est_bytes >> 20} MiB)", flush=True)
                _GPU_BACKEND_LOGGED = True
        except Exception as e:                                     # pragma: no cover
            print(f"    [corr] cupy failed ({type(e).__name__}: {e}) "
                  f"-> numpy CPU", flush=True)
            corr = None
            release_cupy_pool()
    if corr is None:
        sx: np.ndarray = x0.sum(axis=1)                        # (F, N)
        sxx: np.ndarray = np.einsum("fwi,fwi->fi", x0, x0)     # (F, N)
        sxy: np.ndarray = x0.transpose(0, 2, 1) @ x0           # (F, N, N)
        cov = sxy - np.einsum("fi,fj->fij", sx, sx) / w
        var = sxx - sx * sx / w
        with np.errstate(divide="ignore", invalid="ignore"):
            corr = cov / np.sqrt(np.einsum("fi,fj->fij", var, var))
    # Rows/cols whose MA-{w} is not defined over the window -> NaN.
    ok = col_ok[full]                                     # (F, N)
    bad = ~(ok[:, :, None] & ok[:, None, :])
    corr[bad] = np.nan
    stack[full] = corr
    return stack


def _round_none(arr: np.ndarray, nd: int) -> list:
    """Round to ``nd`` decimals; NaN/inf -> None (SQL NULL).

    Mirrors sanitize_for_db_insert's numeric-column semantics but stays
    in pure host numpy — no DataFrame round-trip, no cudf fallback.
    """
    a: np.ndarray = np.round(np.asarray(arr, dtype=np.float64), nd)
    bad = ~np.isfinite(a)
    if bad.any():
        oa = a.astype(object)
        oa[bad] = None
        return oa.tolist()
    return a.tolist()


async def run_correlations(
    conn,
    *,
    target_dates: Optional[Set[datetime.date]] = None,
    force: bool = False,
    industry_ids: Optional[Set[str]] = None,
) -> None:
    """Run the windowed MA-correlation pipeline against the
    stats.industry_basic_stats baseline table (built by builds.industry).

    Reuses the caller's DB connection (does not open/close its own) so the
    sentiments + correlations steps form a single atomic-ish batch.

    Pipeline (matrix arrangement — see module docstring)
      1. Load all (date, industry_id, pool_size, mean_close) rows from
         stats.industry_basic_stats (skipping rows where mean_close is
         NULL), with date as datetime64 (GPU-native). Full history is
         always loaded so MA curves and windows are correct.
      2. For each pool_size: pivot to a wide (date x industry) matrix.
      3. Per pool:
           - Pairwise overlap counts via ONE boolean matmul
             (valid.T @ valid) -> keep pairs with >= MIN_OVERLAP shared
             dates.
           - MA-{W} curves via wide.rolling(W).mean() (GPU-native).
           - Grid starts (stride INTERVAL_DAYS) + full-window validity
             per (start, industry) via a cumsum over the NaN mask.
           - 3D emit mask (S, N, N): upper-triangle pairs where ANY
             window is valid — in incremental mode restricted to
             windows whose END date is in target_dates; ONE np.nonzero
             yields the (start, industry, benchmark) triples.
           - Per window: ONE batched BLAS matmul over the grid starts
             gives the (S, N, N) corr stack; values gathered at the
             emitted cells by fancy indexing.
           - Row dicts built host-side from numpy columns
             (industry-major lexsort; rounded + None-masked).
      4. Truncate (force mode) + ONE key-batched write
         (batched_copy_by_key_async / copy_or_upsert_split_async).
      5. Upsert analysis.analysis_identity (name='industry_correlations').

    Args:
      target_dates: when non-empty, only rows whose window END date
        (start_date + W - 1) is in this set are upserted (incremental
        mode; see find_missing_corr_window_ends). Ignored when ``force``
        or ``industry_ids`` is set.
      force: when True, truncate the table first and recompute all rows.
        Incompatible with ``industry_ids`` (a filtered run must never
        truncate the whole table).
      industry_ids: when non-empty, FILTERED mode — recompute ALL
        windows for the pairs among these industries only (the loaded
        baseline rows are restricted to these industry_ids, so pairs
        form only within the set) and UPSERT them. No truncate is
        issued and ``target_dates`` / ``force`` are ignored. Used by
        the standalone ``python -m analyze.industry_sentiments.corr``
        entry point (--industry / --code args, driven by the UI
        refresh button).
    """
    t0 = time.time()
    print("\n" + "=" * 78, flush=True)
    print("  INDUSTRY CORRELATIONS (internal step of industry_sentiments)",
          flush=True)
    print("=" * 78, flush=True)

    filtered = industry_ids is not None and len(industry_ids) > 0
    if filtered and force:
        raise ValueError(
            "run_correlations: industry_ids filter cannot be combined "
            "with force=True (filtered runs must never truncate the "
            "whole table)"
        )
    incremental = (not force and not filtered
                   and target_dates is not None
                   and len(target_dates) > 0)
    if force:
        print("    mode: FORCE (full recompute)", flush=True)
    elif filtered:
        print(f"    mode: FILTERED ({len(industry_ids)} industries — "
              f"recompute all their windows, upsert)", flush=True)
    elif incremental:
        print(f"    mode: incremental ({len(target_dates)} target window-end "
              f"dates)", flush=True)

    # ---- Step 1: load mean_close series from industry_basic_stats ----
    # Only non-NULL mean_close rows are useful — NULL rows mean no
    # member indices contributed to that (date, industry, pool) slice
    # and cannot be correlated.
    print("\n[c1/4] Loading (date, industry_id, pool_size, mean_close) "
          "from stats.industry_basic_stats (non-NULL mean only)...",
          flush=True)
    if filtered:
        rows = await conn.fetch(f"""
            SELECT extract(epoch from date)::float8 AS date,
                   industry_id, pool_size, mean_close
            FROM {BASELINE_TABLE}
            WHERE mean_close IS NOT NULL
              AND industry_id = ANY($1)
            ORDER BY industry_id, pool_size, date
        """, sorted(industry_ids))
    else:
        rows = await conn.fetch(f"""
            SELECT extract(epoch from date)::float8 AS date,
                   industry_id, pool_size, mean_close
            FROM {BASELINE_TABLE}
            WHERE mean_close IS NOT NULL
            ORDER BY industry_id, pool_size, date
        """)
    n_series = len(set(zip(rec_col(rows, "industry_id"),
                           rec_col(rows, "pool_size"))))
    print(f"      -> {len(rows):,} rows across "
          f"{n_series} (industry, pool_size) series", flush=True)

    if not rows:
        print("      -> no data; skipping correlations step.", flush=True)
        return

    # Whole-column extraction via the shared helper (C-level itemgetter
    # map + one positional unpack — never a per-row python loop; see
    # project convention for DB Record -> column extraction).
    df = pd.DataFrame(rec_cols(rows))
    # datetime64[us] for GPU-native ops throughout — the date column
    # arrives as native float8 (extract(epoch) in SQL) and is
    # materialized via epoch_col_to_dt64 in ONE host pass (object python
    # dates would poison every downstream op into CPU fallbacks).
    # Converted back to python dates only in the emitted rows (asyncpg
    # boundary). mean_close arrives as Decimal (NUMERIC) — one
    # vectorized float cast replaces the former per-row float() loop.
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    df["mean_close"] = df["mean_close"].astype(float)

    # ---- Steps 2+3: per-pool wide matrix + windowed corr stacks -----
    # Partition-key batching — see module docstring. One pivot + ONE
    # boolean overlap matmul + per-(pool, window) matmul-per-grid-start
    # corr stacks; emit stays industry-major for the key-batched writes.
    print("\n[c2/4] Per-pool pivot to wide (date x industry) matrices...",
          flush=True)
    print(f"[c3/4] Windowed MA-corr stacks per (pool, window) "
          f"(windows={WINDOWS}, stride={INTERVAL_DAYS}d)...",
          flush=True)

    out_rows: list[dict] = []
    n_pairs_total = 0
    n_pairs_with_data = 0
    # target window-end dates as datetime64[D] (vectorizable isin).
    tgt64: np.ndarray = (
        np.asarray(sorted(target_dates), dtype="datetime64[D]")
        if incremental else np.array([], dtype="datetime64[D]")
    )
    for pool in POOL_SIZES:
        sub = df[df["pool_size"] == pool]
        if sub.empty:
            continue
        # Wide (date x industry) matrix; NaN before an industry's first
        # date. Columns are sorted lexicographically by pandas, so
        # position order == id order and (ai < bi) == (a_id < b_id).
        wide = sub.pivot(
            index="date", columns="industry_id", values="mean_close"
        ).sort_index()
        t_len, n_ind = wide.shape
        if n_ind < 2:
            continue
        n_pairs_total += n_ind * (n_ind - 1) // 2

        # np.asarray on cudf Index/Series is the clean (no-fallback)
        # host transfer path — Index .tolist()/.values/.to_numpy() are
        # NOT (see project memory).
        ids: np.ndarray = np.asarray(wide.columns)
        valid: np.ndarray = wide.notna().to_numpy()  # (T, N) bool
        sd_d: np.ndarray = np.asarray(wide.index).astype("datetime64[D]")

        # Pairwise overlap counts in ONE matmul — vectorized
        # replacement of the per-pair merge + length check. Pairs with
        # fewer than MIN_OVERLAP shared dates cannot produce a full
        # window even for the shortest MA, so computing and emitting
        # their (all-NULL) rows is pure waste.
        overlap: np.ndarray = (
            valid.astype(np.int64).T @ valid.astype(np.int64)
        )  # (N, N)

        # Grid window starts.
        starts: np.ndarray = _grid_start_indices(t_len)  # (S,)

        # MA-{W} curves (GPU-native rolling mean) + full-window validity.
        ma: dict[int, np.ndarray] = {
            w: wide.rolling(w, min_periods=w).mean().to_numpy()
            for w in WINDOWS
        }
        col_ok: dict[int, np.ndarray] = {
            w: _window_col_ok(ma[w], starts, w) for w in WINDOWS
        }

        # Incremental: per window, is the window END date a target date?
        # (end index starts + w - 1; only full windows count.)
        end_in_target: dict[int, np.ndarray] = {}
        if incremental:
            for w in WINDOWS:
                ends = starts + w - 1
                in_cal = ends < t_len
                end_dates = sd_d[np.minimum(ends, t_len - 1)]
                end_in_target[w] = in_cal & np.isin(end_dates, tgt64)

        # 3D emit mask (S, N, N): upper-triangle pairs with ANY valid
        # window — ONE vectorized np.nonzero replaces the former
        # per-industry base-frame loop (234 DataFrame constructors +
        # per-cell setitem fallbacks). Row-major np.nonzero yields
        # (s_idx, ai_idx, bi_idx) triples directly.
        pair_ok: np.ndarray = np.triu(overlap >= MIN_OVERLAP, k=1)  # (N, N)
        emit3: np.ndarray = np.zeros(
            (starts.size, n_ind, n_ind), dtype=bool
        )
        for w in WINDOWS:
            ok = col_ok[w]  # (S, N)
            emit3 |= ok[:, :, None] & ok[:, None, :]
        emit3 &= pair_ok[None, :, :]
        if incremental:
            touch3: np.ndarray = np.zeros_like(emit3)
            for w in WINDOWS:
                ok = col_ok[w]
                touch3 |= (
                    ok[:, :, None] & ok[:, None, :]
                    & end_in_target[w][:, None, None]
                )
            emit3 &= touch3
        s_idx, ai_idx, bi_idx = np.nonzero(emit3)

        pool_pairs = int(pair_ok.sum())
        n_pairs_with_data += pool_pairs
        pool_rows = int(s_idx.size)
        if s_idx.size == 0:
            print(f"      [{pool:5s}] {t_len:,} dates x {n_ind} industries "
                  f"-> {pool_pairs:,}/{n_ind * (n_ind - 1) // 2:,} pairs "
                  f"(overlap >= {MIN_OVERLAP}), {starts.size:,} grid "
                  f"starts, {pool_rows:,} rows",
                  flush=True)
            continue

        # Corr values per window at the emitted cells only (fancy
        # indexing into the (S, N, N) stacks).
        corr_vals: dict[int, np.ndarray] = {}
        for w in WINDOWS:
            stack = _window_corr_stack(ma[w], starts, w, col_ok[w])
            corr_vals[w] = stack[s_idx, ai_idx, bi_idx]

        # Deterministic industry-major ordering (industry, benchmark,
        # start_date) so key-grouped chunks stream contiguous runs.
        order = np.lexsort((s_idx, bi_idx, ai_idx))
        ind_l = ids[ai_idx[order]].tolist()
        bench_l = ids[bi_idx[order]].tolist()
        # s_idx indexes the (S,) grid starts — map through `starts` to
        # calendar dates. datetime64[D] -> python date objects at the
        # asyncpg boundary.
        sd_l = sd_d[starts[s_idx[order]]].astype(object).tolist()
        val_l = [_round_none(corr_vals[w][order], 4) for w in WINDOWS]

        out_rows.extend(
            {
                "industry_id": a,
                "benchmark_industry_id": b,
                "pool_size": pool,
                "start_date": d,
                "interval": INTERVAL_DAYS,
                "corr_ma20_20d": c20,
                "corr_ma60_60d": c60,
                "corr_ma255_255d": c255,
            }
            for a, b, d, c20, c60, c255 in zip(
                ind_l, bench_l, sd_l, val_l[0], val_l[1], val_l[2],
            )
        )

        print(f"      [{pool:5s}] {t_len:,} dates x {n_ind} industries -> "
              f"{pool_pairs:,}/{n_ind * (n_ind - 1) // 2:,} pairs "
              f"(overlap >= {MIN_OVERLAP}), {starts.size:,} grid starts, "
              f"{pool_rows:,} rows",
              flush=True)

    # ---- Sanitize (in the row builder above) + ONE key-batched write -
    # Row dicts are built host-side directly from numpy columns (see
    # _round_none) — zero DataFrame round-trips, zero cudf fallbacks in
    # the emit/sanitize path. ONE accumulated call per write mode: the
    # key-batched writer re-groups rows into whole-industry chunks
    # (~100K rows) itself, so 757K rows become ~8 COPY round trips
    # instead of 234 per-industry ones.
    total_rows = len(out_rows)
    print(f"      -> {n_pairs_total} industry pairs x up to 4 pools "
          f"= up to {n_pairs_total * 4} (pair, pool) combinations; "
          f"{n_pairs_with_data} had >= {MIN_OVERLAP} overlapping dates",
          flush=True)
    print(f"      -> {total_rows:,} correlation rows emitted"
          f"{' (target window-end dates filtered)' if incremental else ''}",
          flush=True)

    if not out_rows:
        print("      -> no rows to upsert; skipping correlations upsert.",
              flush=True)
        return

    # ---- Step 4: truncate (force only) + insert ---------------------
    if force:
        print(f"\n[c4/4] Truncating {TABLE} and key-batched-COPY-inserting "
              f"{total_rows:,} rows (batch key = industry_id)...",
              flush=True)
        await truncate_table_async(conn, TABLE)
        n = await batched_copy_by_key_async(
            conn, TABLE, out_rows, key="industry_id",
            label="correlations",
        )
        via = "key-batched COPY (force)"
    else:
        print(f"\n[c4/4] Upserting {total_rows:,} rows into {TABLE} "
              f"(target windows)...", flush=True)
        n_copied, n_upserted = await copy_or_upsert_split_async(
            conn, TABLE, out_rows,
            key_columns=[
                "industry_id",
                "benchmark_industry_id",
                "pool_size",
                "start_date",
                "interval",
            ],
            date_column="start_date",
        )
        n = n_copied + n_upserted
        via = "COPY" if n_copied > 0 and n_upserted == 0 else \
              f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else \
              "upsert"
    print(f"      -> inserted {n:,} rows via {via}", flush=True)

    # ---- Register in analysis.analysis_identity ----------------------
    await upsert_analysis_identity(
        conn,
        name=ANALYSIS_NAME,
        detail_name=ANALYSIS_NAME,
        description=ANALYSIS_DESCRIPTION,
    )

    # Sanity summary: row count by pool_size.
    summary = await conn.fetch(f"""
        SELECT pool_size AS pool,
               COUNT(*) AS n_rows,
               COUNT(DISTINCT (industry_id, benchmark_industry_id))
                   AS n_pairs,
               MIN(start_date) AS first_date,
               MAX(start_date) AS last_date
        FROM {TABLE}
        GROUP BY pool_size
        ORDER BY pool_size
    """)
    print("\n      Summary by pool_size:", flush=True)
    for r in summary:
        print(f"        {r['pool']:6s}: {r['n_rows']:>8,} rows . "
              f"{r['n_pairs']:>4} pairs . "
              f"{r['first_date']} -> {r['last_date']}", flush=True)

    print(f"\n  correlations wall time: {time.time() - t0:.1f}s", flush=True)
