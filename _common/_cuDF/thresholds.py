"""DEPRECATED: use ``_common.df_utils._thresholds`` instead.

This module is a thin re-export shim kept for backward compatibility
with code that imports ``from _common._cuDF.thresholds import ...``.
The actual implementation now lives in
``_common/df_utils/_thresholds.py`` and is re-exported from the
top-level ``_common.df_utils`` package.
"""
from _common.df_utils._thresholds import (  # noqa: F401
    OP_PROFILES,
    OpProfile,
    PCIE_BANDWIDTH_BYTES_PER_SEC,
    CONSERVATIVE_MULTIPLIER,
    VRAM_USAGE_CAP,
    breakeven_rows,
    estimate_df_memory_bytes,
    fits_in_vram,
)
