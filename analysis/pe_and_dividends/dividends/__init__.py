"""Dividend-side logic for analysis.pe_and_dividends.

  - dividends.compute — trailing-12m DPS (event-based rolling 365d
                        sum), the index dividend_yield (cap-weighted
                        constituent-yield aggregation) and the
                        etf/stock dividend_yield (trailing-12m DPS /
                        close, merge_asof)
  - dividends.config  — analysis.dividends identity + the trailing-12m
                        dividend window
"""
from analysis.pe_and_dividends.dividends.compute import (
    compute_index_dividend_yield,
    compute_simple_dividend_yield,
    compute_trailing_12m_dps,
)

__all__ = [
    "compute_index_dividend_yield",
    "compute_simple_dividend_yield",
    "compute_trailing_12m_dps",
]
