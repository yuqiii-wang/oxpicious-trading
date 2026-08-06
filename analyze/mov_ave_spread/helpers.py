"""Pure helpers for analyze.mov_ave_spread.

No DB / IO dependencies — safe to unit-test in isolation.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from analyze._common._cuDF import should_use_gpu
from analyze._common.rolling import grouped_rolling_agg
from analyze.mov_ave_spread.config import MA_WINDOWS, NUMERIC_MAX_ABS


def null_if_overflow(series: pd.Series) -> pd.Series:
    """Return a copy of ``series`` with values that would overflow a
    NUMERIC(10,6) column replaced by NaN (later converted to None).

    NUMERIC(10,6) holds values with absolute value < 10^4 after rounding to
    6 decimal places. This helper nulls any value whose rounded absolute
    value >= NUMERIC_MAX_ABS, mirroring PostgreSQL's overflow check so the
    bulk upsert never fails. NaN/inf are also nulled.

    This is the safety net for:
      - slope/curvature columns (raw differences) — high-priced ETFs/indices
        can produce single-day MA changes exceeding 10000 at corporate-action
        or source-data-unit boundaries.
      - gap columns (ratios) — catches any near-zero-denominator ratio that
        slips through gap_col's zero/near-zero check.
    """
    s = pd.to_numeric(series, errors="coerce")
    mask = s.isna() | ~np.isfinite(s) | (s.abs().round(6) >= NUMERIC_MAX_ABS)
    return s.where(~mask)


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


def compute_slopes_curvatures(df: pd.DataFrame) -> pd.DataFrame:
    """Add 1st-derivative (slope) and 2nd-derivative (curvature) columns for
    price and each MA window, computed per (sec_type, code) ordered by date.

    slope[t]      = value[t] - value[t-1]   (NULL on first date of each code)
    curvature[t]  = slope[t] - slope[t-1]   (NULL on first two dates of each code)

    Adds columns price_slope / price_curvature (from ``price``) and
    ma{W}_slope / ma{W}_curvature for W in MA_WINDOWS (from ``ma{W}``).

    GPU acceleration: when the cuDF router determines the GPU is
    worthwhile for this row count (groupby_diff op_type), the entire
    diff() sequence runs on a cuDF DataFrame and is brought back to
    pandas once at the end. This amortizes the H2D/D2H transfer over
    12 diff() operations (6 slopes + 6 curvatures).
    """
    df = df.sort_values(["sec_type", "code", "date"]).reset_index(drop=True)
    grp_keys = ["sec_type", "code"]

    if should_use_gpu(df, op_type="groupby_diff"):
        import cudf  # type: ignore[import-untyped]
        # cuDF can't handle object-dtype ``date`` columns (python date
        # objects). The date column is only used for sorting above (already
        # done), so drop it for the GPU pass and restore it after.
        date_col = df["date"].copy()
        work = df.drop(columns=["date"])
        gdf = cudf.from_pandas(work)
        # Price 1st + 2nd derivative.
        gdf["price_slope"] = gdf.groupby(grp_keys, sort=False)["price"].diff()
        gdf["price_curvature"] = gdf.groupby(grp_keys, sort=False)["price_slope"].diff()
        for w in MA_WINDOWS:
            ma_col = f"ma{w}"
            slope_col = f"ma{w}_slope"
            curv_col = f"ma{w}_curvature"
            gdf[slope_col] = gdf.groupby(grp_keys, sort=False)[ma_col].diff()
            gdf[curv_col] = gdf.groupby(grp_keys, sort=False)[slope_col].diff()
        result = gdf.to_pandas()
        result["date"] = date_col.values
        return result

    # CPU path (pandas Cython).
    # Price 1st + 2nd derivative.
    df["price_slope"] = df.groupby(grp_keys, sort=False)["price"].diff()
    df["price_curvature"] = df.groupby(grp_keys, sort=False)["price_slope"].diff()
    for w in MA_WINDOWS:
        ma_col = f"ma{w}"
        slope_col = f"ma{w}_slope"
        curv_col = f"ma{w}_curvature"
        df[slope_col] = df.groupby(grp_keys, sort=False)[ma_col].diff()
        df[curv_col] = df.groupby(grp_keys, sort=False)[slope_col].diff()
    return df


def compute_rolling_stds(df: pd.DataFrame) -> pd.DataFrame:
    """Add 5 rolling population σ columns (Bollinger band widths) for price,
    computed per (sec_type, code) ordered by date.

    std_{W}days[t] = population standard deviation of price over the last
    W rows (ddof=0, the Bollinger convention). NULL until W consecutive
    rows are available (pandas .rolling(W, min_periods=W).std(ddof=0)
    returns NaN for any window with fewer than W non-NaN values).

    Why population (ddof=0) instead of sample (ddof=1)?
      - Standard Bollinger Bands use population σ. Most charting platforms
        (TradingView, Bloomberg) follow this convention.
      - For N=5 the difference between ddof=0 and ddof=1 is meaningful
        (σ_sample = σ_pop × sqrt(5/4) ≈ 1.118 × σ_pop), so the choice
        matters for the band width.

    σ is in price units (not price²), so it fits NUMERIC(10,6) without
    overflow for any realistic ETF / index / stock price (σ << price
    because σ ≤ max(|price - mean|) ≤ price range).

    Adds columns: std_5days, std_20days, std_60days, std_120days, std_255days.

    Implementation: uses the shared ``grouped_rolling_agg`` helper
    (Cython-compiled ``groupby().rolling().std()``) instead of
    ``transform(lambda s: ...)``. The lambda wrapper forced pandas to
    call back into Python once per group (~5000+ groups × 5 windows =
    25K+ Python callbacks on the 8M-row DataFrame), which dominated
    runtime. The shared helper keeps the entire rolling-std computation
    inside Cython and is cuDF-compatible.
    """
    df = df.sort_values(["sec_type", "code", "date"]).reset_index(drop=True)
    grp_keys = ["sec_type", "code"]
    for w in MA_WINDOWS:
        col = f"std_{w}days"
        # min_periods=W ensures NULL until the window is fully populated —
        # matches the SQL COMMENT and avoids misleading early-window σ
        # values that would be computed from fewer than W observations.
        df[col] = grouped_rolling_agg(
            df, grp_keys, "price", window=w,
            min_periods=w, agg="std", ddof=0,
        )
    return df


def gap_col(df: pd.DataFrame, num_col: str, den_col: str) -> pd.Series:
    """Vectorized (num - den) / den with NULL semantics matching safe_ratio.

    Returns None where num/den is NaN, where the denominator is zero or
    denormalized (|den| < 1e-12, which would produce a huge or non-finite
    ratio), or where the result is non-finite. The null_if_overflow pass
    in build_detail_rows is the final safety net for any ratio that still
    exceeds the NUMERIC(10,6) range.
    """
    num = df[num_col]
    den = df[den_col]
    out = (num - den) / den
    mask = (num.isna() | den.isna()
            | (den.abs() < 1e-12)
            | ~np.isfinite(out))
    return out.where(~mask, other=None)
