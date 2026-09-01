"""Per-day recurring rise/drop periodicity factors — the SEMANTIC CORE.

For each sliding window of N close prices and each integer day period d
(2..N//2; array index j = d − 2) the audit produces THREE day-aligned
factors:

  amp(d)      — energy-merged FFT amplitude of the day: sqrt(Σ amp_k²)
                over the bins k whose period N/k rounds to d (half-UP
                rounding). The Fourier REFERENCE — how large the swings
                at that period are (yuan). NOT recurrence evidence by
                itself.

  count(d)    = recEXT(d) × acfFrac(d)               — the COUNT factor
                recEXT  — prominence-filtered alternating-extrema
                          evidence: pool hits within ±15% of d, over the
                          max possible cycles floor((N−d)/d), capped 1
                acfFrac — MA-detrended ACF recurrence: fraction of the
                          multiples m·d with biased acf ≥ 1.96/√N
                This is the RECURRENCE evidence: did price actually
                rise-and-drop repeatedly with spacing ≈ d. A one-off
                swing, a trend, or noise all score ~0.

  strength(d) = (amp(d)/σ_band) × count(d)           — the summarized
                recurring strength. amp(d) normalized by the swing-band
                σ (sqrt(Σ_{d′≤N/4} amp(d′)² / 2)) then gated by the
                recurrence count. 0 where not auditable (d > N/3 — under
                3 cycles in the window).

The headline period_days = argmax of strength — the period at which
price BOTH cycled repeatedly AND with meaningful swing amplitude.

GPU note: the ACF is computed via FFT (Wiener–Khinchin) so the whole
batch is vectorizable and reuses the shared cudf→cupy→cpu FFT routing
in _fft.py (cuDF implements no FFT). Extrema extraction uses
scipy.signal (C-implemented) per window — the only per-row loop.

PROXY NOTE: everything in this module is pure host numpy/scipy math on
ndarrays — NO cudf objects are touched. The inputs MUST be RAW host
numpy arrays (compute.py unwraps cudf.pandas proxy arrays at the
pandas→numpy boundary via _host_array): proxy-subclass arrays would
route every numpy op here through the cudf fast/slow dispatcher
(profiled at 128 of 136 s per long-history code — each small op sent
to cupy + a `.get()` device sync).
"""
from __future__ import annotations

import numpy as np
from scipy.signal import peak_prominences

from analyze.recurring_cycles._fft import _irfft, _rfft

