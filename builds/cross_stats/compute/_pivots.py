"""Pair-grain benchmark-close pivot (wide format).

Pre-computed ONCE per call; reused by all subjects (avoids per-subject
stack+reset_index). Ported from analyze.sec_alloc_perf_attribution.compute._pivots.
(The ETF-amount wide/long pivots were consolidated away 2026-09-07
together with the cross_stats ETF amount columns.)
"""
from __future__ import annotations

import pandas as pd


def prepare_pivots(
    index_closes: pd.DataFrame,
) -> pd.DataFrame:
    """Returns the benchmark_close_wide pivot (date-indexed, sorted).

    Dates stay datetime64 — object-date columns poison every downstream
    cudf merge; the asyncpg boundary conversion happens in the sanitize
    step.
    """
    return (
        index_closes.pivot(index="date", columns="benchmark_code",
                           values="benchmark_close")
        .sort_index()
    )
