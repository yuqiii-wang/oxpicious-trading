"""CPU-vs-GPU router — bulk data size controller for cudf.pandas.

When the process-level ``cudf.pandas`` hook is active (enabled at the
entry point via ``_common.df_utils._activate.activate()``), ALL pandas
operations transparently run on GPU via cuDF. This router exists to
answer a single question:

  "Is this DataFrame large enough for GPU acceleration to matter?"

The router performs TWO checks:

  1. **GPU available?** - ``detector.is_gpu_available()`` (cached).
  2. **Data volume above breakeven?** - ``thresholds.breakeven_rows()``.

There is NO VRAM fit check — cudf.pandas manages GPU memory internally
and gracefully falls back to CPU when VRAM is insufficient.

The return value is for AWARENESS / LOGGING only — callers use it to
print a one-time notice per (op_type, n_rows) combination. Code-path
branching based on this return value is deprecated (the single pandas
code path handles both CPU and GPU modes via cudf.pandas).

USAGE
=====

    from _common.df_utils import should_use_gpu

    if should_use_gpu(df, op_type="rolling_mean"):
        print("This workload is GPU-worthy — cudf.pandas will accelerate it.", flush=True)
    # ... single pandas code path — cudf.pandas handles GPU/CPU routing.

The ``op_type`` string selects the breakeven threshold from
``thresholds.OP_PROFILES``. Common values:

    "rolling_mean"  - groupby.rolling().mean()
    "rolling_std"   - groupby.rolling().std()
    "rolling_corr"  - df.rolling(N).corr(series)
    "groupby_diff"  - groupby().diff()
    "groupby_shift" - groupby().shift()
    "groupby_agg"   - groupby().agg()
    "merge"         - df.merge(other)
    "elementwise"   - vectorized arithmetic
    "default"       - moderate threshold (fallback)
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

from _common.df_utils._detector import detect_gpu, GPUInfo
from _common.df_utils._thresholds import (
    breakeven_rows,
    estimate_df_memory_bytes,
    OP_PROFILES,
)


@dataclass(frozen=True)
class GPUDecision:
    """Result of ``should_use_gpu()``.

    Attributes:
        use_gpu: True iff GPU is available AND data is above breakeven.
        reason:  human-readable explanation of the decision.
        gpu_info: the GPUInfo snapshot (always populated).
        breakeven: the row threshold that was applied.
        est_vram_bytes: estimated VRAM needed (informational only).
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
    # Fallback: assume all columns are numeric (conservative).
    return len(df.columns) if hasattr(df, "columns") else 10


def should_use_gpu(
    df,
    op_type: str = "default",
    *,
    n_numeric_cols: int | None = None,
    verbose: bool = True,
) -> bool:
    """Check whether the DataFrame is large enough for GPU acceleration.

    Performs TWO checks (no VRAM fit check — cudf.pandas handles that):
      1. GPU available? (cached ``detect_gpu()``)
      2. ``len(df)`` >= operation-specific breakeven threshold?

    The return value is for AWARENESS / LOGGING only. Callers should
    NOT use this for code-path branching — the single pandas code
    path handles both CPU and GPU modes via cudf.pandas.

    Args:
        df: pandas DataFrame. Only ``len(df)`` and column count are read.
        op_type: operation type key from ``OP_PROFILES``.
        n_numeric_cols: override for numeric column count.
        verbose: when True, log the decision to stdout the first time
            each (op_type, n_rows, n_numeric_cols) combination is seen.

    Returns:
        True iff GPU is available AND data volume meets breakeven.
    """
    decision = decide_gpu(df, op_type=op_type,
                          n_numeric_cols=n_numeric_cols)

    if verbose:
        n_nc = decision.est_vram_bytes  # use as proxy for key stability
        key = (op_type, len(df), n_nc)
        if key not in _logged_decisions:
            _logged_decisions.add(key)
            tag = "GPU-worthy" if decision.use_gpu else "CPU-bound"
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

    TWO checks only: GPU availability + breakeven threshold.
    No VRAM fit check — cudf.pandas manages GPU memory internally.
    """
    n_rows = len(df)
    if n_numeric_cols is None:
        n_numeric_cols = _count_numeric_cols(df)

    # Check 1: GPU available?
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
    est_vram = estimate_df_memory_bytes(n_rows, n_numeric_cols)

    if n_rows < threshold:
        return GPUDecision(
            use_gpu=False,
            reason=(f"{n_rows:,} rows < {threshold:,} breakeven "
                    f"for '{op_type}' (cudf.pandas will keep on CPU)"),
            gpu_info=gpu,
            breakeven=threshold,
            est_vram_bytes=est_vram,
        )

    return GPUDecision(
        use_gpu=True,
        reason=(f"{n_rows:,} rows x {n_numeric_cols} cols >= "
                f"{threshold:,} breakeven for '{op_type}' "
                f"(cudf.pandas will accelerate on "
                f"{gpu.device_name})"),
        gpu_info=gpu,
        breakeven=threshold,
        est_vram_bytes=est_vram,
    )


def list_thresholds(n_numeric_cols: int = 10) -> dict[str, int]:
    """Return a dict of {op_type: breakeven_rows} for inspection."""
    return {
        op: breakeven_rows(op, n_numeric_cols=n_numeric_cols)
        for op in OP_PROFILES
    }
