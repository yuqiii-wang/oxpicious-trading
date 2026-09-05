"""Pair-grain output sanitization + corr-only payload.

DB emission is bulk-COPY friendly: plain columns, NaN/inf → None, round(4).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils.sanitize import safe_columns, sanitize_for_db_insert
from builds.cross_stats.config import CORR_WINDOWS

OUT_COLS: list[str] = [
    "code", "date", "sec_type", "benchmark_code",
    "code_sec_shared_weight", "benchmark_sec_shared_weight",
    "benchmark_etf_trading_amount", "code_etf_trading_amount",
    "etf_trading_amount_ratio_benchmark_to_code",
    "etf_trading_amount_ratio_benchmark_to_code_ma5",
    "corr_20d", "corr_60d", "corr_255d",
]

# String/non-numeric columns that must NOT be sanitized as numeric.
_NON_NUMERIC_COLS: set[str] = {"code", "date", "sec_type", "benchmark_code"}

CORR_COLS: list[str] = [f"corr_{N}d" for N in CORR_WINDOWS]

# Corr-only update payload: the 4 PK columns + the 3 corr columns. The
# upsert's DO UPDATE clause touches ONLY corr columns, so base columns
# (weights, ETF amounts, ratio) written by the main run are never clobbered.
CORR_OUT_COLS: list[str] = [
    "code", "date", "sec_type", "benchmark_code", *CORR_COLS,
]


def select_and_sanitize(merged: pd.DataFrame) -> list[dict]:
    """Select output columns and sanitize for the bulk COPY.

    Corr columns may be absent (insert mode) — NaN-filled so the frame
    always carries the full OUT_COLS shape.
    """
    cols = safe_columns(merged)
    for c in CORR_COLS:
        if c not in cols:
            merged[c] = np.nan
    out = merged[OUT_COLS].copy()
    if out.empty:
        return []
    numeric_cols = [c for c in OUT_COLS if c not in _NON_NUMERIC_COLS]
    return sanitize_for_db_insert(out, numeric_cols=numeric_cols, round_to=4)


def select_and_sanitize_corr(corr_bulk: pd.DataFrame,
                             sec_type: str) -> list[dict]:
    """Corr-only sanitize: stamp sec_type and emit the 4-PK + 3-corr
    upsert payload."""
    out = corr_bulk.copy()
    out["sec_type"] = sec_type
    out = out[CORR_OUT_COLS]
    if out.empty:
        return []
    return sanitize_for_db_insert(out, numeric_cols=CORR_COLS, round_to=4)
