"""Python bridge for grouped_ewm.cpp — grouped EWM as one CUDA launch.

Compiles ``ewm.cpp`` (next to this file) at import time via
``cupy.RawModule`` (NVRTC — once per process), then computes the
grouped EWM mean (adjust=False, ignore_na=True) per contiguous group:

    y_0 = x_first_valid
    y_t = (1 - alpha) * y_{t-1} + alpha * x_t     (valid inputs only)

with pandas ``min_periods`` semantics (output NaN until min_periods
valid observations, NaNs excluded from the count; NaN inputs emit NaN
output and leave the recurrence state unchanged).

Parity: bit-identical to pandas
``groupby(...).ewm(alpha, adjust=False, min_periods,
ignore_na=True).mean()`` — verified in temp_scripts/test_ewm_kernel.py
(NaN gaps, warm-up heads, sub-min_periods groups, 6/255 windows).

Contract (caller obligations):
    - groups are CONTIGUOUS blocks of rows (df pre-sorted by
      [group_key, date]; rsi.py's compute_rsi_and_gaps guarantees this)
    - ``starts``/``ends`` = int64 exclusive row bounds per group
    - import this module ONLY when the GPU path is confirmed available
      (rsi.py imports it lazily inside the GPU branch)
"""
from __future__ import annotations

import cupy as cp

_THREADS = 256


def _compile_module() -> cp.RawModule:
    """Compile ewm.cpp at import time (NVRTC; fails fast, no lazy hit)."""
    try:
        from importlib.resources import files

        src = (
            files("analyze.mov_ave_spread._kernels")
            .joinpath("ewm.cpp")
            .read_text("utf-8")
        )
        return cp.RawModule(code=src)
    except Exception as e:
        raise ImportError(
            f"failed to compile analyze/mov_ave_spread/_kernels/ewm.cpp: {e}"
        ) from e


# eager compile at import: any caller pays NVRTC once per process,
# never mid-run
_MODULE: cp.RawModule = _compile_module()


def grouped_ewm_mean(
    x: cp.ndarray,
    starts: cp.ndarray,
    ends: cp.ndarray,
    *,
    alpha: float,
    min_periods: int,
    out: cp.ndarray | None = None,
) -> cp.ndarray:
    """Grouped EWM mean over contiguous groups, one kernel launch.

    Args:
        x: values, float64 device memory; NaN inputs are skipped
            (ignore_na=True semantics) and produce NaN outputs.
        starts: int64 device array, first row index of each group.
        ends: int64 device array, one-past-last row index of each group.
        alpha: smoothing factor (Wilder: ``1 / window``).
        min_periods: pandas min_periods (valid observations, NaNs
            excluded; output NaN until reached).
        out: optional preallocated float64 destination (n,).

    Returns:
        The EWM series (``out`` if given), same length as x; output row
        order matches input row order (groups are contiguous blocks).
    """
    n = x.size
    n_groups = starts.size
    if starts.size != ends.size:
        raise ValueError(
            f"starts size {starts.size} != ends size {ends.size}")
    if ends.size and int(ends[-1].get()) != n:
        raise ValueError(
            f"ends[-1] {int(ends[-1].get())} != x size {n}")
    if out is None:
        out = cp.empty(n, dtype=cp.float64)
    kern = _MODULE.get_function("grouped_ewm_adjust_false_ignore_na")
    blocks = (n_groups + _THREADS - 1) // _THREADS
    kern((blocks,), (_THREADS,),
         (x, starts, ends, out,
          cp.float64(alpha), cp.int32(min_periods), cp.int32(n_groups)))
    return out
