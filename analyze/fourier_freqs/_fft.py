"""FFT backend routing (cudf -> cupy -> cpu) shared by the fourier_freqs
compute + pattern-score modules.

cuDF implements NO FFT (any release, incl. 26.08), so spectral transforms
cannot go through the cudf.pandas proxy. They are routed EXPLICITLY to
CuPy (cuFFT, GPU) when a CUDA device is present, else numpy (CPU) — the
same cascade used by _common.df_utils.rolling_corr for ops cuDF lacks.
"""
from __future__ import annotations

import logging

import numpy as np

from _common.df_utils.rolling_corr import release_cupy_pool

logger = logging.getLogger(__name__)

_cupy_ok: bool | None = None
_rfft_backend_logged: bool = False
_cupy_fail_logged: bool = False


def _log_cupy_fail(fn: str, e: Exception) -> None:
    """Print a cupy failure -> numpy fallback ONCE (not per call)."""
    global _cupy_fail_logged
    if not _cupy_fail_logged:
        logger.warning(
            "[fourier_freqs] cupy %s failed (%s: %s) -> numpy CPU "
            "(subsequent failures silent)",
            fn, type(e).__name__, e)
        print(f"    [fourier_freqs] cupy {fn} failed "
              f"({type(e).__name__}: {e}) -> numpy CPU "
              f"(subsequent failures silent)", flush=True)
        _cupy_fail_logged = True


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


def _rfft(windows: np.ndarray, axis: int = 1) -> np.ndarray:
    """Real FFT ``rfft(windows)`` with cudf->cupy->cpu routing.

    ``windows`` is a float64 ndarray (already detrended). cuDF implements
    NO FFT, so the transform is routed EXPLICITLY to CuPy (cuFFT, GPU) when
    a CUDA device is present, else numpy (CPU). The returned complex128
    spectrum keeps all downstream logic (amplitude, argmax) unchanged.
    """
    global _rfft_backend_logged
    if _cupy_available():
        import cupy as cp

        try:
            x = cp.asarray(windows)
            out = cp.fft.rfft(x, axis=axis).get()  # D2H (complex128)
            del x  # pool-free the device input before releasing
            release_cupy_pool()
            if not _rfft_backend_logged:
                logger.info("[fourier_freqs] FFT backend: cupy (GPU/cuFFT)")
                print("    [fourier_freqs] FFT backend: cupy (GPU/cuFFT)",
                      flush=True)
                _rfft_backend_logged = True
            return out
        except Exception as e:
            # MemoryError, missing cuFFT shared libs (libcufft.so.*),
            # driver hiccups — any GPU failure falls back to numpy.
            _log_cupy_fail("rFFT", e)
    if not _rfft_backend_logged:
        logger.info("[fourier_freqs] FFT backend: numpy (CPU)")
        print("    [fourier_freqs] FFT backend: numpy (CPU)", flush=True)
        _rfft_backend_logged = True
    return np.fft.rfft(windows, axis=axis)


def _irfft(F: np.ndarray, n: int, axis: int = 1) -> np.ndarray:
    """Inverse real FFT with the same cudf->cupy->cpu routing as _rfft."""
    if _cupy_available():
        import cupy as cp

        try:
            out = cp.fft.irfft(cp.asarray(F), n=n, axis=axis).get()
            release_cupy_pool()
            return out
        except Exception as e:
            _log_cupy_fail("irFFT", e)
    return np.fft.irfft(F, n=n, axis=axis)
