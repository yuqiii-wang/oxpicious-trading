"""Pure numpy/pandas transformation logic for analyze.recurring_cycles.

Computes the recurring rise/drop periodicity of close prices per
(sec_type, code, last_date, range_days) using sliding windows.

Key algorithm — per code and window size N:
  1. Create sliding windows of size N over the close-price series
     (numpy stride_tricks — zero-copy view).
  2. Detrend each window (subtract its mean — removes DC) and compute
     the one-sided bin amplitude spectrum |X[k]| × 2 / N (k = 1..N//2)
     via rfft — the Fourier amplitude REFERENCE.
  3. compute_pattern_scores merges the bin amplitudes into integer day
     periods and audits RECURRENCE per day d in the TIME domain:
     count(d) = alternating-extrema evidence × ACF coherence;
     strength(d) = (amp(d)/σ_band) × count(d).
  4. Headline: period_days = argmax of the strength spectrum (+2 day
     offset); strength / count_factor / amplitude are that day's
     factor values. 0 = NO recurring rise/drop period detected (all
     strengths 0 — flat window, pure trend, or one-off swings).
  5. The three per-day spectra (amplitude/count/strength, index j =
     day j+2, length N//2 − 1) are stored as Postgres double-precision
     arrays and drive the per-date recurring-cycle bar charts on the
     Recurring Cycles page (one chart per range_days window, reactive
     to a clicked date on the top index price plot).

GPU note: cuDF does NOT implement FFT, so the spectral transform is
routed EXPLICITLY to CuPy (cuFFT, GPU) when available, else numpy (CPU)
— see _fft.py (shared with pattern_score.py, which also uses an
FFT-based ACF). The sliding-window detrend/stride-stuff stays on the
array module cudf.pandas chooses (GPU via the proxy when active, else
CPU); only the rfft/irfft are routed by hand.

Memory note: the spectrum columns are held as 1-D numpy array views into
each (code, range_days) 2-D block until the DataFrame is written —
three blocks per (code, range_days) (amplitudes, count, strength). For
index-only this was ~4-5 GB with the old bin-aligned blocks; the
day-aligned blocks are the same size (N//2 − 1 vs N//2 columns). Run
per sec_type (--sec-type) with --force when memory-bound.
"""
from __future__ import annotations

import logging
import numpy as np
import pandas as pd

from _common.df_utils import should_use_gpu  # noqa: F401 — per project convention

from analyze.recurring_cycles._fft import _rfft
from analyze.recurring_cycles.config import RANGE_DAYS
from analyze.recurring_cycles.pattern_score import compute_pattern_scores

logger = logging.getLogger(__name__)


def _host_array(x: np.ndarray) -> np.ndarray:
    """Unwrap a cudf.pandas proxy ndarray to a RAW host numpy array.

    Arrays derived from proxy pandas objects (`series.values` etc.) are
    proxy-subclass ndarrays whose EVERY downstream numpy op dispatches
    through __array_function__ into the cudf fast/slow machinery —
    profiling showed 128 of 136 s per long-history code burned there
    (each small op routed to cupy + a `.get()` device sync). Raw
    ndarray inputs keep the whole compute path in plain host numpy.
    """
    return x._fsproxy_slow if hasattr(x, "_fsproxy_slow") else x

# Column order for the output DataFrame (matches the DB table schema).
OUTPUT_COLUMNS = [
    "sec_type", "code", "period_days", "strength", "count_factor",
    "amplitude", "amplitude_spectrum", "count_spectrum",
    "strength_spectrum", "last_date", "range_days",
]

# Numeric SCALAR columns for sanitize_for_db_insert.
# The array columns (amplitude/count/strength_spectrum) are NOT included
# here (sanitize only handles scalar numeric columns); they are converted
# to Python lists in __main__._write_rows for asyncpg.
NUMERIC_COLS = ["strength", "count_factor", "amplitude"]


