"""CUDA kernel registry for builds — one algo/formula per .cpp file.

CONVENTION
==========

Algo implementations in builds default to the pandas/cudf single-code
path (CPU, or GPU under cudf.pandas when every op is cudf-native).
Write a ``.cpp`` kernel here ONLY when cudf/cupy cannot express the
algo (missing primitive like grouped-ewm, or a fused formulation that
would otherwise cost dozens of whole-column passes). Rules:

  - one formula/algo per ``.cpp`` file, named after the formula
    (``ema.cpp`` -> ``ema_adjust_false`` kernel);
  - the matching bridge module (``ema.py``) compiles the .cpp at import
    time via ``cupy.RawModule`` (NVRTC) and exposes a typed wrapper;
  - importing ``builds`` eagerly imports this package, so kernels are
    compiled once at script start — never lazily mid-run; a compile
    failure prints a warning and falls back to CPU paths.

Modules:
    ema: adjust=False EMA (see ema.cpp for the math)
"""
import builtins

try:
    from builds._algo_kernels.ema import ema_adjust_false

    __all__ = ["ema_adjust_false"]
except ImportError as _e:                       # noqa: F841 (re-raised msg)
    builtins.print(
        f"[kernels] WARNING: CUDA kernel compile failed — CPU paths only "
        f"({_e})", flush=True)
    __all__: list[str] = []
