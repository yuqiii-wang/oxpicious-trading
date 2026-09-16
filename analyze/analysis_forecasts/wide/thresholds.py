"""Per-code, per-horizon reversal bars (analyze.analysis_forecasts
.wide.thresholds).

The bar a bucket's reverse_prob is computed against for one stat month's
window: the fixed REVERSE_THRESHOLD constant in "fixed" mode (2026-09
default), or the adaptive k·σ of the code's own window forward changes
in "std" mode. Host-numpy reductions over the window-sliced (T, C)
change matrices — the window moment math is plain array algebra.
"""
from __future__ import annotations

import numpy as np

from analyze.analysis_forecasts.config import (
    FORWARD_HORIZONS,
    REVERSE_THRESHOLD,
    REVERSE_THRESHOLD_MODE,
    REVERSE_THRESHOLD_STD_K,
    REVERSE_THRESHOLD_STD_MIN_DAYS,
)


def window_sigmas(
    NC0s: dict[int, np.ndarray],
    FINs: dict[int, np.ndarray],
) -> tuple[dict[int, np.ndarray], dict[int, np.ndarray]]:
    """Per-horizon population σ and valid-day count of the window's
    forward changes, per code — (C,) arrays.

    σ is the dispersion of the n-day forward changes over ALL of the
    code's window days (the base_rates population — NOT the bucket
    days), the same quantity base_ave_change averages over. NaN σ where
    the code has no valid window day.
    """
    sigma: dict[int, np.ndarray] = {}
    cnts: dict[int, np.ndarray] = {}
    for n in FORWARD_HORIZONS:
        fin = FINs[n]
        cnt = fin.sum(axis=0)
        # NC0 is 0.0 on invalid days — masked sums equal valid-day sums
        # (same trick as aggregate_horizons_sparse).
        g = np.where(fin, NC0s[n], 0.0)
        s = g.sum(axis=0)
        s2 = (g * g).sum(axis=0)
        with np.errstate(divide="ignore", invalid="ignore"):
            var = s2 / cnt - (s / cnt) ** 2
        sig = np.sqrt(np.maximum(var, 0.0))
        sigma[n] = np.where(cnt > 0, sig, np.nan)
        cnts[n] = cnt
    return sigma, cnts


def reverse_thresholds(
    sigma: dict[int, np.ndarray],
    cnts: dict[int, np.ndarray],
) -> dict[int, np.ndarray]:
    """Per-horizon (C,) reversal bar for one stat month's window.

    "std" mode: thr[n] = REVERSE_THRESHOLD_STD_K[n] · σ_n — adaptive per
    code/horizon (no look-ahead: the window ends at the stat month).
    "fixed" mode or degenerate σ (non-finite / ≤ 0 / fewer than
    REVERSE_THRESHOLD_STD_MIN_DAYS valid days): the legacy constant
    REVERSE_THRESHOLD. Returned arrays are finite everywhere, so
    threshold comparisons never see NaN.
    """
    thr: dict[int, np.ndarray] = {}
    for n in FORWARD_HORIZONS:
        if REVERSE_THRESHOLD_MODE != "std":
            thr[n] = np.full(cnts[n].shape, REVERSE_THRESHOLD)
            continue
        k = REVERSE_THRESHOLD_STD_K[n]
        ok = (
            np.isfinite(sigma[n])
            & (sigma[n] > 0)
            & (cnts[n] >= REVERSE_THRESHOLD_STD_MIN_DAYS)
        )
        thr[n] = np.where(ok, k * sigma[n], REVERSE_THRESHOLD)
    return thr
