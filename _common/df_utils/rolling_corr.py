"""Pairwise rolling Pearson correlation — CuPy fallback for the one
rolling op cuDF does not implement.

WHY THIS MODULE EXISTS
======================
cuDF (any release, incl. 26.08) implements ``rolling`` only for
count/sum/mean/min/max/std/var — ``rolling().corr()`` raises
NotImplementedError and cudf.pandas transparently falls back to the
SLOW pandas CPU path (profiled ~8 s/M rows). For pairwise correlation
over a wide (date x entity) frame the true workload is T x N x N
pair-window operations (e.g. 1700 dates x 94 industries = 15M ops per
window), all of which are simple running-sum algebra — an ideal CuPy
batched-GPU kernel.

This helper computes the FULL pairwise tensor in one batched pass:

    tensor[t, i, j] = Pearson corr of columns i and j over the
                      trailing ``window`` rows ending at row t

with pandas-exact semantics (validated cell-by-cell against
``wide.rolling(window, min_periods).corr()``):

  - NaN values are excluded PAIRWISE (a date counts for pair (i, j)
    only when BOTH columns are non-NaN).
  - PARTIAL windows count: for t < window-1 the trailing window is
    truncated to the available rows, exactly like pandas rolling.
  - A value is emitted only when the pair has >= ``min_periods``
    joint-valid observations in the (possibly truncated) window.
  - Zero variance in either series (denominator 0) -> NaN.

BACKEND ROUTING (three tiers — never hit slow pandas unless tiny)
==================================================================
  1. CuPy GPU   — when cupy imports, a CUDA device exists, the
     pair-window workload is >= the ``rolling_corr`` breakeven, and the
     tensor working set fits free VRAM. One H2D transfer of the (T, N)
     matrix + one D2H transfer of the (T, N, N) result per call.
  2. numpy CPU  — the SAME batched cumsum-algebra kernel running on
     numpy (the kernel body is array-module agnostic). Used when the
     cudf.pandas proxy hook is installed but the CuPy path is not
     taken (tensor exceeds the VRAM cap, or below the GPU breakeven):
     there, native pandas would route through the proxy, hit cuDF's
     missing Rolling.corr, and pay a fast->slow fallback transfer per
     call. On PURE-CPU hosts (no hook) native pandas' Cython pairwise
     corr is the fastest CPU backend (measured 1733x98: 1.4 s vs 2.4 s
     per window) and is kept.
  3. pandas CPU — pure-CPU hosts (no cudf.pandas hook) and tiny inputs
     where kernel launch/bookkeeping overhead dominates: the validated
     no-arg pairwise ``wide.rolling(...).corr()`` path.

float64 throughout: cumsum rounding differences vs pandas' incremental
window sums are ~1e-12 — far below the 4-decimal DB rounding, though a
value sitting exactly on a rounding boundary may flip its last digit.

The input ``wide`` must have a sorted ascending index and columns are
sorted internally (both no-ops for the pivot-produced frames callers
pass); the returned tensor's [i, j] slots follow the sorted column
order, i.e. ``wide.sort_index().sort_index(axis=1).columns``.
"""
from __future__ import annotations

import sys

import numpy as np

from _common.df_utils._thresholds import (
    CONSERVATIVE_MULTIPLIER,
    breakeven_rows,
)

# Estimated VRAM for the CuPy path, in float64 (T, N, N) tensors alive
# simultaneously (joint-validity bool + xm/ym + one running-sum chain).
_CUPY_TENSOR_OVERHEAD = 6

# Fraction of FREE VRAM the tensor working set must fit under (leaves
# headroom for cuDF/cupy internals and concurrent GPU processes).
_VRAM_USAGE_CAP = 0.75

_cupy_ok: bool | None = None


def _cupy_available() -> bool:
    """Cached CuPy + CUDA device check (import cost paid once)."""
    global _cupy_ok
    if _cupy_ok is None:
        try:
            import cupy as cp  # noqa: F401

            cp.cuda.runtime.getDeviceCount()
            _cupy_ok = True
        except Exception:
            _cupy_ok = False
    return _cupy_ok