# Audit constants.
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

    L = max(3, floor(N/4) made odd).
    """
    Wc, N = w.shape
    L = N // 4
    if L % 2 == 0:
        L += 1
    L = max(3, L)
    half = L // 2
    cs = np.zeros((Wc, N + 1), dtype=np.float64)
    cs[:, 1:] = np.cumsum(w, axis=1)
    i = np.arange(N)
    lo = np.maximum(0, i - half)
    hi = np.minimum(N, i + half + 1)
    ma = (cs[:, hi] - cs[:, lo]) / (hi - lo)[None, :]
    return w - ma


def _acf_fft(x: np.ndarray) -> np.ndarray:
    """Biased ACF (lags 0..N−1) of each mean-subtracted row via FFT.

    Linear (non-circular) autocorrelation: zero-pad to 2N, rfft → |F|²
    → irfft. Denominator is the full-window Σx² of the row (biased
    estimator). Rows with zero energy return all-zero ACF.
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

    STRICT local maxima / minima (candidate detection vectorized over
    the whole window matrix) passing a topographic prominence >=
    K_PROM × sigma(daily changes, ddof=0) — scipy.signal.peak_prominences
    computes the same signed prominence the former TS port implemented by
    hand. Kept extrema are forced to alternate, then the pool of
    full-cycle period estimates = consecutive-gap sums (hi->lo->hi) plus
    doubled single gaps (half-cycle corroboration).
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
    """Extrema-evidence fraction per day — pool hits / max cycles, cap 1.

    Vectorized interval formulation: pool value p hits day d iff
    |p − d| ≤ TOL·d  ⇔  d ∈ [p/1.15, p/0.85] — a CONTIGUOUS integer-day
    range. Every pool element contributes +1 across its range; per-row
    day hits are the cumsum of those range end-points, scattered for ALL
    rows at once via a single weighted bincount over flattened
    (row, day) indices — no per-row Python loop and no (pool × days)
    broadcast. (12× faster than the broadcast form; verified equivalent
    up to knife-edge float boundaries, where the interval form is the
    mathematically exact one.)
    """
    n_rows = len(pools)
    n_days = len(days)
    maxrep = np.maximum(1, (N - days) // days).astype(np.float64)
    d_min, d_max = int(days[0]), int(days[-1])

    lens = np.fromiter((p.size for p in pools), dtype=np.int64, count=n_rows)
    if int(lens.sum()) == 0:
        return np.zeros((n_rows, n_days), dtype=np.float64)

    pool_all = np.concatenate(pools)
    rows = np.repeat(np.arange(n_rows, dtype=np.int64), lens)

    d_lo = np.ceil(pool_all / (1.0 + TOL)).astype(np.int64)
    d_hi = np.floor(pool_all / (1.0 - TOL)).astype(np.int64)
    keep = (d_hi >= d_min) & (d_lo <= d_max)
    if not keep.any():
        return np.zeros((n_rows, n_days), dtype=np.float64)
    d_lo, d_hi, rows = d_lo[keep], d_hi[keep], rows[keep]
    np.clip(d_lo, d_min, d_max, out=d_lo)
    np.clip(d_hi, d_min, d_max, out=d_hi)

    # Scatter +1 at each range start and −1 after each range end on the
    # (row, day+1) grid; cumsum along the day axis reconstructs hits.
    width = n_days + 1
    flat = rows * width
    n_keep = len(rows)
    delta = np.bincount(
        np.concatenate([flat + (d_lo - d_min), flat + (d_hi - d_min + 1)]),
        weights=np.concatenate([np.ones(n_keep), -np.ones(n_keep)]),
        minlength=n_rows * width,
    ).astype(np.float64).reshape(n_rows, width)
    hits = np.cumsum(delta, axis=1)[:, :n_days]
    return np.minimum(hits / maxrep[None, :], 1.0)


def compute_pattern_scores(
    windows: np.ndarray,
    amplitudes: np.ndarray,
    range_days: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute the per-day amp / count / strength blocks for one (code, N).

    Args:
        windows: (n_windows, range_days) RAW close-price windows (the
            same sliding windows the FFT used, before detrending).
        amplitudes: (n_windows, range_days // 2) one-sided bin amplitude
            spectrum per window (excl. DC) — the FFT block from compute.
        range_days: the window size N.

    Returns:
        (amp_block, count_block, strength_block), each (n_windows,
        N//2 − 1) float64 DAY-aligned: element j is integer day period
        d = j + 2. amp_block = energy-merged FFT amplitude per day
        (yuan); count_block = recEXT × acfFrac (0 for d > N//2, and the
        day axis already ends at N//2); strength_block =
        (amp/σ_band) × count, 0 for d > N//3 (not auditable — under 3
        cycles). All zeros when N < 8 (window too short to audit).
    """
    n_windows, n_bins = amplitudes.shape
    N = range_days
    n_days = max(0, N // 2 - 1)
    amp_block = np.zeros((n_windows, n_days), dtype=np.float64)
    count_block = np.zeros((n_windows, n_days), dtype=np.float64)
    strength_block = np.zeros((n_windows, n_days), dtype=np.float64)
    if n_windows == 0 or n_days == 0 or N < 8:
        return amp_block, count_block, strength_block

    # Raw host numpy math only (see module docstring PROXY NOTE) — the
    # blocks are filled in place by the split-out impl below.
    _fill_pattern_scores(
        windows, amplitudes, N, amp_block, count_block, strength_block,
    )
    return amp_block, count_block, strength_block


def _fill_pattern_scores(
    windows: np.ndarray,
    amplitudes: np.ndarray,
    N: int,
    amp_block: np.ndarray,
    count_block: np.ndarray,
    strength_block: np.ndarray,
) -> None:
    """Fill the three day-aligned blocks in place for one (code, N)."""
    n_windows, n_bins = amplitudes.shape
    n_days = amp_block.shape[1]

    # Day period of each bin: floor(N/k + 0.5) — half-UP rounding.
    ks = np.arange(1, n_bins + 1, dtype=np.float64)
    day_of_bin = np.floor(N / ks + 0.5).astype(np.int64)
    days = np.arange(2, N // 2 + 1, dtype=np.int64)  # day axis

    # One-hot day-merge matrix (n_bins × n_days): column j accumulates
    # Σ amp_k² over the bins rounding to day j — merged amp² per day via
    # ONE matmul. Bins whose day falls outside 2..N//2 (day N from k=1)
    # contribute nothing.
    day_pos = np.where(
        (day_of_bin >= 2) & (day_of_bin <= N // 2), day_of_bin - 2, -1
    )
    merge = np.zeros((n_bins, n_days), dtype=np.float64)
    valid = day_pos >= 0
    merge[valid, day_pos[valid]] = 1.0

    # σ_band per row: sqrt(Σ amp² over days ≤ N//4 / 2).
    band_mask = (days <= N // 4).astype(np.float64)
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
        amp_day = np.sqrt(merged_sq)          # energy-merged amplitude
        sig_band = np.sqrt((merged_sq @ band_mask[:, None])[:, 0] / 2.0)
        safe_sig = np.where(sig_band > 0, sig_band, 1.0)
        amp_norm = amp_day / safe_sig[:, None]
        amp_norm[sig_band <= 0, :] = 0.0

        strength_day = amp_norm * count_day * auditable_day[None, :]

        amp_block[lo:hi] = amp_day
        count_block[lo:hi] = count_day
        strength_block[lo:hi] = strength_day
