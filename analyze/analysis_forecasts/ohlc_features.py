"""OHLC anchor-day fallback features
(analyze.analysis_forecasts.ohlc_features).

The derived-population modules' pattern columns are AUTHORITATIVE in
analysis.mov_ave_spreads_detail_ohlc_gaps (gap_open computed behind the
estimated-row exclusion, the wick top-decile flags and the classified
``pattern`` stored there since 2026-09-27 — the forecasts fetch LEFT
JOINs them in as ``src_gap_open`` / ``src_pattern``). This module now
derives only the FALLBACK anatomy for rows the gaps store does not have
(the build lagging behind): ``attach_day_anatomy`` computes gap_open and
bearish_body from the fetched day_open/price so the OHLC-boost
classifier degrades to the gap×candle quadrants instead of breaking.

The wick block (upper/lower wick + trailing top-decile flags) is NOT
derived here anymore — those live solely in the gaps store (the
trailing-quantile computation was the pipeline's only CPU-fallback
pass; it belongs to the owning mov_ave_spread suite).
"""
from __future__ import annotations

import pandas as pd

from _common.df_utils import grouped_shift


def attach_day_anatomy(df: pd.DataFrame) -> pd.DataFrame:
    """Attach the fallback anchor-day anatomy columns. NULL on the
    code's first day / missing open / degenerate prev close (per code
    on its own trading-day sequence — the forward.py grouped_shift
    idiom). The caller coalesces gap_open with the gaps store's
    authoritative ``src_gap_open``."""
    grouped_shift(df, ["code"], "price", out_names="_prev_close",
                  periods=1, sort=False)
    gap = df["day_open"] / df["_prev_close"] - 1.0
    bad = df["day_open"].isna() | df["_prev_close"].isna() \
        | (df["_prev_close"].abs() < 1e-12) | gap.isna()
    df["gap_open"] = gap.where(~bad)
    # the bearish-body feature: the day CLOSED below its OPEN (a red
    # candle) — 1.0/0.0, NULL where the open itself is unknown
    red = (df["price"] < df["day_open"]).fillna(False)
    df["bearish_body"] = red.astype("float64").where(df["day_open"].notna())
    return df.drop(columns=["_prev_close"])


__all__ = ["attach_day_anatomy"]
