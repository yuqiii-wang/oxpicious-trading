"""Forward changes (analyze.analysis_forecasts.fetch.forward).

Derives the per-code forward-looking columns the change matrices
scatter: next_change_{n}d (the endpoint n-day fractional price change)
plus its period-end close next_close_{n} (price[t+n] — the
forecast_results.ave_close input). All ops are vectorized cudf.pandas
DataFrame ops over the long frame (grouped_shift — cuDF-accelerated; no
per-code Python loops).

(The swing-aware path extremes path_high_{n}d / path_low_{n}d — the
reversal-event inputs — were REMOVED 2026-09-25 with
forecast_results.reverse_prob; see config/horizons.py.)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import grouped_shift

from analyze.analysis_forecasts.config import FORWARD_HORIZONS


def add_forward_changes(df: pd.DataFrame) -> pd.DataFrame:
    """Add next_change_{n}d columns for n in FORWARD_HORIZONS.

    next_change_{n}d = (price[t+n] - price[t]) / price[t], per code on its
    OWN trading-day sequence (grouped_shift — cuDF-accelerated; calendar
    gaps are not rows). NULL when the forward price is missing, the base
    price is ~0, or the ratio is non-finite.

    The shifted forward close itself is KEPT as next_close_{n}
    (= price[t+n]; NULL exactly where the shift falls off the code's
    tail) — the PERIOD-END close the forecast_results.ave_close average
    gathers over each bucket's valid trigger days.
    """
    for n in FORWARD_HORIZONS:
        col = f"next_close_{n}"
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
    return df
