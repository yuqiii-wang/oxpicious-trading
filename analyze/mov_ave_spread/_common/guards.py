"""Numeric guards + gap-ratio helpers shared by every mov_ave_spread step.

No DB / IO dependencies - safe to unit-test in isolation.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import host_array
from analyze.mov_ave_spread.config import NUMERIC_MAX_ABS


def null_if_overflow(
    series: pd.Series,
    *,
    max_abs: float = NUMERIC_MAX_ABS,
    scale: int = 6,
) -> np.ndarray:
    """Return ``series`` values with overflow-risky entries nulled to NaN.

    Default: NUMERIC(10,6) — values with absolute value < 10^4 after
    rounding to 6 decimal places. Override ``max_abs``/``scale`` for
    wider columns (e.g. NUMERIC(16,4) → max_abs=10^12, scale=4).

    This helper nulls any value whose rounded absolute value >= max_abs,
    mirroring PostgreSQL's overflow check so the bulk upsert never fails.
    NaN/inf are also nulled.

    This is the safety net for:
      - slope/curvature columns (raw differences) — high-priced ETFs/indices
        can produce single-day MA changes exceeding 10000 at corporate-action
        or source-data-unit boundaries.
      - gap columns (ratios) — catches any near-zero-denominator ratio that
        slips through gap_col's zero/near-zero check.
      - trading_amt_ma* columns (NUMERIC(24,4)) — pass max_abs=NUMERIC_WIDE_
        MAX_ABS, scale=4. Daily trading_amount for a high-turnover index can
        reach ~10^13 yuan; NUMERIC(10,6) would overflow.

    Host-pure (B-A3): data leaves the proxy Series ONCE via ``to_numpy``
    + :func:`_common.df_utils.host_array`; the mask (NaN / inf /
    |rounded| >= max_abs) is computed in raw host numpy. Returns a plain
    float64 ndarray — assign directly with ``df[col] = result`` (the
    former proxied to_numeric/isna/abs/round/where chain cost ~5 cudf
    fallbacks per column).
    """
    arr = _to_float64_host(series)
    bad = ~np.isfinite(arr) | (np.abs(np.round(arr, scale)) >= max_abs)
    out = arr.copy()
    out[bad] = np.nan
    return out


def _to_float64_host(series: pd.Series) -> np.ndarray:
    """Series -> RAW host float64 ndarray, coercing non-numeric junk.

    Fast path: float columns skip ``pd.to_numeric`` entirely (one fewer
    proxied dispatch per column — the columns reaching the overflow
    guards are computed floats in practice). ``na_value=np.nan`` keeps
    nullable/missing columns on the cudf fast path — a plain
    ``to_numpy(dtype=np.float64)`` raises ValueError on missing values
    and falls back per column (~630 fallbacks per stock-scale run).
    Object/other dtypes take the to_numeric-coerce path.
    """
    try:
        return host_array(
            series.to_numpy(dtype=np.float64, na_value=np.nan)
        )
    except (TypeError, ValueError):
        return host_array(
            pd.to_numeric(series, errors="coerce").to_numpy(
                dtype=np.float64, na_value=np.nan
            )
        )


def null_if_overflow_counted(
    series: pd.Series,
    *,
    max_abs: float = NUMERIC_MAX_ABS,
    scale: int = 6,
) -> tuple[np.ndarray, int]:
    """:func:`null_if_overflow` + the number of values newly nulled.

    The count is derived from the same host mask (positions that were
    not NaN before and are NaN after — inf / overflow / coercion) at
    zero extra proxy dispatch, replacing the former per-column
    ``isna().sum()`` before/after proxied pairs.
    """
    arr = _to_float64_host(series)
    was_nan = np.isnan(arr)
    bad = ~np.isfinite(arr) | (np.abs(np.round(arr, scale)) >= max_abs)
    out = arr.copy()
    out[bad] = np.nan
    return out, int((bad & ~was_nan).sum())


def safe_ratio(num, den):
    """(num - den) / den — returns None for NaN/None/non-positive denominator.

    Mirrors the SQL NULL-on-bad-input semantics so that detail rows always
    match what a pure-SQL computation would produce. Also nulls results
    whose absolute value would overflow NUMERIC(10,6) (|result| >= 10000),
    which can occur when ``den`` is denormalized (near-zero but non-zero).
    """
    if num is None or den is None:
        return None
    try:
        n = float(num)
        d = float(den)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(n) or not np.isfinite(d) or d == 0:
        return None
    r = (n - d) / d
    if not np.isfinite(r) or abs(r) >= NUMERIC_MAX_ABS:
        return None
    return r


def gap_col(df: pd.DataFrame, num_col: str, den_col: str) -> pd.Series:
    """Vectorized (num - den) / den with NULL semantics matching safe_ratio.

    Returns None where num/den is NaN, where the denominator is zero or
    denormalized (|den| < 1e-12, which would produce a huge or non-finite
    ratio), or where the result is non-finite. The null_if_overflow pass
    in the step's sanitize/build boundary is the final safety net for any
    ratio that still exceeds the NUMERIC(10,6) range.
    """
    num = df[num_col]
    den = df[den_col]
    out = (num - den) / den
    mask = (num.isna() | den.isna()
            | (den.abs() < 1e-12)
            | ~np.isfinite(out))
    return out.where(~mask, other=None)
