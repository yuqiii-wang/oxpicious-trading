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
    "code_price_with_benchmark_offset",
    "code_price_with_benchmark_offset_by_weighted_amt",
    "corr_20d", "corr_60d", "corr_255d",
]

# String/non-numeric columns that must NOT be sanitized as numeric.
_NON_NUMERIC_COLS: set[str] = {"code", "date", "sec_type", "benchmark_code"}

CORR_COLS: list[str] = [f"corr_{N}d" for N in CORR_WINDOWS]

# Column materialized by the post-COPY SQL pass (runner step 3b,
# _pair_offsets.PAIR_WEIGHTED_UPDATE_SQL_* — the amount weighting needs
# the stock-turnover fan-out, impossible frame-side). Absent from the
# insert-mode frame: NaN-filled here so COPY writes NULL, then the
# UPDATE pass fills it.
_SQL_PASS_COLS: list[str] = [
    "code_price_with_benchmark_offset_by_weighted_amt"
]

# Corr-only update payload: the 4 PK columns + the 3 corr columns. The
# upsert's DO UPDATE clause touches ONLY corr columns, so base columns
# (weights) written by the main run are never clobbered.
CORR_OUT_COLS: list[str] = [
    "code", "date", "sec_type", "benchmark_code", *CORR_COLS,
]


def select_and_sanitize(merged: pd.DataFrame) -> list[dict]:
    """Select output columns and sanitize for the bulk COPY.

    Corr columns may be absent (insert mode) and the weighted-offset
    column is SQL-pass materialized — NaN-filled so the frame always
    carries the full OUT_COLS shape.
    """
    cols = safe_columns(merged)
    for c in [*CORR_COLS, *_SQL_PASS_COLS]:
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
