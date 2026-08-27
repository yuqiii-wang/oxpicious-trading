"""Standalone correlations entry point — ``python -m analyze.industry_sentiments.corr``.

Re-exports the correlations step's public API (implemented in
``analyze.industry_sentiments.correlations``) so callers can import from
either location.
"""
from analyze.industry_sentiments.correlations import (  # noqa: F401
    find_missing_corr_window_ends,
    run_correlations,
    TABLE,
)
