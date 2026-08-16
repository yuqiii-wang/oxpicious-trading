"""Shared helpers for per-algo DB fetch.

Each algo's ``fetch_signal_data`` owns its SQL (which analysis tables to
JOIN + which columns to select). This module factors out the parts that are
identical across all algos: the per-sec_type basic_stats / tech_stats table
names and the DataFrame post-processing (date normalization + Decimal->float
coercion).

Moved from the former ``strategy.factors_and_algos._fetch_util`` (collapsed
into the ``_algo`` package so the shared infra lives in one place).
"""
from __future__ import annotations

import pandas as pd

from strategy._common.constants import (  # noqa: F401
    SEC_TYPE_BASIC_STATS_TABLE,
    SEC_TYPE_TECH_STATS_TABLE,
)


def basic_stats_table(sec_type: str) -> str:
    """Return the stats.<sec_type>_basic_stats table for OHLC fill prices."""
    return SEC_TYPE_BASIC_STATS_TABLE[sec_type]


def tech_stats_table(sec_type: str) -> str:
    """Return the stats.<sec_type>_tech_stats table for precomputed MAs/EMAs."""
    return SEC_TYPE_TECH_STATS_TABLE[sec_type]


def rows_to_df(rows, numeric_cols) -> pd.DataFrame:
    """Build a sorted-by-(code,date) DataFrame from asyncpg rows.

    - ``date`` normalized to python date for clean serialization.
    - every column in ``numeric_cols`` coerced to float (Decimal/NUMERIC →
      float for pandas arithmetic). Missing columns are skipped.
    """
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    for c in numeric_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


__all__ = ["basic_stats_table", "tech_stats_table", "rows_to_df"]