def release_cupy_pool() -> None:
    """Return all cached CuPy pool blocks to the CUDA driver.

    CuPy's default allocator caches freed device arrays in a memory
    pool; pooled VRAM stays allocated — invisible as free to
    ``cp.cuda.runtime.memGetInfo`` and to OTHER processes — until
    process exit. GPU compute in this repo happens in short bursts
    (rolling-corr tensors, FFT) separated by long CPU/DB-bound phases,
    so without this release each process parks its peak burst working
    set (GBs of (T, N, N) float64 tensors) on the GPU while doing zero
    GPU work; concurrent analyze processes then exhaust VRAM and
    degrade to the numpy CPU kernel (observed: 3 concurrent jobs filled
    32 GB with near-idle GPU-Util between bursts).

    Best-effort: no-op when CuPy is unavailable or nothing is pooled.
    """
    try:
        import cupy as cp

        cp.get_default_memory_pool().free_all_blocks()
        cp.get_default_pinned_memory_pool().free_all_blocks()
    except Exception:
        pass


def _pandas_tensor(wide, window: int, min_periods: int) -> np.ndarray:
    """CPU fallback: pandas' native pairwise rolling corr, reshaped.

    ``wide.rolling(window, min_periods).corr()`` returns a
    (T*N, N) panel with a (date, column) MultiIndex; to_numpy() is
    date-major row-major, so reshape yields tensor[t, i, j] directly.
    """
    t_len, n_ind = wide.shape
    panel = wide.rolling(window, min_periods=min_periods).corr()
    return panel.to_numpy().reshape(t_len, n_ind, n_ind)


def _cupy_tensor(arr: np.ndarray, window: int, min_periods: int) -> np.ndarray:
    """CuPy batched pairwise rolling corr — see :func:`_tensor_kernel`."""
    import cupy as cp

    return _tensor_kernel(cp, arr, window, min_periods)


def _numpy_tensor(arr: np.ndarray, window: int, min_periods: int) -> np.ndarray:
    """numpy batched pairwise rolling corr — see :func:`_tensor_kernel`.

    Same kernel as the CuPy path (the algebra uses only xp-agnostic
    ops). On CPU-only hosts this replaces pandas' per-pair rolling loop
    (N^2 Cython rolling iterations, ~8 s/M rows) with 6 vectorized
    cumsum passes over (T, N, N) — ~20x faster at production scale.
    """
    return _tensor_kernel(np, arr, window, min_periods)


