"""PE compute logic for the ``pe`` submodule of analyze.pe_and_dividends.

Covers:
  - pe: raw PE with the invalid-value rule applied (PE <= 0 / NULL → NaN)
"""
from __future__ import annotations

import pandas as pd


# ---------------------------------------------------------------------------
#  pe — raw PE per code (index/etf/stock), invalid values masked
# ---------------------------------------------------------------------------
def clean_pe(df: pd.DataFrame) -> pd.Series:
    """Return the RAW PE series with invalid values masked to NaN.

    INVALID-VALUE RULE: a PE of <= 0 (zero, negative, or NULL) is treated
    as missing data — 0 means "no earnings reported" and negative PE is a
    sign-flip that breaks the linear scale. PE <= 0 / NULL → NaN, so the
    stored column is strictly positive or NULL ("entry val NULL for
    invalid data").
    Mirrors the rz_balance 0/NULL→NaN rule in analyze.margins.compute
    ("skip the date as a holiday; denominator does not count for null").

    Args:
        df: DataFrame with a ``pe`` column (index / etf / stock PE source
            — for index from stats.index_valuation.pe, for etf / stock
            pre-computed by builds.etf / builds.stock).

    Returns:
        Series aligned to df's index with cleaned pe values (NaN where
        the source PE is invalid).
    """
    if df.empty:
        return pd.Series(dtype=float)
    # Clean: PE <= 0 or NULL → NaN. A 0 / negative PE is meaningless
    # (no earnings / sign-flip). Treated as missing.
    # where(bool Series) — where(callable) has no cuDF fast path.
    pe_num = pd.to_numeric(df["pe"], errors="coerce")
    return pe_num.where(pe_num > 0)
