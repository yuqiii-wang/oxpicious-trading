"""Pair-grain pivots: benchmarks + ETF amounts to wide/long format.

Pre-computed ONCE per call; reused by all subjects (avoids per-subject
stack+reset_index). Ported from analyze.sec_alloc_perf_attribution.compute._pivots.
"""
from __future__ import annotations

import pandas as pd


def prepare_pivots(
    index_closes: pd.DataFrame,
    etf_amount_by_index: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Returns (benchmark_close_wide, etf_amount_wide, etf_amount_long).

    Both wide pivots are date-indexed and sorted; etf_amount_long is the
    long (date, index_code, etf_amount) frame. Dates stay datetime64 —
    object-date columns poison every downstream cudf merge; the asyncpg
    boundary conversion happens in the sanitize step.
    """
    benchmark_close_wide = (
        index_closes.pivot(index="date", columns="benchmark_code",
                           values="benchmark_close")
        .sort_index()
    )

    if not etf_amount_by_index.empty:
        etf_amount_wide = (
            etf_amount_by_index.pivot(index="date", columns="index_code",
                                      values="etf_amount")
            .sort_index()
        )
        etf_amount_long = etf_amount_wide.stack().reset_index()
        etf_amount_long.columns = ["date", "index_code", "etf_amount"]
    else:
        etf_amount_wide = pd.DataFrame()
        etf_amount_long = pd.DataFrame(
            columns=["date", "index_code", "etf_amount"]
        )

    return benchmark_close_wide, etf_amount_wide, etf_amount_long