def _tensor_kernel(xp, arr: np.ndarray, window: int,
                   min_periods: int) -> np.ndarray:
    """Batched pairwise rolling corr over a (T, N) float64 matrix.

    ``xp`` is the array module (``cupy`` or ``numpy``) — every op used
    below exists identically in both.

    Algebra per pair (i, j) and window end t (partial-window aware):

        n   = #joint-valid obs      num = n*Sxy - Sx*Sy
        Sx  = sum m*x_i             d1  = n*Sxx - Sx*Sx
        Sy  = sum m*x_j             d2  = n*Syy - Sy*Sy
        Sxy = sum m*x_i*x_j         corr = num / sqrt(d1*d2)
        Sxx = sum m*x_i^2             (NaN where n < min_periods or
        Syy = sum m*x_j^2              d1*d2 <= 0, i.e. zero variance)

    All window sums come from ONE cumsum per operand along the time
    axis, differenced with partial-window-aware start offsets — every
    pair and every date computed in a handful of vectorized kernels.

    PRECISION: columns are mean-centered first (Pearson corr is
    shift-invariant, so this changes nothing mathematically). Without
    centering, cumsum prefixes of ~100-magnitude rebased prices lose
    ~1e-7 relative precision to cancellation in ``n*Sxx - Sx*Sx``;
    centered prefixes (~sigma magnitude) keep errors ~1e-12.

    DEGENERATE WINDOWS -> NaN (three guards):
      1. zero variance (d1*d2 <= 0 after clamping) — pandas emits
         +/-inf or 0-noise there; downstream both become SQL NULL
         (sanitize maps inf and NaN to NULL identically).
      2. exactly-stale windows — stale composite indices repeat the
         exact same value for days; the true variance is exactly 0 but
         cumsum rounding leaks ~1e-9 into d1/d2, producing
         noise/noise ratios up to +/-184. Detected exactly (all
         consecutive pairs in the window valid and identical) -> NaN.
      3. |corr| > 1 + 1e-9 — mathematically impossible; any such value
         is num/den rounding garbage on a near-degenerate window.
    """
    t_len, n_ind = arr.shape
    x = xp.asarray(arr)  # (T, N); no-op for numpy, the single H2D for cupy
    valid = ~xp.isnan(x)  # (T, N)
    # Mean-center each column over its valid values (shift-invariance
    # of Pearson corr makes this exact math, purely for precision).
    xs = xp.where(valid, x, 0.0)
    n_valid = valid.sum(axis=0)  # (N,)
    col_mean = xs.sum(axis=0) / xp.maximum(n_valid, 1)  # (N,)
    xc = xs - col_mean[None, :]  # invalid positions hold -mean, masked below

    # ---- Exactly-stale window detection ---------------------------
    # Stale composite indices repeat the EXACT same value for days. A
    # window of identical values has exactly zero variance, but the
    # cumsum-diff arithmetic leaks ~1e-9 rounding into d1/d2, making
    # den = sqrt(tiny*tiny) and corr = noise/noise = arbitrary
    # (observed up to +/-184 in production data). Detect the exact
    # case — every consecutive pair inside the (partial) window is
    # valid and identical — and emit NaN (corr undefined), matching
    # the den == 0 branch. pandas' incremental window sums preserve
    # the exact zero and emit 0/NaN there instead.
    if t_len >= 2:
        eq = (valid[1:] & valid[:-1]
              & (x[1:] == x[:-1]))  # (T-1, N) consecutive identical
        neq = xp.zeros((t_len, n_ind), dtype=xp.float64)
        neq[1:] = (~eq).astype(xp.float64)
        c_neq = xp.cumsum(neq, axis=0)
        zero = xp.zeros((1, n_ind), dtype=xp.float64)
        c_neq = xp.concatenate([zero, c_neq], axis=0)  # (T+1, N)
        t_idx = xp.arange(t_len)
        ends2 = c_neq[1:]  # c[t+1]
        starts2 = c_neq[xp.minimum(xp.maximum(1, t_idx - window + 2),
                                   t_len)]  # c[max(1, t-W+2)]
        # # of unequal consecutive pairs inside window [max(0,t-W+1), t]
        stale = (ends2 - starts2) == 0.0  # (T, N)
    else:
        stale = xp.zeros((t_len, n_ind), dtype=bool)

    # Joint validity m[t, i, j] = valid[t, i] & valid[t, j]  -> (T, N, N)
    m = valid[:, :, None] & valid[:, None, :]
    mf = m.astype(xp.float64)
    # Each operand masked to 0 where the PAIR is invalid, so cumsums
    # accumulate only pairwise-valid contributions.
    xm = xp.where(m, xc[:, :, None], 0.0)  # x_i, joint-masked
    ym = xp.where(m, xc[:, None, :], 0.0)  # x_j, joint-masked

    def _window_sums(t):
        """Trailing-window sums ending at each t (partial windows count).

        cumsum with a prepended zero row c[0..T]; window sum at t is
        c[t+1] - c[max(0, t+1-window)] — truncated windows at the head
        match pandas rolling semantics exactly.
        """
        c = xp.cumsum(t, axis=0)
        zero = xp.zeros((1,) + t.shape[1:], dtype=xp.float64)
        c = xp.concatenate([zero, c], axis=0)  # (T+1, N, N)
        ends = c[1:]  # c[t+1], t = 0..T-1
        n_tail = max(0, t.shape[0] - window + 1)
        starts = xp.concatenate(
            [xp.zeros((window - 1,) + t.shape[1:], dtype=xp.float64),
             c[:n_tail]],
            axis=0,
        )  # c[max(0, t+1-window)], length T
        return ends - starts

    n_obs = _window_sums(mf)
    sx = _window_sums(xm)
    sy = _window_sums(ym)
    sxx = _window_sums(xm * xm)
    syy = _window_sums(ym * ym)
    sxy = _window_sums(xm * ym)
    del xm, ym, mf  # free ~3 tensors before the final arithmetic

    num = n_obs * sxy - sx * sy
    d1 = xp.maximum(n_obs * sxx - sx * sx, 0.0)
    d2 = xp.maximum(n_obs * syy - sy * sy, 0.0)
    den = xp.sqrt(d1 * d2)
    # Undefined where: too few joint-valid obs, zero variance (incl.
    # exactly-stale windows), or EITHER series stale in the window.
    stale_pair = stale[:, :, None] | stale[:, None, :]  # (T, N, N)
    ok = (n_obs >= min_periods) & (den > 0.0) & ~stale_pair
    corr = num / xp.where(den > 0.0, den, 1.0)
    # Mathematical impossibility clamp: true |corr| <= 1, so anything
    # beyond 1 + 1e-9 (float noise margin) is num/den rounding garbage
    # on a near-degenerate window -> NaN.
    ok &= xp.abs(corr) <= 1.0 + 1e-9
    corr = xp.where(ok, corr, xp.nan)
    if xp is np:
        return corr  # already host memory
    return corr.get()  # (T, N, N) — the single D2H transfer


