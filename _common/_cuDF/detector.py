"""DEPRECATED: use ``_common.df_utils._detector`` instead.

This module is a thin re-export shim kept for backward compatibility
with code that imports ``from _common._cuDF.detector import ...``.
The actual implementation now lives in
``_common/df_utils/_detector.py`` and is re-exported from the
top-level ``_common.df_utils`` package.
"""
from _common.df_utils._detector import (  # noqa: F401
    GPUInfo,
    detect_gpu,
    is_gpu_available,
    get_gpu_info,
    reset_cache,
)
