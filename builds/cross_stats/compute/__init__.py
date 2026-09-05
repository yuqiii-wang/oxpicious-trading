"""builds.cross_stats.compute — pair-grain transformation pipeline.

Ported from analyze.sec_alloc_perf_attribution.compute (2026-09-04); the
write target is hardwired to stats.cross_stats (config.TABLE). Split into
single-responsibility modules so steps can be tested/swapped independently:

  - ``_orchestrator`` : ``build_and_insert`` — the single public entry.
  - ``_lookback``     : incremental-mode lookback pre-filter.
  - ``_pivots``       : benchmark + ETF wide/long pivots.
  - ``_filters``      : DB skip check + incremental row filter.
  - ``_merge``        : subject-benchmark merge + shared weights.
  - ``_gpu_corr``     : BULK GPU rolling correlations (stride grid).
  - ``_etf``          : ETF amounts + capped ratio + MA5.
  - ``_sanitize``     : output column selection + DB sanitization.

GPU ACTIVATION
==============
The entry point (__main__.py) runs ``_common.df_utils._activate.activate()``
BEFORE any pandas import; this package additionally guards direct imports
(runner-style callers that skip __main__) with ``maybe_enable_cudf_pandas``.
"""
from __future__ import annotations

import sys

# ---------------------------------------------------------------------------
#  Process-level cudf.pandas activation — must run BEFORE any pandas import.
#  Only attempted once per process; no-ops gracefully without a GPU.
# ---------------------------------------------------------------------------
if "builds.cross_stats.compute" not in sys.modules:
    try:
        from _common.df_utils import maybe_enable_cudf_pandas
        _enabled, _desc = maybe_enable_cudf_pandas(mode="auto")
        if _enabled:
            print(f"    [GPU] {_desc}", flush=True)
    except Exception:
        pass  # No GPU / cuDF — fall through to CPU-only gracefully.

from ._orchestrator import build_and_insert  # noqa: E402

__all__ = ["build_and_insert"]
