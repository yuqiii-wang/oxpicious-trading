"""DEPRECATED: use ``_common.df_utils._router`` instead.

This module is a thin re-export shim kept for backward compatibility
with code that imports ``from _common._cuDF.router import ...``. The
actual implementation now lives in ``_common/df_utils/_router.py`` and
is re-exported from the top-level ``_common.df_utils`` package.
"""
from _common.df_utils._router import (  # noqa: F401
    GPUDecision,
    should_use_gpu,
    decide_gpu,
    list_thresholds,
)
