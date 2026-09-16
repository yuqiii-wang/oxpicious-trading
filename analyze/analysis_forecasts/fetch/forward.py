"""Forward changes and path extremes (analyze.analysis_forecasts
.fetch.forward).

Derives the per-code forward-looking columns the change matrices
scatter: next_change_{n}d (the endpoint n-day fractional price change)
and path_high_{n}d / path_low_{n}d (the n-day forward window's signed
close extremes — the swing-aware reversal event and max_low_change_ratio
inputs). All ops are vectorized cudf.pandas DataFrame ops over the long
frame (grouped_shift — cuDF-accelerated; no per-code Python loops).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import grouped_shift

from analyze.analysis_forecasts.config import FORWARD_HORIZONS, MM_HORIZONS


def add_forward_changes(df: pd.DataFrame) -> pd.DataFrame:
    """Add next_change_{n}d columns for n in FORWARD_HORIZONS.

    next_change_{n}d = (price[t+n] - price[t]) / price[t], per code on its
    OWN trading-day sequence (grouped_shift — cuDF-accelerated; calendar
    gaps are not rows). NULL when the forward price is missing, the base
    price is ~0, or the ratio is non-finite.
    """
    for n in FORWARD_HORIZONS:
        col = f"_next_price_{n}"
        grouped_shift(df, ["code"], "price", out_names=col,
                      periods=-n, sort=False)
        prev = df["price"]
        nxt = df[col]
        out = (nxt - prev) / prev
        # Pure-boolean mask (nullable <NA> comparisons poison the cudf
        # frame — the margin.add_margin_ratio_features note): the
        # shifted prev price is filled before its compare; nxt.isna()
        # already covers the missing-forward-price rows.
        mask = nxt.isna() | (prev.fillna(0.0).abs() < 1e-12) \
            | ~np.isfinite(out)
        df[f"next_change_{n}d"] = out.where(~mask)
        df = df.drop(columns=[col])
    return df


def _forward_extreme(df: pd.DataFrame, n: int, *, high: bool) -> pd.Series:
    """Per-code extreme of ``price`` over the NEXT n rows (t, t+n] —
    max when ``high`` else min, NaN-skipping, NaN where the code has no
    forward rows left.

    Doubling levels F_1, F_2, F_4, ... (F_{2m} = F_{m} ⊕ shift(F_{m},
    -m), ⊕ = NaN-skipping max/min) with the exact window assembled from
    n's binary decomposition (each bit: one shift of the level column +
    one combine). Rows covering fewer than n forward rows (the code's
    tail) may hold a partial-window extreme — the caller masks them out
    via the endpoint-change validity, which is exactly "n forward rows
    exist".
    """
    neutral = -np.inf if high else np.inf
    combine = np.maximum if high else np.minimum

    def shift_col(col: str, periods: int) -> pd.Series:
        name = f"_pe_s{periods}"
        grouped_shift(df, ["code"], col, out_names=name, periods=periods,
                      sort=False)
        s = df[name].fillna(neutral)
        df.drop(columns=[name], inplace=True)
        return s

    # Doubling levels: F_1 = the next row, F_{2m} = F_m ⊕ F_m(t+m).
    # Levels live as (±inf-filled) df columns so they can be shifted;
    # shifting a filled column is safe — a neutral tail value shifted
    # into an earlier row is exactly the "block has no rows" semantics.
    levels: dict[int, pd.Series] = {}
    grouped_shift(df, ["code"], "price", out_names="_pe_lvl1", periods=-1,
                  sort=False)
    levels[1] = df["_pe_lvl1"].fillna(neutral)
    step = 1
    while step * 2 <= n:
        step *= 2
        levels[step] = combine(levels[step // 2], shift_col(
            f"_pe_lvl{step // 2}", -(step // 2)))
        df[f"_pe_lvl{step}"] = levels[step]

    # Assemble exactly n rows from the binary decomposition of n.
    res: pd.Series | None = None
    covered = 0
    for level in sorted(levels.keys(), reverse=True):
        if covered + level <= n:
            block = levels[level] if covered == 0 else shift_col(
                f"_pe_lvl{level}", -covered)
            res = block if res is None else combine(res, block)
            covered += level
    assert res is not None and covered == n
    df.drop(columns=[c for c in df.columns if c.startswith("_pe_lvl")],
            inplace=True)
    return res


def add_path_extremes(df: pd.DataFrame) -> pd.DataFrame:
    """Add path_high_{n}d / path_low_{n}d columns for n in MM_HORIZONS.

    The SIGNED extrema of the close within the n-row forward window
    (t, t+n], relative to the signal-day close: path_high_{n}d =
    max(price[t+1..t+n]) / price[t] - 1 (the window's highest close),
    path_low_{n}d = min(...) (the lowest close) — the within-period
    SWING that the endpoint next_change_{n}d (one point of that path)
    does not capture. Consumed by the swing-aware reversal event and
    the max_low_change_ratio swing ratio (wide.aggregate_horizons_sparse).

    Per code on its OWN trading-day sequence, so the window is exactly
    n rows whenever next_change_{n}d is finite — these columns are NULL
    on exactly the rows the endpoint change is (the short forward tail;
    the base SQL already drops price-less rows mid-sequence).
    """
    for n in MM_HORIZONS:
        fin = df[f"next_change_{n}d"].notna()
        for name, high in (("path_high", True), ("path_low", False)):
            ext = _forward_extreme(df, n, high=high)
            df[f"{name}_{n}d"] = ((ext / df["price"]) - 1.0).where(fin)
    return df
