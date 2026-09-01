"""Python bridge for ema.cpp — adjust=False EMA as one fused CUDA launch.

Compiles ``ema.cpp`` (next to this file) at import time via
``cupy.RawModule`` (NVRTC — once per process, and once per script start
because ``builds/__init__`` eagerly imports this package), then computes

    y_t = a*G_t + d^(p+1)*x0,   G_t = sum_{j < min(T, p+1)} d^j * x_{t-j}

per span, where ``p`` is the 0-based row position within its contiguous
group and ``T`` is the smallest power bound with ``d^T < 1e-12``.

Parity: matches pandas ``ewm(span, adjust=False, min_periods=1)`` to
<=1e-12 relative (validated vs pandas on random walks and warm-up heads;
see temp_scripts/debug_ema_kernel.py).

Contract (caller obligations):
    - groups are CONTIGUOUS blocks of rows (pre-sort by [group_key, date])
    - ``pos_int64`` = 0-based position within each group (cumcount)
    - ``x`` is NaN-free float64 (NaN would propagate through the sum)
"""
from __future__ import annotations

import math
from importlib.resources import files
from typing import TYPE_CHECKING

import cupy as cp

if TYPE_CHECKING:
    import numpy as np

# EMA truncation: window grows until d^T < 1e-12 (matches
# _common.df_utils.rolling._EMA_LOG_EPS)
_LOG_EPS: float = math.log(1e-12)

_THREADS = 256


def _compile_module() -> cp.RawModule:
    """Compile ema.cpp at import time (NVRTC; fails fast, no lazy hit)."""
    try:
        src = (
            files("builds._algo_kernels").joinpath("ema.cpp").read_text("utf-8")
        )
        return cp.RawModule(code=src)
    except Exception as e:
        raise ImportError(f"failed to compile builds/_algo_kernels/ema.cpp: {e}") from e


# eager compile at import: any script importing this bridge pays NVRTC
# once at start, never mid-run
_MODULE: cp.RawModule = _compile_module()


def max_terms(span: int) -> int:
    """Truncation bound T for a span: smallest power of two with d^T < 1e-12.

    Power-of-two rounding matches the shift-doubling path's effective
    window, keeping the discarded tail (d^T * x / (1-d)) at ~1e-16 —
    truncating at the exact bound instead leaves a ~1e-9 tail that
    flips 6-decimal rounding boundaries vs pandas ewm.

    The kernel's pairwise-sum stack covers T up to 2^14; spans beyond
    that (d^16384 >= 1e-12 needs d >= 0.999436, span >= ~3540) raise.
    """
    d = max(1.0 - 2.0 / (span + 1.0), 1e-300)   # guards span=1 -> d=0
    exact = math.ceil(_LOG_EPS / math.log(d))
    t = 1 << (exact - 1).bit_length()
    if t > 1 << 14:
        raise ValueError(f"span {span} needs {t} terms > kernel max 16384")
    return t


def ema_adjust_false(
    x: cp.ndarray,
    pos_int64: cp.ndarray,
    *,
    alpha: float,
    span: int,
    out: cp.ndarray | None = None,
) -> cp.ndarray:
    """One-span fused EMA over x (device float64), one kernel launch.

    Args:
        x: values, float64, NaN-free, groups contiguous (device mem).
        pos_int64: 0-based position within each group (device mem).
        alpha: smoothing factor ``2 / (span + 1)``.
        span: EMA span (used only for the truncation bound; keep alpha
            and span consistent — alpha is passed explicitly so the
            caller can hoist it out of per-call recomputation).
        out: optional preallocated float64 destination (n,).

    Returns:
        The EMA series (``out`` if given), same length as x.
    """
    n = x.size
    if pos_int64.size != n:
        raise ValueError(
            f"pos_int64 size {pos_int64.size} != x size {n}")
    if out is None:
        out = cp.empty(n, dtype=cp.float64)
    kern = _MODULE.get_function("ema_adjust_false")
    blocks = (n + _THREADS - 1) // _THREADS
    kern((blocks,), (_THREADS,),
         (x, pos_int64, out,
          cp.float64(alpha), cp.float64(max(1.0 - alpha, 1e-300)),
          cp.int32(max_terms(span)), cp.int32(n)))
    return out
