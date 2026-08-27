"""GPU-safe vectorized parsing shared by etf/index/options/futures builds.

Ported from builds/stock/_helpers/helpers.py (identical semantics) so the
other build modules stop calling scalar ``parse_num``/``parse_date`` via
``Series.apply`` — under cudf.pandas every apply() attempt is a Numba JIT
compilation of the host ufunc; when it fails (it does, per element), each
element becomes one slow-path fallback (~9,206 lines/run in
builds.index.baseline before this fix).

Fast paths:
* ``safe_to_numeric``  — direct ``astype(float)`` (downloads conversion
  writes plain normalized floats).
* ``safe_to_datetime`` — ONE ``pd.to_datetime(format="%Y-%m-%d")`` (source
  CSVs are canonical YYYY-MM-DD).

Legacy multi-format fallbacks run only on exception.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from builds._commons.column_maps import DATE_INVALID_PATTERNS, DATE_VALID_RE

# Date formats handled by safe_to_datetime's LEGACY fallback branch, most
# common first. Source CSVs are all zero-padded YYYY-MM-DD (guaranteed by
# the downloads conversion), so the FAST path never touches any of these.
_DATE_FORMATS: tuple[tuple[str, str], ...] = (
    (r"^\d{4}-\d{1,2}-\d{1,2}$", "%Y-%m-%d"),
    (r"^\d{8}$",                  "%Y%m%d"),
    (r"^\d{4}/\d{1,2}/\d{1,2}$",  "%Y/%m/%d"),
    (r"^\d{4}\.\d{1,2}\.\d{1,2}$", "%Y.%m.%d"),
)


def safe_to_datetime(series: pd.Series) -> pd.Series:
    """Convert a series to datetime64 — clean-data fast path first."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    try:
        return pd.to_datetime(series, format="%Y-%m-%d")
    except (ValueError, TypeError):
        pass
    # --- legacy fallback: defensive multi-format parsing ------------------
    s = series.astype(str)
    s = s.str.strip()
    for pat in DATE_INVALID_PATTERNS:
        s = s.replace(pat, pd.NA)
    s = s.replace("", pd.NA)
    s = s.replace("-", pd.NA)
    valid_mask = s.str.match(DATE_VALID_RE, na=False)
    result = pd.Series(pd.NaT, index=series.index)
    if bool(valid_mask.any()):
        valid = s[valid_mask]
        parsed = pd.Series(pd.NaT, index=valid.index)
        remaining = pd.Series(True, index=valid.index)
        for pat, fmt in _DATE_FORMATS:
            m = remaining & valid.str.match(pat, na=False)
            if bool(m.any()):
                parsed[m] = pd.to_datetime(valid[m], format=fmt)
                remaining = remaining & ~m
        if bool(remaining.any()):
            parsed[remaining] = pd.to_datetime(valid[remaining], format="mixed")
        result[valid_mask] = parsed
    return result


def safe_to_numeric(series: pd.Series) -> pd.Series:
    """Convert a series to float64 — clean-data fast path first."""
    if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
        return series.astype(float)
    try:
        return series.astype(float)
    except (ValueError, TypeError):
        pass
    # --- legacy fallback: defensive numeric cleaning -----------------------
    s = series.astype(str)
    s = s.str.strip()
    for pat in DATE_INVALID_PATTERNS:
        s = s.replace(pat, pd.NA)
    s = s.replace("", pd.NA)
    s = s.replace("-", pd.NA)
    s = s.str.replace(",", "", regex=False)
    valid_mask = s.str.match(r"^-?\d+\.?\d*([eE][+-]?\d+)?$", na=False)
    result = pd.Series(np.nan, index=series.index)
    if bool(valid_mask.any()):
        result[valid_mask] = s[valid_mask].astype(float)
    return result


__all__ = ["safe_to_datetime", "safe_to_numeric"]
