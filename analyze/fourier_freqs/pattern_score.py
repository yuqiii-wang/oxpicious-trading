"""Batched periodic-pattern score factors — the SEPARATION by count & amp.

Python port (vectorized over windows) of the consolidated periodic-pattern
audit previously computed client-side
(data_viz/src/analysis/pages/FourierFreqs/patternScore.ts — semantics
validated against the browser by _verify_port.py). For each sliding
window and each integer day frequency d the audit produces:

  count(d)    = recEXT(d) × acfFrac(d)               — the COUNT factor
                recEXT  — prominence-filtered alternating-extrema
                          evidence: pool hits within ±15% of d, over the
                          max possible cycles floor((N−d)/d), capped 1
                acfFrac — MA-detrended ACF recurrence: fraction of the
                          multiples m·d with biased acf ≥ 1.96/√N
  strength(d) = (amp(d)/σ_band) × count(d)           — the AMP × COUNT
                summarized strength; this IS the former consolidated
                "pattern score". amp(d) is the energy-merged FFT
                amplitude of the day (sqrt of Σ amp_k² over the bins
                rounding to d); σ_band = sqrt(Σ_{d′≤N/4} amp(d′)² / 2).
                0 where not auditable (d > N/3 — under 3 cycles).

The factors are stored per FFT BIN (bin-aligned with amplitude_spectrum):
element i (bin k = i+1) carries the factors of the integer day period
round(N/k) — every bin of a day shares the same value. Day N (k=1) and
days > N//2 get 0 (no recurrence claim is possible).

JS-parity details (must match patternScore.ts / spectrumOption.ts):
  • day rounding uses floor(N/k + 0.5) — JS Math.round (half UP), NOT
    numpy's banker's rounding.
  • centered-MA detrend window L = max(3, floor(N/4) made odd).
  • biased ACF (denominator = full-window Σx²), τ = 1.96/√N.
  • extrema: strict local maxima/minima of the RAW closes passing a
    topographic prominence ≥ 1.5 × σ(daily changes, ddof=0), forced to
    alternate; pool = consecutive-gap sums + doubled single gaps.

GPU note: the ACF is computed via FFT (Wiener–Khinchin) so the whole
batch is vectorizable and reuses the shared cudf→cupy→cpu FFT routing
in _fft.py (cuDF implements no FFT). Extrema extraction uses
scipy.signal (C-implemented) per window — the only per-row loop.
"""
from __future__ import annotations

import numpy as np
from scipy.signal import peak_prominences

from analyze.fourier_freqs._fft import _irfft, _rfft

# Audit constants — mirror patternScore.ts exactly.
TOL = 0.15    # evidence-vs-day tolerance (±15% of the day)
K_PROM = 1.5  # extrema prominence threshold in units of σ(daily change)
Z95 = 1.96    # 95% white-noise ACF confidence multiplier

_EMPTY_F64 = np.empty(0, dtype=np.float64)

# Window-chunk size for the batched ACF/audit — bounds working memory
# (2N-padded complex FFT block ≈ chunk × 2N × 16 B) per the project's
# GPU batch-sending rule.
_CHUNK = 2048


def _ma_residual(w: np.ndarray) -> np.ndarray:
    """Centered-MA residual of each row — removes periods ≥ ~N/4.

    Mirrors patternScore.ts maResidual: L = max(3, floor(N/4) | 1) —
    the bitwise OR makes the window odd (JS semantics: even → +1).
    """
    Wc, N = w.shape
    L = N // 4
    if L % 2 == 0:
        L += 1
    L = max(3, L)
    half = L // 2
    cs = np.zeros((Wc, N + 1), dtype=np.float64)
    np.cumsum(w, axis=1, out=cs[:, 1:])
    i = np.arange(N)
    lo = np.maximum(0, i - half)
    hi = np.minimum(N, i + half + 1)
    ma = (cs[:, hi] - cs[:, lo]) / (hi - lo)[None, :]
    return w - ma


