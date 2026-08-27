"""Pure numpy/pandas transformation logic for analyze.fourier_freqs.

Computes the dominant Fourier frequency of close prices per
(sec_type, code, last_date, range_days) using a sliding-window real FFT.

Key algorithm:
  For each code and each range_days window size:
    1. Create sliding windows of size range_days over the close-price
       series (numpy stride_tricks — zero-copy view).
    2. Detrend each window (subtract its mean — removes the DC component
       so it doesn't dominate the amplitude spectrum).
    3. Compute rfft (real FFT) for ALL windows at once (vectorized along
       axis=1 — one numpy call per (code, range_days) pair).
    4. One-sided amplitude spectrum: |X[k]| × 2 / range_days for
       k = 1..N//2 (excludes DC at k=0).
    5. Dominant frequency = argmax of the amplitude spectrum.
    6. Convert FFT bin k* to period in days: freq = round(N / k*).
       - Minimum freq = 2 (Nyquist — shortest detectable cycle).
       - Maximum freq = N (one full cycle over the window — longest
         detectable cycle).
    7. amplitude_close_price = amplitude at k*.
  8. amplitude_spectrum = the FULL one-sided amplitude array (all bins
     k=1..N//2), stored as a Postgres double-precision array. Drives the
     per-date full-FFT-spectrum bar charts on the Fourier Frequencies
     page (one chart per range_days window, reactive to a clicked date
     on the top index price plot).

  Edge case: constant window (all close prices equal) → detrended signal
  is all zeros → all amplitudes are 0 → argmax returns 0 → freq=N,
  amplitude=0. This is semantically correct: "no periodic signal, period
  is the entire window with zero amplitude."

GPU note: cuDF does NOT implement FFT (any release, incl. 26.08), so the
spectral transform cannot go through the cudf.pandas proxy. Instead it is
routed EXPLICITLY to CuPy (cuFFT, GPU) when a CUDA device is present, else
numpy (CPU) — the same cudf->cupy->cpu cascade used by
_common.df_utils.rolling_corr for ops cuDF lacks. See _fft.py (shared
with pattern_score.py, which also uses an FFT-based ACF). The
sliding-window detrend/stride-stuff stays on the array module
cudf.pandas chooses (GPU via the proxy when active, else CPU); only the
rfft itself is routed by hand. When neither CuPy nor the cudf.pandas hook
is installed the whole pipeline is plain numpy.

Memory note: the spectrum columns are held as 1-D numpy array views into
each (code, range_days) 2-D block until the DataFrame is written — three
blocks per (code, range_days) now (amplitudes, count, strength), roughly
tripling the array-view peak vs the amplitude-only era. For index-only
this was ~4-5 GB with one block; plan accordingly and run per sec_type
(--sec-type) with --force when memory-bound. The 1275d window has ~637
bins per row, increasing memory proportionally.
"""
from __future__ import annotations

import logging
import numpy as np
import pandas as pd

from _common.df_utils import should_use_gpu  # noqa: F401 — per project convention

