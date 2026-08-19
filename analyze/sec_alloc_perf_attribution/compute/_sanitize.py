"""Step 9: shared sanitize_for_db_insert + output column selection."""
from __future__ import annotations

import pandas as pd

from analyze._common import sanitize_for_db_insert


# ---------------------------------------------------------------------------
#  Output column definitions + sanitization
# ---------------------------------------------------------------------------
OUT_COLS: list[str] = [
    "code", "date", "sec_type", "benchmark_code",
    "code_sec_shared_weight", "benchmark_sec_shared_weight",
    "benchmark_etf_trading_amount", "code_etf_trading_amount",
    "etf_trading_amount_ratio_benchmark_to_code_ma5",
    "corr_5d", "corr_20d", "corr_60d", "corr_255d",
]

# String/non-numeric columns that must NOT be sanitized as numeric.
_NON_NUMERIC_COLS: set[str] = {"code", "date", "sec_type", "benchmark_code"}


def select_and_sanitize(merged: pd.DataFrame) -> list[dict]:
    """Select output columns and sanitize for asyncpg upsert."""
    out = merged[OUT_COLS].copy()
    if out.empty:
        return []
    numeric_cols = [c for c in OUT_COLS if c not in _NON_NUMERIC_COLS]
    return sanitize_for_db_insert(out, numeric_cols=numeric_cols, round_to=4)
