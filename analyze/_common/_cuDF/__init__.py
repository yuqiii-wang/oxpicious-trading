"""cuDF / GPU acceleration router for analyze scripts.

Provides a single decision function — ``should_use_gpu(df, op_type)``
— that checks three conditions and returns True only when GPU
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
     (3× the numeric data size, for cuDF buffers + intermediates) is
     checked against 75% of free VRAM to leave headroom.

BREAKEVEN THRESHOLDS (RTX 5090, PCIe Gen5, 4× conservative multiplier)
======================================================================

For 10 numeric columns (the profile normalization):

    rolling_mean :    ~25K rows (theoretical) → ~100K rows (conservative)
    rolling_std  :    ~30K rows (theoretical) → ~120K rows (conservative)
    rolling_corr :    ~10K rows (theoretical) →  ~40K rows (conservative)
    groupby_diff :    ~80K rows (theoretical) → ~320K rows (conservative)
    merge        :   ~130K rows (theoretical) → ~520K rows (conservative)
    elementwise  :   ~160K rows (theoretical) → ~640K rows (conservative)

The analyze workloads (5M-8M rows) are well above all thresholds, so
GPU will be selected whenever CUDA + cuDF are available.

USAGE
=====

    from analyze._common._cuDF import should_use_gpu

    if should_use_gpu(df, op_type="rolling_std"):
        import cudf
        gdf = cudf.from_pandas(df)
        # ... cuDF operations ...
        result = gdf.to_pandas()
    else:
        # ... pandas operations ...

The decision is logged to stdout once per unique (op_type, n_rows)
combination, so repeated calls in a loop don't spam output.

MODULES
=======

  - ``detector``  : CUDA / cuDF availability detection (cached).
  - ``thresholds``: Breakeven row counts per operation type.
  - ``router``    : ``should_use_gpu()`` combining both + VRAM check.
"""
from analyze._common._cuDF.detector import (
    GPUInfo,
    detect_gpu,
    is_gpu_available,
    get_gpu_info,
    reset_cache,
)
from analyze._common._cuDF.thresholds import (
    OP_PROFILES,
    breakeven_rows,
    estimate_df_memory_bytes,
    fits_in_vram,
)
from analyze._common._cuDF.router import (
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
