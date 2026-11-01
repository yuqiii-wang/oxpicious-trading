"""Standalone correlations entry point — ``python -m analysis.market_beta.corr``.

Re-exports the correlations step's public API (implemented in
``analysis.market_beta.correlations``) so callers can import from
either location.
"""
from analysis.market_beta.correlations import (  # noqa: F401
    find_missing_corr_window_ends,
    run_correlations,
    TABLE,
)