from analyze.fourier_freqs._fft import _rfft
from analyze.fourier_freqs.config import RANGE_DAYS
from analyze.fourier_freqs.pattern_score import compute_pattern_scores

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FFT backend routing lives in _fft.py (shared with pattern_score.py —
# cuDF implements no FFT, so rfft/irfft are routed cudf->cupy->cpu there).
# ---------------------------------------------------------------------------


# Column order for the output DataFrame (matches the DB table schema).
OUTPUT_COLUMNS = [
    "sec_type", "code", "freq", "amplitude_close_price",
    "amplitude_spectrum", "count_spectrum", "strength_spectrum",
    "last_date", "range_days",
]

# Numeric SCALAR columns for sanitize_for_db_insert.
# The array columns (amplitude/count/strength_spectrum) are NOT included
# here (sanitize only handles scalar numeric columns); they are converted
# to Python lists in __main__._write_rows for asyncpg.
NUMERIC_COLS = ["amplitude_close_price"]


def compute_fourier_freqs(
    close_df: pd.DataFrame,
    sec_type: str,
    range_days_list: tuple[int, ...] = RANGE_DAYS,
    *,
    target_dates: dict[str, set] | None = None,
) -> pd.DataFrame:
    """Compute dominant Fourier frequency per (code, last_date, range_days).

    For each code, creates sliding windows of each range_days size over
    the close-price series, detrends, computes rfft, and extracts the
    dominant frequency (highest-amplitude FFT bin, excluding DC).

    Args:
        close_df: DataFrame with columns code, date, close. Sorted by
            (code, date) is NOT required — this function sorts internally.
        sec_type: 'index', 'etf', or 'stock'.
        range_days_list: tuple of window sizes in trading days. Defaults
            to RANGE_DAYS from config (20, 60, 255, 500, 750).
        target_dates: optional per-code date sets to compute (code ->
            set of dates). When None, all dates with sufficient history
            are computed. When supplied (incremental mode), only windows
            of a code whose last_date is in that code's target set are
            included in the output. The FFT still uses the FULL per-code
            history for the window (target_dates only filters which
            windows to OUTPUT, not the input data).

    Returns:
        DataFrame with columns: sec_type, code, freq (int),
        amplitude_close_price (float), amplitude_spectrum (list[float] /
        1-D numpy array — the full one-sided amplitude spectrum, length
        floor(range_days/2), excluding DC), count_spectrum and
        strength_spectrum (same shape/bin alignment — the periodic-pattern
        audit factors per integer day freq: count = extrema evidence ×
        ACF coherence, strength = (amp/σ_band) × count; see
        pattern_score.py), last_date (date), range_days (int). Empty
        DataFrame with the correct columns if close_df is empty.
    """
    if close_df.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    # Per project convention: check GPU availability. The FFT itself uses
    # numpy (cuDF doesn't support FFT), but the check is included for
    # consistency with other analyze modules.
    _ = should_use_gpu(close_df, op_type="groupby_agg")

    # Accumulate numpy arrays per (code, range_days) chunk, then
    # concatenate once at the end. This avoids the overhead of building
    # a list of 28M+ Python dicts.
    sec_types_arr: list[np.ndarray] = []
    codes_arr: list[np.ndarray] = []
    freqs_arr: list[np.ndarray] = []
    amps_arr: list[np.ndarray] = []
    dates_arr: list[np.ndarray] = []
    range_days_arr: list[np.ndarray] = []

    # Spectrum accumulators: one 1-D numpy array per window (a row of the
    # 2-D block for that (code, range_days)). Built via `list(block)` (C-level
    # row split) so we don't pay a per-window Python loop. The views
    # reference the parent 2-D blocks, keeping them live until the
    # DataFrame is written (see the memory note above). Cannot
    # np.concatenate (ragged: N//2 differs per range_days) — kept as flat
    # Python lists and assigned to the DataFrame columns directly.
    spectrums_flat: list[np.ndarray] = []
    counts_flat: list[np.ndarray] = []
    strengths_flat: list[np.ndarray] = []

    for code, group in close_df.groupby("code", sort=True):
        group = group.sort_values("date").reset_index(drop=True)
        close = group["close"].values.astype(np.float64)
        dates = group["date"].values

        # Incremental mode: this code's target date set. Normalize to
        # datetime.date once per code — guards against datetime64 vs
        # datetime.date mismatches across pandas/cudf DataFrame
        # constructions (a datetime64 value is never `in` a set of
        # datetime.date, which would silently drop every window).
        code_targets: set | None = None
        if target_dates is not None:
            code_targets = target_dates.get(code)
            if not code_targets:
                # No missing targets for this code — skip entirely.
                continue
            if dates.dtype != object:
                dates = pd.to_datetime(dates).date

        for range_days in range_days_list:
            n = len(close)
            if n < range_days:
                # Not enough history for this window size — skip.
                continue

            # Sliding windows: shape (n_windows, range_days).
            # sliding_window_view returns a read-only view (zero copy).
            windows = np.lib.stride_tricks.sliding_window_view(
                close, range_days
            )
            window_dates = dates[range_days - 1:]

            # Incremental mode: filter to the code's target dates BEFORE
            # the FFT to save computation. Only windows whose last_date
            # is a target are processed.
            if code_targets is not None:
                mask = np.array(
                    [d in code_targets for d in window_dates],
                    dtype=bool,
                )
                if not mask.any():
                    continue
                windows = windows[mask]
                window_dates = window_dates[mask]

            n_windows = len(window_dates)
            if n_windows == 0:
                continue

            # Detrend: subtract mean from each window (removes DC).
            means = windows.mean(axis=1, keepdims=True)
            windows_detrended = windows - means

            # Real FFT — vectorized over all windows (axis=1).
            # Output shape: (n_windows, range_days // 2 + 1).
            # cuDF has no FFT -> routed explicitly to CuPy (GPU) when
            # available, else numpy (CPU). See _rfft.
            fft_result = _rfft(windows_detrended, axis=1)
            n_freq = fft_result.shape[1]

            if n_freq <= 1:
                # Only DC component (range_days <= 1) — can't extract
                # a meaningful frequency. Skip.
                continue

            # One-sided amplitude spectrum (exclude DC at index 0).
            # |X[k]| × 2 / N for k = 1..N//2. Shape: (n_windows, N//2).
            amplitudes = (
                np.abs(fft_result[:, 1:]) * 2.0 / range_days
            )

            # Sanitize the FULL spectrum: NaN/inf (from NaN in close
            # prices) → 0. Done once here so both the dominant extraction
            # and the stored spectrum are clean. Constant-window bins are
            # already 0 (detrended to zeros), so this only touches NaN.
            if not np.all(np.isfinite(amplitudes)):
                amplitudes = np.nan_to_num(
                    amplitudes, nan=0.0, posinf=0.0, neginf=0.0,
                )

            # Dominant frequency bin (1-based, because we excluded k=0).
            dominant_k = np.argmax(amplitudes, axis=1) + 1

            # Convert FFT bin to period in days: period = N / k.
            freqs = np.round(
                range_days / dominant_k
            ).astype(np.int32)

            # Amplitude at the dominant frequency.
            dom_amps = amplitudes[
                np.arange(n_windows), dominant_k - 1
            ].astype(np.float64)

            # (NaN handling for dom_amps/freqs is now redundant — the
            # nan_to_num above already sanitized the whole spectrum — but
            # kept as a defensive guard in case amplitudes was somehow
            # re-introduced to NaN between the two reads.)
            nan_mask = ~np.isfinite(dom_amps)
            if nan_mask.any():
                dom_amps = dom_amps.copy()
                freqs = freqs.copy()
                dom_amps[nan_mask] = 0.0
                freqs[nan_mask] = range_days

            # Pattern-score separation (count vs amp) on the RAW windows —
            # bin-aligned with the amplitude block. See pattern_score.py.
            count_block, strength_block = compute_pattern_scores(
                windows, amplitudes, range_days
            )

            # Accumulate as typed arrays for efficient concatenation.
            sec_types_arr.append(
                np.full(n_windows, sec_type, dtype=object)
            )
            codes_arr.append(
                np.full(n_windows, code, dtype=object)
            )
            freqs_arr.append(freqs)
            amps_arr.append(dom_amps)
            dates_arr.append(window_dates)
            range_days_arr.append(
                np.full(n_windows, range_days, dtype=np.int32)
            )
            # Append each window's full spectrum rows (1-D views into the
            # 2-D blocks). Order matches the flat arrays above (per
            # (code, range_days), per window within the block).
            spectrums_flat.extend(list(amplitudes))
            counts_flat.extend(list(count_block))
            strengths_flat.extend(list(strength_block))

    if not sec_types_arr:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    return pd.DataFrame({
        "sec_type": np.concatenate(sec_types_arr),
        "code": np.concatenate(codes_arr),
        "freq": np.concatenate(freqs_arr),
        "amplitude_close_price": np.concatenate(amps_arr),
        "amplitude_spectrum": spectrums_flat,
        "count_spectrum": counts_flat,
        "strength_spectrum": strengths_flat,
        "last_date": np.concatenate(dates_arr),
        "range_days": np.concatenate(range_days_arr),
    })
