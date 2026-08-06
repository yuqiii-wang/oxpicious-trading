"""CPU-vs-GPU router for analyze operations.

The single entry point is ``should_use_gpu()``, which combines three
checks:

  1. **CUDA available?** — ``detector.is_gpu_available()`` (cached)
  2. **Data volume above breakeven?** — ``thresholds.breakeven_rows()``
  3. **DataFrame fits in VRAM?** — ``thresholds.fits_in_vram()``

If all three pass, GPU (cuDF) is worthwhile. Otherwise, CPU (pandas)
is faster. The decision is logged once per (op_type, n_rows) pair so
repeated calls don't spam stdout.

USAGE
=====

    from analyze._common._cuDF import should_use_gpu

    if should_use_gpu(df, op_type="rolling_std"):
        import cudf
        gdf = cudf.from_pandas(df)
        result = gdf.groupby(keys).rolling(W).std(ddof=0)
        result = result.to_pandas()
    else:
        result = df.groupby(keys).rolling(W).std(ddof=0)

The ``op_type`` string selects the breakeven threshold from
``thresholds.OP_PROFILES``. Common values:

    "rolling_mean"  — groupby.rolling().mean()
    "rolling_std"   — groupby.rolling().std()
    "rolling_corr"  — df.rolling(N).corr(series)
    "groupby_diff"  — groupby().diff()
    "merge"         — df.merge(other)
    "elementwise"   — vectorized arithmetic
    "default"       — moderate threshold (fallback)
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

from analyze._common._cuDF.detector import detect_gpu, GPUInfo
from analyze._common._cuDF.thresholds import (
    breakeven_rows,
    estimate_df_memory_bytes,
    fits_in_vram,
    OP_PROFILES,
    VRAM_USAGE_CAP,
)


@dataclass(frozen=True)
class GPUDecision:
    """Result of ``should_use_gpu()``.

    Attributes:
        use_gpu: True iff GPU (cuDF) should be used.
        reason:  human-readable explanation of the decision.
        gpu_info: the GPUInfo snapshot (always populated, even when
                  use_gpu is False — useful for logging).
        breakeven: the row threshold that was applied.
        est_vram_bytes: estimated VRAM needed for the DataFrame.
    """
    use_gpu: bool
    reason: str
    gpu_info: GPUInfo
    breakeven: int
    est_vram_bytes: int


# Cache of decisions already logged, so we only print each unique
# (op_type, n_rows) decision once per process. Keyed by
# (op_type, n_rows, n_numeric_cols).
_logged_decisions: set[tuple[str, int, int]] = set()


def _count_numeric_cols(df) -> int:
    """Count numeric columns in a pandas or cuDF DataFrame."""
    try:
        import pandas as pd
        if isinstance(df, pd.DataFrame):
            return sum(1 for c in df.columns
                       if pd.api.types.is_numeric_dtype(df[c]))
    except ImportError:
        pass
    # Fallback: assume all columns are numeric (conservative for VRAM).
    return len(df.columns) if hasattr(df, "columns") else 10


def should_use_gpu(
    df,
    op_type: str = "default",
    *,
    n_numeric_cols: int | None = None,
    verbose: bool = True,
) -> bool:
    """Decide whether to use GPU (cuDF) or CPU (pandas) for ``df``.

    Combines three checks:
      1. CUDA + cuDF available (cached ``detect_gpu()``).
      2. ``len(df)`` >= operation-specific breakeven threshold.
      3. Estimated VRAM fits in available GPU memory.

    Args:
        df: pandas (or cuDF) DataFrame. Only ``len(df)`` and column
            count are read — no data is copied.
        op_type: operation type key from ``OP_PROFILES``. Common:
            "rolling_mean", "rolling_std", "rolling_corr",
            "groupby_diff", "merge", "elementwise", "default".
        n_numeric_cols: override for numeric column count. When None,
            counted from ``df.dtypes``. For wide frames (25+ cols)
            this affects the VRAM estimate and breakeven.
        verbose: when True, log the decision to stdout the first time
            each (op_type, n_rows, n_numeric_cols) combination is seen.

    Returns:
        True iff GPU (cuDF) should be used; False for CPU (pandas).
    """
    decision = decide_gpu(df, op_type=op_type,
                          n_numeric_cols=n_numeric_cols)

    if verbose:
        key = (op_type, len(df), decision.est_vram_bytes)
        if key not in _logged_decisions:
            _logged_decisions.add(key)
            tag = "GPU" if decision.use_gpu else "CPU"
            print(f"    [cuDF router] {tag}: {decision.reason}",
                  file=sys.stdout, flush=True)

    return decision.use_gpu


def decide_gpu(
    df,
    op_type: str = "default",
    *,
    n_numeric_cols: int | None = None,
) -> GPUDecision:
    """Full decision with structured result (no logging).

    Use this when you need the reason/breakeven/VRAM details, e.g. for
    structured logging or testing. For the simple boolean, use
    ``should_use_gpu()``.
    """
    n_rows = len(df)
    if n_numeric_cols is None:
        n_numeric_cols = _count_numeric_cols(df)

    # Check 1: CUDA + cuDF available?
    gpu = detect_gpu()
    if not gpu.available:
        return GPUDecision(
            use_gpu=False,
            reason=f"GPU unavailable ({gpu.reason})",
            gpu_info=gpu,
            breakeven=0,
            est_vram_bytes=0,
        )

    # Check 2: data volume above breakeven?
    threshold = breakeven_rows(op_type, n_numeric_cols=n_numeric_cols)
    if n_rows < threshold:
        return GPUDecision(
            use_gpu=False,
            reason=(f"{n_rows:,} rows < {threshold:,} breakeven "
                    f"for '{op_type}' (staying on CPU)"),
            gpu_info=gpu,
            breakeven=threshold,
            est_vram_bytes=0,
        )

    # Check 3: fits in VRAM?
    est_vram = estimate_df_memory_bytes(n_rows, n_numeric_cols)
    if not fits_in_vram(n_rows, n_numeric_cols, gpu.vram_free_bytes):
        budget = int(gpu.vram_free_bytes * VRAM_USAGE_CAP)
        return GPUDecision(
            use_gpu=False,
            reason=(f"estimated {est_vram / 1024**3:.2f} GB VRAM exceeds "
                    f"budget {budget / 1024**3:.2f} GB free "
                    f"({gpu.device_name})"),
            gpu_info=gpu,
            breakeven=threshold,
            est_vram_bytes=est_vram,
        )

    return GPUDecision(
        use_gpu=True,
        reason=(f"{n_rows:,} rows × {n_numeric_cols} cols >= "
                f"{threshold:,} breakeven for '{op_type}', "
                f"~{est_vram / 1024**3:.2f} GB VRAM "
                f"({gpu.device_name})"),
        gpu_info=gpu,
        breakeven=threshold,
        est_vram_bytes=est_vram,
    )


def list_thresholds(n_numeric_cols: int = 10) -> dict[str, int]:
    """Return a dict of {op_type: breakeven_rows} for inspection.

    Useful for logging or debugging the router's thresholds.
    """
    return {
        op: breakeven_rows(op, n_numeric_cols=n_numeric_cols)
        for op in OP_PROFILES
    }
