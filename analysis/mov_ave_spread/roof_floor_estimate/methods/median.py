"""median — the cross-method consensus row.

Per (date, window): the NaN-skipping MEDIAN of the five independent
methods' roof values (and of their floor values). The median is the
robust standard for ensembling level estimates — one stray method (a
donchian wick spike, a degenerate trendline extrapolation) cannot drag
the consensus.

Compute layout: ONE host-numpy boundary per (side, window) — the five
method columns are unwrapped via host_array once, np.nanmedian folds
them on the host (all-NaN rows yield NaN), and the result is written
back as a host array. cuDF has no row-wise (axis=1) median across
columns, and the mid-flow hybrid forms are banned — this is the
sanctioned per-group-boundary host core applied to a column-fold.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from _common.df_utils import host_array
from analysis.mov_ave_spread.roof_floor_estimate.methods import (
    BASE_ESTIMATE_METHODS,
)


def attach_median_estimates(df: pd.DataFrame, windows) -> None:
    """Assign ``roof_median_{w}d`` / ``floor_median_{w}d`` per window.

    Requires the five base methods' columns to be present.
    """
    for w in windows:
        for side in ("roof", "floor"):
            cols = [f"{side}_{m}_{w}d" for m in BASE_ESTIMATE_METHODS]
            stacked = np.vstack([
                host_array(df[c].to_numpy()) for c in cols
            ])
            with warnings.catch_warnings():
                # all-NaN rows (no method has a level) legitimately
                # yield NaN — nanmedian's RuntimeWarning is noise here.
                warnings.simplefilter("ignore", RuntimeWarning)
                med = np.nanmedian(stacked, axis=0)
            df[f"{side}_median_{w}d"] = med