def compute_recurring_cycles(
    close_df: pd.DataFrame,
    sec_type: str,
    range_days_list: tuple[int, ...] = RANGE_DAYS,
    *,
    target_dates: dict[str, set] | None = None,
) -> pd.DataFrame:
    """Compute recurring rise/drop periodicity per (code, last_date, N).

    For each code, creates sliding windows of each range_days size over
    the close-price series, detrends, computes the bin amplitude
    spectrum via rfft, merges bins into integer day periods, and audits
    RECURRENCE per day (extrema evidence × ACF coherence). The headline
    period is the top-strength day — where price both cycled repeatedly
    AND with meaningful swing amplitude.

    Args:
        close_df: DataFrame with columns code, date, close. Sorted by
            (code, date) is NOT required — this function sorts internally.
        sec_type: 'index', 'etf', or 'stock'.
        range_days_list: tuple of window sizes in trading days. Defaults
            to RANGE_DAYS from config (20, 60, 255, 500, 750, 1275).
        target_dates: optional per-code date sets to compute (code ->
            set of dates). When None, all dates with sufficient history
            are computed. When supplied (incremental mode), only windows
            of a code whose last_date is in that code's target set are
            included in the output. The computation still uses the FULL
            per-code history for the window (target_dates only filters
            which windows to OUTPUT, not the input data).

    Returns:
        DataFrame with columns: sec_type, code, period_days (int; 0 =
        no recurring period), strength / count_factor / amplitude
        (float — the top-strength day's factors, 0 when period_days is
        0), amplitude_spectrum / count_spectrum / strength_spectrum
        (per-day arrays, index j = day j+2, length floor(N/2) − 1),
        last_date (date), range_days (int). Empty DataFrame with the
        correct columns if close_df is empty.
    """
    if close_df.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    # Per project convention: check GPU availability. The FFT itself is
    # routed in _fft.py, but the check is included for consistency with
    # other analyze modules.
    _ = should_use_gpu(close_df, op_type="groupby_agg")

    # Accumulate numpy arrays per (code, range_days) chunk, then
    # concatenate once at the end. This avoids the overhead of building
    # a list of 28M+ Python dicts.
    sec_types_arr: list[np.ndarray] = []
    codes_arr: list[np.ndarray] = []
    periods_arr: list[np.ndarray] = []
    strengths_arr: list[np.ndarray] = []
    counts_arr: list[np.ndarray] = []
    amps_arr: list[np.ndarray] = []
    dates_arr: list[np.ndarray] = []
    range_days_arr: list[np.ndarray] = []

    # Spectrum accumulators: one 1-D numpy array per window (a row of the
    # 2-D block for that (code, range_days)). Built via `list(block)`
    # (C-level row split) so we don't pay a per-window Python loop. The
    # views reference the parent 2-D blocks, keeping them live until the
    # DataFrame is written. Cannot np.concatenate (ragged: N//2 − 1
    # differs per range_days) — kept as flat Python lists and assigned
    # to the DataFrame columns directly.
    spectrums_flat: list[np.ndarray] = []
    counts_flat: list[np.ndarray] = []
    strengths_flat: list[np.ndarray] = []

    codes_seen = 0
    for code, group in close_df.groupby("code", sort=True):
        codes_seen += 1
        if codes_seen % 1000 == 0:
            # Progress heartbeat — the per-code loop is the pipeline's
            # long phase and prints nothing otherwise.
            print(f"      ... {codes_seen:,} codes scanned",
                  flush=True)
        group = group.sort_values("date").reset_index(drop=True)
        # Unwrap proxy arrays at the pandas→numpy boundary (see
        # _host_array): everything downstream must be RAW host numpy.
        close = _host_array(group["close"].values).astype(np.float64)
        # Day-granularity host datetime64: vectorized ops (isin) and
        # astype(object) -> datetime.date, no proxy dispatch.
        dates = _host_array(group["date"].values).astype("datetime64[D]")

        # Incremental mode: this code's target date set as a sorted
        # datetime64[D] array — np.isin replaces the former per-window
        # Python list-comprehension membership test (O(history) Python
        # calls per code per range_days).
        code_targets: set | None = None
        target_arr: np.ndarray | None = None
        if target_dates is not None:
            code_targets = target_dates.get(code)
            if not code_targets:
                # No missing targets for this code — skip entirely.
                continue
            target_arr = np.array(sorted(code_targets), dtype="datetime64[D]")

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
            # is a target are processed. datetime64[D].astype(object)
            # materializes datetime.date objects (C-level, host).
            if target_arr is not None:
                mask = np.isin(window_dates, target_arr)
                if not mask.any():
                    continue
                windows = windows[mask]
                window_dates = window_dates[mask].astype(object)

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

            if fft_result.shape[1] <= 1:
                # Only DC component (range_days <= 1) — can't extract
                # meaningful periods. Skip.
                continue

            # One-sided bin amplitude spectrum (exclude DC at index 0).
            # |X[k]| × 2 / N for k = 1..N//2. Shape: (n_windows, N//2).
            bin_amplitudes = (
                np.abs(fft_result[:, 1:]) * 2.0 / range_days
            )

            # Sanitize: NaN/inf (from NaN in close prices) → 0. Constant
            # windows are already 0 (detrended to zeros).
            if not np.all(np.isfinite(bin_amplitudes)):
                bin_amplitudes = np.nan_to_num(
                    bin_amplitudes, nan=0.0, posinf=0.0, neginf=0.0,
                )

            # Per-day recurring periodicity factors (amp / count /
            # strength), day-aligned: element j = day j + 2.
            amp_block, count_block, strength_block = compute_pattern_scores(
                windows, bin_amplitudes, range_days
            )

            # Headline: the TOP-STRENGTH day — the period at which price
            # both recurred repeatedly AND with meaningful amplitude.
            # 0 = no recurring period (all strengths 0: flat window,
            # pure trend, or one-off swings — count gates them out).
            top_idx = np.argmax(strength_block, axis=1)
            top_strength = strength_block[np.arange(n_windows), top_idx]
            has_recur = top_strength > 0
            periods = np.where(has_recur, top_idx + 2, 0).astype(np.int32)

            top_count = count_block[np.arange(n_windows), top_idx]
            top_amp = amp_block[np.arange(n_windows), top_idx]
            top_count = np.where(has_recur, top_count, 0.0)
            top_amp = np.where(has_recur, top_amp, 0.0)

            # Accumulate as typed arrays for efficient concatenation.
            sec_types_arr.append(
                np.full(n_windows, sec_type, dtype=object)
            )
            codes_arr.append(
                np.full(n_windows, code, dtype=object)
            )
            periods_arr.append(periods)
            strengths_arr.append(top_strength.astype(np.float64))
            counts_arr.append(top_count.astype(np.float64))
            amps_arr.append(top_amp.astype(np.float64))
            dates_arr.append(window_dates)
            range_days_arr.append(
                np.full(n_windows, range_days, dtype=np.int32)
            )
            # Append each window's full per-day spectrum rows (1-D views
            # into the 2-D blocks). Order matches the flat arrays above
            # (per (code, range_days), per window within the block).
            spectrums_flat.extend(list(amp_block))
            counts_flat.extend(list(count_block))
            strengths_flat.extend(list(strength_block))

    if not sec_types_arr:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    # Build the output frame with the REAL pandas class: pd.DataFrame is
    # a cudf.pandas-registered proxy constructor, and handing it the
    # three spectrum columns (lists of per-row ndarrays) sends it down
    # the cudf fast path, which transfers element-by-element — profiled
    # at 116 s for ONE 33k-row frame. The real class builds instantly.
    real_pd_df = pd.DataFrame._fsproxy_slow
    return real_pd_df({
        "sec_type": np.concatenate(sec_types_arr),
        "code": np.concatenate(codes_arr),
        "period_days": np.concatenate(periods_arr),
        "strength": np.concatenate(strengths_arr),
        "count_factor": np.concatenate(counts_arr),
        "amplitude": np.concatenate(amps_arr),
        "amplitude_spectrum": spectrums_flat,
        "count_spectrum": counts_flat,
        "strength_spectrum": strengths_flat,
        "last_date": np.concatenate(dates_arr),
        "range_days": np.concatenate(range_days_arr),
    })
