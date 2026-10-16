"""CUDA kernel bridges for _common.df_utils (the shared compute layer).

One ``<formula>.cpp`` NVRTC kernel + ``<formula>.py`` cupy bridge per
formula family, mirroring the ``builds/_algo_kernels`` layout. Import
the bridges LAZILY from inside a try/except GPU branch — module import
triggers NVRTC compilation and requires an active GPU.

This package's ``__init__`` deliberately imports NOTHING: every
``_common.df_utils`` consumer (CPU hosts included) executes it, so the
cupy import must stay behind the callers' lazy bridges.

- ``ewm``: grouped EWM mean (adjust=False, ignore_na=True, per-group
  ``min_periods``) — the Wilder-smoothing kernel shared by
  ``analysis.mov_ave_spread.steps.rsi`` (the mov_ave_rsi build) and
  ``analysis.trends.trend_state`` (the RSI re-entry trend classifier);
  replaces the pandas ``groupby.ewm`` CPU fallback (32 fallback
  lines/run on 6.6M stock rows). MOVED 2026-10-15 from
  ``analysis.mov_ave_spread._kernels`` (verbatim kernel + bridge; the
  second consumer made it a shared-library primitive).
"""
