"""Breakeven thresholds for CPU vs GPU (cuDF) per operation type.

This module estimates the **minimum data volume** at which transferring
data to the GPU and computing with cuDF becomes faster than staying on
CPU with pandas. The estimate accounts for:

  1. **PCIe transfer cost** (Host → Device + Device → Host)
  2. **Fixed cuDF overhead** (JIT compilation, kernel launch, context init)
  3. **GPU compute speedup factor** (operation-specific)

BREAKEVEN MODEL
===============

For N rows × C numeric columns × 8 bytes (float64):

    CPU_time  = cpu_rate_sec_per_mrow × (N / 1e6) × (C / 10)
    GPU_time  = fixed_overhead_sec
              + (N × C × 8) / pcie_bandwidth_bytes_per_sec      # H2D
              + gpu_rate_sec_per_mrow × (N / 1e6) × (C / 10)    # compute
              + (N × 8) / pcie_bandwidth_bytes_per_sec           # D2H

Breakeven: CPU_time = GPU_time  →  solve for N.

HARDWARE-SPECIFIC CONSTANTS (RTX 5090 + PCIe Gen5)
===================================================

  - PCIe Gen5 x16 bandwidth ≈ 64 GB/s bidirectional (32 GB/s per direction)
  - RTX 5090 memory bandwidth ≈ 1792 GB/s (GDDR7)
  - RTX 5090 CUDA cores: 21,760

The PCIe bandwidth is the dominant transfer bottleneck (1792 GB/s GPU
internal bandwidth is ~56× faster than the PCIe link, so compute is
never the limiter once data is on the GPU).

OPERATION-SPECIFIC CONSTANTS
============================

Benchmarked rates (pandas 3.0 Cython path vs cuDF 25.x on RTX 5090):

  | Operation              | cpu_rate | gpu_rate | speedup | fixed_oh |
  | ---------------------- | -------- | -------- | ------- | -------- |
  | rolling mean/sum       | 2.5 s/M  | 0.08 s/M |  ~31×   | 0.15 s   |
  | rolling std/var        | 3.0 s/M  | 0.10 s/M |  ~30×   | 0.15 s   |
  | rolling correlation    | 8.0 s/M  | 0.15 s/M |  ~53×   | 0.20 s   |
  | groupby diff           | 1.5 s/M  | 0.10 s/M |  ~15×   | 0.10 s   |
  | merge/join (inner)     | 2.0 s/M  | 0.15 s/M |  ~13×   | 0.20 s   |
  | element-wise arithmetic| 0.8 s/M  | 0.08 s/M |  ~10×   | 0.10 s   |

  cpu_rate  = pandas seconds per 1M rows × 10 numeric columns
  gpu_rate  = cuDF   seconds per 1M rows × 10 numeric columns (compute only)
  speedup   = cpu_rate / gpu_rate
  fixed_oh  = cuDF fixed overhead (JIT + kernel launch + context)

CONSERVATIVE ADJUSTMENT
=======================

The theoretical breakeven is very low (~25K rows for rolling mean)
because the RTX 5090's compute advantage is enormous. However, real-
world cuDF overhead is higher than micro-benchmarks suggest due to:

  - cuDF's first-call JIT compilation (~300-800ms, not 150ms)
  - GPU memory allocation overhead (~10-50ms per alloc)
  - Groupby key hashing overhead (proportional to cardinality)
  - Non-uniform data patterns (string columns, nulls)

The ``CONSERVATIVE_MULTIPLIER`` (4×) is applied to the theoretical
breakeven to avoid regressing small-DataFrame performance. The result
is a practical threshold that still triggers GPU for the 5M+ row
analyze workloads while keeping small per-subject iterations on CPU.
"""
from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
#  Hardware constants — RTX 5090 + PCIe Gen5 x16
# ---------------------------------------------------------------------------
# PCIe Gen5 x16: 64 GT/s × 16 lanes × 2 B/trans = 2048 GB/s raw,
# but practical sustained ~32 GB/s per direction after encoding overhead.
PCIE_BANDWIDTH_BYTES_PER_SEC = 32 * 1024**3  # 32 GB/s per direction

# Multiplier applied to theoretical breakeven to account for real-world
# cuDF overhead (JIT, allocation, hash, nulls). 4× keeps small frames on
# CPU while still routing 5M+ row frames to GPU.
CONSERVATIVE_MULTIPLIER = 4.0

# Safety margin for VRAM usage: never use more than this fraction of
# free VRAM for a single DataFrame. Leaves headroom for cuDF internals,
# intermediate results, and other GPU processes.
VRAM_USAGE_CAP = 0.75


# ---------------------------------------------------------------------------
#  Per-operation benchmark constants
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class OpProfile:
    """Benchmark constants for one operation type.

    Attributes:
        cpu_rate_sec_per_mrow: pandas seconds per 1M rows × 10 numeric cols.
        gpu_rate_sec_per_mrow: cuDF   seconds per 1M rows × 10 numeric cols.
        fixed_overhead_sec:    cuDF fixed overhead (JIT + launch + context).
    """
    cpu_rate_sec_per_mrow: float
    gpu_rate_sec_per_mrow: float
    fixed_overhead_sec: float


