"""CUDA kernels for analyze.mov_ave_spread.

One subdirectory per formula family, mirroring the
``builds/_algo_kernels`` layout (``<formula>.cpp`` NVRTC kernel +
``<formula>.py`` cupy bridge). Import the bridges LAZILY — module import
triggers NVRTC compilation and requires an active GPU.

- ``ewm``: grouped EWM mean (adjust=False, ignore_na=True) — replaces
  the pandas ``groupby.ewm`` CPU fallback in rsi.py (B-A4-class storm;
  32 fallback lines/run on 6.6M stock rows).
"""
from analyze.mov_ave_spread._kernels.ewm import grouped_ewm_mean

__all__ = ["grouped_ewm_mean"]
