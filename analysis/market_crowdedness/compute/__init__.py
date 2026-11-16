"""analysis.market_crowdedness.compute — the crowdedness compute core.

Layout: ``metrics`` holds the shared metric cores (the sanctioned host
boundaries + the GPU Pearson corr), ``finalize`` the shared defined-row
gate + percentile/score finalize, ``pinned`` the year-pinned hot-set
planner/evaluator, ``pool_batch`` one whole-pool chunk's compute, and
``driver`` the CHUNK_PAIR_BUDGET chunked driver.

The pipeline consumes only the driver entry; everything else is
package-internal (underscore names — pure code motion from the former
monolithic __main__.py, names unchanged).
"""
from __future__ import annotations

from analysis.market_crowdedness.compute.driver import _compute_crowdedness

__all__ = ["_compute_crowdedness"]
