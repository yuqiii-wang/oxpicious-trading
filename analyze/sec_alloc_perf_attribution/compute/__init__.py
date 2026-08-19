"""Sec alloc perf attribution — compute package.

This package contains the transformation logic for
``analyze.sec_alloc_perf_attribution``. It is split into small,
single-responsibility modules (see docstrings in each file) so that
individual steps can be tested and swapped independently.

GPU ACTIVATION
==============

The ``maybe_enable_cudf_pandas`` hook from ``_common.df_utils`` is
called here to activate the process-level ``cudf.pandas`` backend
BEFORE any pandas operations. This must happen before the first
``import pandas`` — the lazy import contract ensures that importing
this package does NOT eagerly import pandas.

Usage::

    # In __main__.py or run.py — BEFORE importing pandas anything:
    from analyze.sec_alloc_perf_attribution.compute import (
        build_and_insert,  # single public function
    )
    # cudf.pandas is now active (if GPU available).

MODULES
=======

  - ``_orchestrator`` : ``build_and_insert`` — the single public entry.
  - ``_lookback``     : Step 0 — incremental-mode lookback pre-filter.
  - ``_pivots``       : Step 1 — benchmark + ETF wide/long pivots.
  - ``_filters``      : Steps 2 + 8 — DB skip check + row filter.
  - ``_merge``        : Steps 3 + 4 — subject-benchmark merge + shared wts.
  - ``_gpu_corr``     : Step 5b — BULK GPU rolling correlations (ALL subjects).
  - ``_correlations`` : Step 5 — per-subject rolling corr (backward compat).
  - ``_etf``          : Steps 6 + 7 — ETF amounts + MA5 ratio.
  - ``_sanitize``     : Step 9 — output column selection + sanitization.
"""
from __future__ import annotations

import sys

# ---------------------------------------------------------------------------
#  Process-level cudf.pandas activation
#  MUST run BEFORE any pandas import.
# ---------------------------------------------------------------------------
# Only attempt activation once per process.
if "analyze.sec_alloc_perf_attribution.compute" not in sys.modules:
    try:
        from _common.df_utils import maybe_enable_cudf_pandas
        _enabled, _desc = maybe_enable_cudf_pandas(mode="auto")
        if _enabled:
            print(f"    [GPU] {_desc}", flush=True)
    except Exception:
        pass  # No GPU / cuDF — fall through to CPU-only gracefully.


# ---------------------------------------------------------------------------
#  Public API — single function re-exported
# ---------------------------------------------------------------------------
from ._orchestrator import build_and_insert  # noqa: E402

__all__ = ["build_and_insert"]