def _acf_fft(x: np.ndarray) -> np.ndarray:
    """Biased ACF (lags 0..N−1) of each mean-subtracted row via FFT.

    Linear (non-circular) autocorrelation: zero-pad to 2N, rfft → |F|²
    → irfft. Denominator is the full-window Σx² of the row (biased
    estimator, same as patternScore.ts). Rows with zero energy return
    all-zero ACF.
    """
    Wc, N = x.shape
    denom = np.einsum("ij,ij->i", x, x)
    xpad = np.zeros((Wc, 2 * N), dtype=np.float64)
    xpad[:, :N] = x
    F = _rfft(xpad, axis=1)
    ac = _irfft(F * np.conj(F), n=2 * N, axis=1)[:, :N]
    out = np.zeros((Wc, N), dtype=np.float64)
    nz = denom > 0
    out[nz] = ac[nz] / denom[nz, None]
    return out


def _acf_frac(acf: np.ndarray, N: int, days: np.ndarray) -> np.ndarray:
    """Fraction of significant multiples per day — vectorized over rows.

    For each day d, maxRepeats = floor((N−d)/d) (≥ 1 for d ≤ N//2) and
    frac(d) = #{m ≤ maxRepeats : acf[m·d] ≥ 1.96/√N} / maxRepeats. The
    (day, m) lag indices are flattened once and grouped with
    np.add.reduceat — no Python loop over days.
    """
    tau = Z95 / np.sqrt(N)
    mmax = (N - days) // days  # ≥ 1 for all days ≤ N//2
    starts = np.concatenate([[0], np.cumsum(mmax)[:-1]]).astype(np.int64)
    total = int(mmax.sum())
    day_idx = np.repeat(np.arange(len(days)), mmax)
    m_arr = np.arange(total, dtype=np.int64) - np.repeat(starts, mmax) + 1
    lags = m_arr * days[day_idx]
    # int32, NOT bool — np.add on bools acts as logical OR, not a sum.
    hits = (acf[:, lags] >= tau).astype(np.int32)  # (Wc, total)
    return np.add.reduceat(hits, starts, axis=1) / mmax[None, :]


def _extrema_pools(w: np.ndarray) -> list[np.ndarray]:
    """Per-row extrema evidence pools of the RAW closes.

    Mirrors patternScore.ts extremaEvidencePool: STRICT local maxima /
    minima (candidate detection vectorized over the whole window matrix)
    passing a topographic prominence >= K_PROM x sigma(daily changes,
    ddof=0) — scipy.signal.peak_prominences computes the SAME signed
    prominence the TS code implements by hand (validated: identical
    extrema sets vs the JS port, whereas find_peaks' internal candidate
    selection differs on tie/plateau edge cases). Kept extrema are
    forced to alternate, then the pool of full-cycle period estimates =
    consecutive-gap sums (hi->lo->hi) plus doubled single gaps
    (half-cycle corroboration).
    """
    Wc, N = w.shape
    sds = np.std(w[:, 1:] - w[:, :-1], axis=1, ddof=0)
    mid = w[:, 1:-1]
    mask_hi = (mid > w[:, :-2]) & (mid > w[:, 2:])  # strict local maxima
    mask_lo = (mid < w[:, :-2]) & (mid < w[:, 2:])  # strict local minima
    pools: list[np.ndarray] = []
    for r in range(Wc):
        row = w[r]
        thr = K_PROM * sds[r]
        hi_idx = np.flatnonzero(mask_hi[r]) + 1
        lo_idx = np.flatnonzero(mask_lo[r]) + 1
        prom_hi = peak_prominences(row, hi_idx)[0] if hi_idx.size else _EMPTY_F64
        prom_lo = peak_prominences(-row, lo_idx)[0] if lo_idx.size else _EMPTY_F64
        idx = np.concatenate([hi_idx[prom_hi >= thr], lo_idx[prom_lo >= thr]])
        typ = np.concatenate(
            [np.ones(int((prom_hi >= thr).sum()), dtype=np.int8),
             -np.ones(int((prom_lo >= thr).sum()), dtype=np.int8)]
        )
        order = np.argsort(idx, kind="stable")
        idx, typ = idx[order], typ[order]
        if len(idx) > 1:
            keep = np.concatenate([[True], np.diff(typ) != 0])
            idx = idx[keep]
        if len(idx) < 3:
            pools.append(_EMPTY_F64)
            continue
        gaps = np.diff(idx).astype(np.float64)
        pools.append(np.concatenate([gaps[:-1] + gaps[1:], 2.0 * gaps]))
    return pools


