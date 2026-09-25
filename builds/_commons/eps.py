"""Shared vectorized EPS computation (close / pe).

The single definition of the repo's EPS semantics — every PE write path
(etf split tables, stock PE-only passes) computes eps = close / pe
rounded to 6 in pandas with this helper. NaN on miss; the NaN→None
conversion happens at the row-emission boundary (records_from_frame /
sanitize_for_db_insert), never here.
"""
from __future__ import annotations

import pandas as pd


def compute_eps_vec(close: pd.Series, pe: pd.Series) -> pd.Series:
    """Float-native EPS with NaN on miss — nan_to_none in
    records_from_frame converts NaN→None at emission (no object column
    on the frame)."""
    mask = close.notna() & pe.notna() & (pe > 0)
    return (close.astype(float) / pe.astype(float)).where(mask).round(6)


__all__ = ["compute_eps_vec"]