def pairwise_rolling_corr(
    wide,
    window: int,
    *,
    min_periods: int = 2,
    verbose: bool = True,
) -> np.ndarray:
    """(T, N, N) rolling Pearson correlation tensor of all column pairs.

    Args:
        wide: DataFrame with a sorted ascending index (e.g. dates) and
            numeric columns (entities). Columns are sorted internally;
            the tensor's [i, j] slots follow that sorted column order.
        window: trailing window length in rows.
        min_periods: minimum joint-valid observations required to emit
            a correlation (pandas semantics; partial windows count).
        verbose: print the backend decision once per call.

    Returns:
        float64 ndarray of shape (T, N, N); symmetric in (i, j); NaN
        on the diagonal only when a column is constant/insufficient.
    """
    wide = wide.sort_index().sort_index(axis=1)
    t_len, n_ind = wide.shape
    if t_len == 0 or n_ind == 0:
        return np.full((t_len, n_ind, n_ind), np.nan)

    arr = wide.to_numpy(dtype=np.float64)
    pair_window_ops = max(1, t_len * n_ind * n_ind)

    # numpy-kernel threshold: the THEORETICAL rolling_corr breakeven
    # (undo the 4x conservative multiplier used for the GPU decision —
    # the numpy kernel pays no H2D/D2H transfer, only vectorized CPU
    # cumsums, so it beats per-pair Cython rolling almost immediately).
    numpy_min_ops = max(1, int(breakeven_rows("rolling_corr")
                               / CONSERVATIVE_MULTIPLIER))

    # Is the cudf.pandas import hook installed in THIS process? When it
    # is, ``_pandas_tensor``'s wide.rolling().corr() goes through the
    # proxy, hits cuDF's missing Rolling.corr, and pays a fast->slow
    # fallback (H2D/D2H churn) on EVERY call — the numpy kernel avoids
    # that entirely. On a pure-CPU host (no hook) native pandas' Cython
    # pairwise corr is measured FASTER than the numpy kernel at
    # production shapes (1733 x 98: 1.4 s vs 2.4 s per window), so
    # pandas stays the CPU backend there.
    cudf_hook = "cudf.pandas" in sys.modules

    backend = "pandas (CPU)"
    reason = ""
    if not _cupy_available():
        if cudf_hook and pair_window_ops >= numpy_min_ops:
            backend = "numpy (CPU)"
        elif not cudf_hook:
            pass  # native pandas is the fastest CPU backend here
        else:
            reason = (f"{pair_window_ops:,} pair-window ops below numpy "
                      f"kernel breakeven ({numpy_min_ops:,})")
    elif pair_window_ops < breakeven_rows("rolling_corr"):
        if pair_window_ops >= numpy_min_ops:
            backend = "numpy (CPU)"
            reason = (f"{pair_window_ops:,} pair-window ops below GPU "
                      f"breakeven")
        else:
            reason = (f"{pair_window_ops:,} pair-window ops below "
                      f"breakevens")
    else:
        import cupy as cp

        free_vram, _total = cp.cuda.runtime.memGetInfo()
        est_bytes = _CUPY_TENSOR_OVERHEAD * pair_window_ops * 8
        if est_bytes > free_vram * _VRAM_USAGE_CAP:
            backend = "numpy (CPU)"
            reason = (f"tensor {est_bytes / 1e9:.1f} GB exceeds free "
                      f"VRAM cap ({free_vram / 1e9:.1f} GB free)")
        else:
            backend = "cupy (GPU)"

    if verbose:
        note = f" — {reason}" if reason else ""
        print(f"    [rolling_corr] {t_len:,} rows x {n_ind} cols, "
              f"window {window}: {pair_window_ops:,} pair-window ops "
              f"-> {backend}{note}", flush=True)

    if backend == "cupy (GPU)":
        try:
            tensor = _cupy_tensor(arr, window, min_periods)
            # All device temporaries are pool-freed by now — return the
            # pooled blocks to the driver so the long CPU/DB phases (and
            # concurrent GPU processes) see the VRAM as free again.
            release_cupy_pool()
            return tensor
        except MemoryError:
            # Concurrent GPU processes can shrink free VRAM between the
            # check and the allocation — release whatever was pooled,
            # then degrade to the numpy kernel (still much faster than
            # the proxied pandas path).
            release_cupy_pool()
            print("    [rolling_corr] cupy MemoryError -> numpy CPU "
                  "kernel", flush=True)
            return _numpy_tensor(arr, window, min_periods)
    if backend == "numpy (CPU)":
        return _numpy_tensor(arr, window, min_periods)
    return _pandas_tensor(wide, window, min_periods)