def _rec_ext(pools: list[np.ndarray], N: int, days: np.ndarray) -> np.ndarray:
    """Extrema-evidence fraction per day — pool hits / max cycles, cap 1."""
    maxrep = np.maximum(1, (N - days) // days).astype(np.float64)
    tol = TOL * days
    out = np.zeros((len(pools), len(days)), dtype=np.float64)
    for r, pool in enumerate(pools):
        if pool.size == 0:
            continue
        hits = (np.abs(pool[:, None] - days[None, :]) <= tol[None, :]).sum(axis=0)
        out[r] = np.minimum(hits / maxrep, 1.0)
    return out


def compute_pattern_scores(
    windows: np.ndarray,
    amplitudes: np.ndarray,
    range_days: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute the count & strength factor blocks for one (code, range_days).

    Args:
        windows: (n_windows, range_days) RAW close-price windows (the
            same sliding windows the FFT used, before detrending).
        amplitudes: (n_windows, range_days // 2) one-sided amplitude
            spectrum per window (excl. DC) — the FFT block from compute.
        range_days: the window size N.

    Returns:
        (count_block, strength_block), each (n_windows, N//2) float64
        bin-aligned with amplitudes: element i (bin k=i+1) carries the
        factors of the integer day period round(N/k). Zeros when the
        window is too short to audit (N < 8) or the day is outside the
        auditable recurrence range.
    """
    n_windows, n_bins = amplitudes.shape
    N = range_days
    count_block = np.zeros((n_windows, n_bins), dtype=np.float64)
    strength_block = np.zeros((n_windows, n_bins), dtype=np.float64)
    if n_windows == 0 or N < 8:
        return count_block, strength_block

    # JS Math.round(N/k) parity — half UP, not banker's rounding.
    ks = np.arange(1, n_bins + 1, dtype=np.float64)
    day_of_bin = np.floor(N / ks + 0.5).astype(np.int64)
    days = np.arange(2, N // 2 + 1, dtype=np.int64)  # auditable day axis
    n_days = len(days)

    # One-hot day-merge matrix (n_bins × n_days): column j accumulates
    # Σ amp_k² over the bins rounding to day j — merged amp² per day via
    # ONE matmul (the TS dayMap merge). Bins whose day falls outside the
    # axis (day N from k=1, days > N//2) contribute nothing.
    day_pos = np.where(
        (day_of_bin >= 2) & (day_of_bin <= N // 2), day_of_bin - 2, -1
    )
    merge = np.zeros((n_bins, n_days), dtype=np.float64)
    valid = day_pos >= 0
    merge[valid, day_pos[valid]] = 1.0

    # σ_band per row: sqrt(Σ amp² over bins with day ≤ N//4 / 2).
    band_mask = ((day_of_bin <= N // 4) & valid).astype(np.float64)
    auditable_day = (days <= N // 3).astype(np.float64)

    for lo in range(0, n_windows, _CHUNK):
        hi = min(lo + _CHUNK, n_windows)
        wc = windows[lo:hi]
        amp = amplitudes[lo:hi]

        # ---- time-domain count factors --------------------------------
        resid = _ma_residual(wc)
        resid = resid - resid.mean(axis=1, keepdims=True)
        acf = _acf_fft(resid)
        afrac = _acf_frac(acf, N, days)
        rext = _rec_ext(_extrema_pools(wc), N, days)
        count_day = rext * afrac  # (Wc, n_days)

        # ---- amp factors from the FFT block ---------------------------
        amp_sq = amp * amp
        merged_sq = amp_sq @ merge            # (Wc, n_days) merged amp²
        sig_band = np.sqrt((amp_sq @ band_mask[:, None])[:, 0] / 2.0)
        safe_sig = np.where(sig_band > 0, sig_band, 1.0)
        amp_norm = np.sqrt(merged_sq) / safe_sig[:, None]
        amp_norm[sig_band <= 0, :] = 0.0

        strength_day = amp_norm * count_day * auditable_day[None, :]

        # ---- broadcast day values back onto the bin axis ---------------
        gather = np.where(valid, day_pos, 0)
        count_block[lo:hi] = np.where(
            valid[None, :], count_day[:, gather], 0.0
        )
        strength_block[lo:hi] = np.where(
            valid[None, :], strength_day[:, gather], 0.0
        )

    return count_block, strength_block