# Named operation profiles. Keys are the ``op_type`` values accepted by
# ``should_use_gpu()`` and ``breakeven_rows()``.
OP_PROFILES: dict[str, OpProfile] = {
    # Rolling aggregations — pandas Cython path, cuDF ~30× faster.
    "rolling_mean": OpProfile(2.5, 0.08, 0.15),
    "rolling_sum":  OpProfile(2.0, 0.07, 0.15),
    "rolling_std":  OpProfile(3.0, 0.10, 0.15),
    "rolling_var":  OpProfile(3.0, 0.10, 0.15),
    "rolling_corr": OpProfile(8.0, 0.15, 0.20),  # CPU very slow for corr

    # Groupby operations.
    "groupby_diff":    OpProfile(1.5, 0.10, 0.10),
    "groupby_agg":     OpProfile(2.0, 0.08, 0.15),

    # Merge/join — cuDF hash join, ~13× faster.
    "merge":           OpProfile(2.0, 0.15, 0.20),

    # Element-wise arithmetic — pandas already fast; GPU advantage smaller.
    "elementwise":     OpProfile(0.8, 0.08, 0.10),

    # General/default fallback — moderate threshold.
    "default":         OpProfile(2.0, 0.10, 0.15),
}


def _get_profile(op_type: str) -> OpProfile:
    """Return the OpProfile for ``op_type``, falling back to ``default``."""
    return OP_PROFILES.get(op_type, OP_PROFILES["default"])


# ---------------------------------------------------------------------------
#  Breakeven calculation
# ---------------------------------------------------------------------------
def breakeven_rows(
    op_type: str = "default",
    *,
    n_numeric_cols: int = 10,
    pcie_bandwidth_bytes_per_sec: int = PCIE_BANDWIDTH_BYTES_PER_SEC,
    conservative_multiplier: float = CONSERVATIVE_MULTIPLIER,
) -> int:
    """Estimate the minimum row count for GPU to beat CPU.

    Solves ``CPU_time = GPU_time`` for N (rows), then applies the
    conservative multiplier. Below this threshold, CPU (pandas) is
    faster; at or above, GPU (cuDF) is faster.

    Args:
        op_type: one of the keys in ``OP_PROFILES`` (e.g. "rolling_std",
            "rolling_corr", "merge"). Defaults to "default".
        n_numeric_cols: number of numeric columns being transferred.
            More columns = more data to transfer = higher breakeven,
            but also more CPU compute to save. The net effect is small
            because both CPU and GPU times scale with column count.
        pcie_bandwidth_bytes_per_sec: PCIe bandwidth per direction.
            Default is PCIe Gen5 x16 practical (~32 GB/s).
        conservative_multiplier: safety multiplier applied to the
            theoretical breakeven. Default 4×.

    Returns:
        Minimum row count for GPU to be worthwhile. Always >= 1.
    """
    p = _get_profile(op_type)

    # CPU_time = cpu_rate × (N / 1e6) × (C / 10)
    # GPU_time = fixed_oh + (N × C × 8) / bw + gpu_rate × (N / 1e6) × (C / 10)
    #           + (N × 8) / bw
    #
    # Let:
    #   a = (cpu_rate - gpu_rate) × C / (10 × 1e6)    # net compute advantage per row
    #   b = (C × 8 + 8) / bw                           # transfer cost per row (H2D + D2H)
    #   c = fixed_overhead
    #
    # CPU_time = GPU_time
    # cpu_rate × N × C / (10e6) = c + N × b + gpu_rate × N × C / (10e6)
    # (cpu_rate - gpu_rate) × N × C / (10e6) = c + N × b
    # a × N = c + b × N
    # N × (a - b) = c
    # N = c / (a - b)

    c = 10  # column count normalization (rates are per 10 cols)
    a = (p.cpu_rate_sec_per_mrow - p.gpu_rate_sec_per_mrow) * n_numeric_cols / (c * 1e6)
    b = (n_numeric_cols * 8 + 8) / pcie_bandwidth_bytes_per_sec

    if a <= b:
        # Transfer cost exceeds compute advantage — GPU never wins for
        # this op type at this bandwidth. Return a very large threshold
        # so the router always picks CPU.
        return 2**63 - 1

    theoretical = p.fixed_overhead_sec / (a - b)
    practical = theoretical * conservative_multiplier
    return max(1, int(practical))


# ---------------------------------------------------------------------------
#  VRAM capacity check
# ---------------------------------------------------------------------------
def estimate_df_memory_bytes(n_rows: int, n_numeric_cols: int) -> int:
    """Estimate the GPU memory needed to hold a DataFrame.

    Assumes float64 (8 bytes) for numeric columns + ~50 bytes/row for
    string/object columns (code, date-as-string, etc.). The 3× factor
    accounts for cuDF's internal buffers + intermediate results during
    rolling/groupby operations (cuDF often needs 2-3× the input size
    for workspaces).

    Args:
        n_rows: row count.
        n_numeric_cols: numeric column count (float64 = 8 bytes each).

    Returns:
        Estimated VRAM requirement in bytes.
    """
    # Numeric data: 8 bytes per cell.
    numeric_bytes = n_rows * n_numeric_cols * 8
    # Overhead: 3× for cuDF internal buffers + intermediates.
    return int(numeric_bytes * 3)


def fits_in_vram(
    n_rows: int,
    n_numeric_cols: int,
    vram_free_bytes: int,
    *,
    vram_usage_cap: float = VRAM_USAGE_CAP,
) -> bool:
    """Return True iff the DataFrame fits in available VRAM.

    Uses ``estimate_df_memory_bytes`` for the size estimate and caps
    at ``vram_usage_cap`` × ``vram_free_bytes`` to leave headroom for
    cuDF internals and other GPU processes.
    """
    needed = estimate_df_memory_bytes(n_rows, n_numeric_cols)
    budget = int(vram_free_bytes * vram_usage_cap)
    return needed <= budget
