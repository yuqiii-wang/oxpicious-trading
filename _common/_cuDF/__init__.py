"""DEPRECATED: use ``_common.df_utils`` instead.

This package is a thin re-export shim kept for backward compatibility
with code that imports ``from _common._cuDF import should_use_gpu``.
The actual implementation now lives in ``_common/df_utils/`` and is
the consolidated entry point for DataFrame utilities + the cuDF
router. New code should import from ``_common.df_utils`` directly.

The original docstring (kept for historical reference):

    cuDF / GPU acceleration router for analyze scripts.

    Provides a single decision function - ``should_use_gpu(df, op_type)``
    - that checks three conditions and returns True only when GPU
    acceleration will actually be faster than CPU (pandas):

      1. **CUDA available?** A working NVIDIA GPU + cuDF installation is
         detected via ``nvidia-smi`` + ``import cudf`` + a smoke-test
         ``cudf.DataFrame()`` allocation. Detection is lazy and cached.

      2. **Data volume above breakeven?** The minimum row count at which
         GPU beats CPU is estimated per operation type, accounting for
         PCIe transfer cost (H2D + D2H), cuDF fixed overhead (JIT + kernel
         launch), and the GPU compute speedup factor. See
         ``thresholds.py`` for the full model.

      3. **DataFrame fits in VRAM?** The estimated GPU memory needed
         (3x the numeric data size, for cuDF buffers + intermediates) is
         checked against 75% of free VRAM to leave headroom.
"""
from _common.df_utils._detector import (  # noqa: F401
    GPUInfo,
    detect_gpu,
    is_gpu_available,
    get_gpu_info,
    reset_cache,
)
from _common.df_utils._thresholds import (  # noqa: F401
    OP_PROFILES,
    breakeven_rows,
    estimate_df_memory_bytes,
    fits_in_vram,
)
from _common.df_utils._router import (  # noqa: F401
    GPUDecision,
    should_use_gpu,
    decide_gpu,
    list_thresholds,
)

__all__ = [
    # Detector
    "GPUInfo",
    "detect_gpu",
    "is_gpu_available",
    "get_gpu_info",
    "reset_cache",
    # Thresholds
    "OP_PROFILES",
    "breakeven_rows",
    "estimate_df_memory_bytes",
    "fits_in_vram",
    "list_thresholds",
    # Router
    "GPUDecision",
    "should_use_gpu",
    "decide_gpu",
]
